#!/usr/bin/env python3
"""Run safe, isolated Bug Validator build probes across FM-Agent languages.

This runner never accepts an LLM-provided shell command.  It detects a bounded
adapter from the project snapshot, records that profile, and executes only the
adapter's fixed command list under a probe-owned attempt directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from _common import project, state
from fm_agent_core.languages import PROFILES


# Derived from the central registry; do not add extension maps in this runner.
LANGUAGE_EXTENSIONS = {
    profile.key: set(profile.extensions) for profile in PROFILES
    if profile.support_level != "external-plugin"
}
IGNORED_DIRS = {".git", ".codegraph", "fm_agent", "fm_agent_skill", "node_modules", "build", "target", "dist", "out", "__pycache__"}
ADAPTERS = {"auto", "cmake", "cargo", "go", "python", "java", "javascript", "typescript", "cuda", "arkts", "none"}


def safe_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return result or "probe"


def is_test(path: Path) -> bool:
    return state.is_test_source_path(path.as_posix())


def source_files(target: Path) -> list[Path]:
    result = []
    for item in target.rglob("*"):
        if not item.is_file() or any(part in IGNORED_DIRS for part in item.relative_to(target).parts):
            continue
        rel = item.relative_to(target)
        if item.suffix.lower() in {extension for values in LANGUAGE_EXTENSIONS.values() for extension in values} and not is_test(rel):
            result.append(item)
    return sorted(result)


def languages(target: Path) -> list[str]:
    files = source_files(target)
    return sorted(language for language, extensions in LANGUAGE_EXTENSIONS.items() if any(path.suffix.lower() in extensions for path in files))


def auto_adapter(target: Path, language_keys: list[str]) -> tuple[str, str | None]:
    if (target / "CMakeLists.txt").is_file(): return "cmake", None
    if (target / "Cargo.toml").is_file(): return "cargo", None
    if (target / "go.mod").is_file(): return "go", None
    if "typescript" in language_keys and (target / "tsconfig.json").is_file(): return "typescript", None
    for name, adapter in (("python", "python"), ("java", "java"), ("javascript", "javascript"), ("cuda", "cuda"), ("arkts", "arkts")):
        if name in language_keys: return adapter, None
    return "none", "no supported FM-Agent language was found"


def profile(target: Path, requested: str) -> dict:
    language_keys = languages(target)
    adapter, reason = auto_adapter(target, language_keys) if requested == "auto" else (requested, None)
    supported = adapter in {"cmake", "cargo", "go", "python", "java", "javascript", "typescript"}
    if adapter == "cmake" and not (target / "CMakeLists.txt").is_file(): reason, supported = "CMakeLists.txt is missing", False
    if adapter == "cargo" and not (target / "Cargo.toml").is_file(): reason, supported = "Cargo.toml is missing", False
    if adapter == "cargo" and not (target / "Cargo.lock").is_file(): reason, supported = "Cargo.lock is required for an offline frozen probe", False
    if adapter == "go" and not (target / "go.mod").is_file(): reason, supported = "go.mod is missing", False
    if adapter == "typescript" and not (target / "tsconfig.json").is_file(): reason, supported = "tsconfig.json is missing", False
    if adapter == "java" and "java" not in language_keys: reason, supported = "no Java source was found", False
    if adapter == "python" and "python" not in language_keys: reason, supported = "no Python source was found", False
    if adapter == "javascript" and "javascript" not in language_keys: reason, supported = "no JavaScript source was found", False
    if adapter == "cuda": reason, supported = "CUDA requires an explicitly approved toolchain adapter", False
    if adapter == "arkts": reason, supported = "ArkTS requires an explicitly approved toolchain adapter", False
    if adapter == "none": supported = False
    excluded = [profile.key for profile in PROFILES if profile.support_level == "external-plugin"]
    return {"schema_version": 2, "project": str(target), "languages": language_keys, "excluded_languages": excluded, "adapter": adapter, "supported": supported, "reason": reason, "generated_at": state.now()}


def run_command(command: list[str], cwd: Path, env: dict[str, str], timeout: int) -> dict:
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, env=env)
        return {"command": command, "returncode": completed.returncode, "stdout": completed.stdout[-8000:], "stderr": completed.stderr[-8000:]}
    except FileNotFoundError as exc:
        return {"command": command, "returncode": 127, "stdout": "", "stderr": f"required command is unavailable: {exc}"}
    except subprocess.TimeoutExpired as exc:
        return {"command": command, "returncode": 124, "stdout": (exc.stdout or "")[-8000:], "stderr": (exc.stderr or "")[-8000:] + "\nprobe command timed out"}


def adapter_commands(target: Path, adapter: str, attempt: Path, cmake_target: str | None) -> tuple[list[tuple[list[str], Path, dict[str, str]]], str | None]:
    env = dict(os.environ)
    if adapter == "cmake":
        build = attempt / "build"
        commands = [(["cmake", "-S", str(target), "-B", str(build)], target, env), (["cmake", "--build", str(build)] + (["--target", cmake_target] if cmake_target else []), target, env)]
        return commands, None
    if adapter == "cargo":
        env["CARGO_TARGET_DIR"] = str(attempt / "target")
        return [(["cargo", "build", "--frozen"], target, env)], None
    if adapter == "go":
        env.update({"GOCACHE": str(attempt / "go-cache"), "GOMODCACHE": str(attempt / "go-mod-cache"), "GOPROXY": "off"})
        return [(["go", "build", "./..."], target, env)], None
    if adapter == "python":
        script = "import pathlib,sys; root=pathlib.Path(sys.argv[1]); files=sys.argv[2:]; [compile((root / item).read_text(encoding='utf-8'), item, 'exec') for item in files]"
        files = [path.relative_to(target).as_posix() for path in source_files(target) if path.suffix.lower() == ".py"]
        return [([sys.executable, "-c", script, str(target), *files], attempt, env)], None
    if adapter == "java":
        classes, source_list = attempt / "classes", attempt / "java_sources.txt"
        classes.mkdir(parents=True, exist_ok=True)
        source_list.write_text("\n".join(str(path) for path in source_files(target) if path.suffix.lower() == ".java") + "\n", encoding="utf-8")
        return [(["javac", "-d", str(classes), f"@{source_list}"], attempt, env)], None
    if adapter == "javascript":
        commands = [(["node", "--check", str(path)], attempt, env) for path in source_files(target) if path.suffix.lower() in {".js", ".jsx"}]
        return commands, None
    if adapter == "typescript": return [(["tsc", "--noEmit", "--project", str(target / "tsconfig.json")], target, env)], None
    return [], "adapter has no safe built-in command"


def write_profile(target: Path, data: dict) -> Path:
    path = state.control_dir(target) / "build_profile.json"; state.atomic_json(path, data); return path


def configured_adapter(target: Path, requested: str | None) -> str:
    if requested is not None: return requested
    config = state.read_json(state.skill_dir(target) / "config.json", {})
    value = config.get("probe_adapter", "auto") if isinstance(config, dict) else "auto"
    return value if value in ADAPTERS else "auto"


def run_probe(target: Path, bug_id: str, attempt_number: int, requested: str, timeout: int, cmake_target: str | None) -> dict:
    data = profile(target, requested); profile_path = write_profile(target, data)
    attempt = state.skill_dir(target) / "probes" / safe_component(bug_id) / f"attempt_{attempt_number:03d}"
    # The host Bug Validator preparation pass owns reproduction.json/probe.* in
    # this immutable attempt directory.  Build evidence is coordinator-owned
    # and may be added exactly once beside those artifacts.
    if attempt.exists() and not (attempt / "reproduction.json").is_file():
        raise ValueError(f"probe attempt exists without a prepared reproduction contract: {attempt}")
    attempt.mkdir(parents=True, exist_ok=True)
    if (attempt / "build_result.json").exists():
        raise ValueError(f"build result already exists and is immutable: {attempt / 'build_result.json'}")
    result = {"schema_version": 1, "bug_id": bug_id, "attempt": attempt_number, "attempt_dir": str(attempt), "profile_path": str(profile_path), "profile": data, "commands": [], "started_at": state.now()}
    if not data["supported"]:
        result.update({"state": "unsupported", "ok": False, "reason": data["reason"] or "unsupported adapter"})
    else:
        commands, reason = adapter_commands(target, data["adapter"], attempt, cmake_target)
        if reason:
            result.update({"state": "unsupported", "ok": False, "reason": reason})
        else:
            result["commands"] = [run_command(command, cwd, env, timeout) for command, cwd, env in commands]
            result["ok"] = bool(result["commands"]) and all(item["returncode"] == 0 for item in result["commands"])
            result["state"] = "completed"
    result["ended_at"] = state.now(); state.atomic_json(attempt / "build_result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect a safe language/build adapter and run one immutable Bug Validator probe attempt.")
    parser.add_argument("action", choices=("detect", "run")); parser.add_argument("--project", required=True)
    parser.add_argument("--adapter", choices=tuple(sorted(ADAPTERS))); parser.add_argument("--bug-id"); parser.add_argument("--attempt", type=int, default=1); parser.add_argument("--timeout-seconds", type=int, default=120); parser.add_argument("--target", help="optional CMake build target")
    args = parser.parse_args(); target = project(args)
    try:
        adapter = configured_adapter(target, args.adapter)
        if args.action == "detect": result = profile(target, adapter); result["profile_path"] = str(write_profile(target, result))
        else:
            if not args.bug_id: parser.error("run requires --bug-id")
            if args.attempt < 1 or args.timeout_seconds < 1: parser.error("attempt and timeout-seconds must be positive")
            result = run_probe(target, args.bug_id, args.attempt, adapter, args.timeout_seconds, args.target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); raise SystemExit(2)


if __name__ == "__main__": main()
