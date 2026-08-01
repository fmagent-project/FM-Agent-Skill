#!/usr/bin/env python3
"""Run safe, isolated Bug Validator build probes across FM-Agent languages.

This runner never accepts an LLM-provided shell command.  It detects a bounded
adapter from the project snapshot, records that profile, and executes only the
adapter's fixed command list under a probe-owned attempt directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from _common import project, state
from fm_agent_core.languages import PROFILES, probe_adapter_choices, source_extensions
from sandbox import AdapterUnavailable, sandbox_command, sandbox_metadata


# Derived from the central registry; do not add extension maps in this runner.
LANGUAGE_EXTENSIONS = {
    profile.key: set(profile.extensions) for profile in PROFILES
    if profile.extensions & source_extensions()
}
IGNORED_DIRS = {".git", ".codegraph", "fm_agent", "fm_agent_skill", "node_modules", "build", "target", "dist", "out", "__pycache__"}
ADAPTERS = frozenset(probe_adapter_choices())


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


def _detectors_present(target: Path, language) -> bool:
    return not language.requires_build_metadata or all((target / item).is_file() for item in language.build_detectors)


def auto_adapter(target: Path, language_keys: list[str]) -> tuple[str, str | None]:
    for language in PROFILES:
        if language.key in language_keys and language.build_adapter and _detectors_present(target, language):
            return language.build_adapter, None
    return "none", "no supported FM-Agent language was found"


def profile(target: Path, requested: str) -> dict:
    language_keys = languages(target)
    adapter, reason = auto_adapter(target, language_keys) if requested == "auto" else (requested, None)
    candidates = [item for item in PROFILES if item.key in language_keys and item.build_adapter == adapter]
    supported = bool(candidates) and adapter != "none"
    if not candidates and adapter != "none":
        reason = reason or f"no detected language profile owns build adapter {adapter}"
    elif candidates and not any(_detectors_present(target, item) for item in candidates):
        required = sorted({name for item in candidates for name in item.build_detectors})
        reason, supported = f"required project metadata is missing: {', '.join(required)}", False
    if adapter == "none": supported = False
    excluded = [profile.key for profile in PROFILES if profile.support_level == "capability_plugin"]
    return {"schema_version": 2, "project": str(target), "languages": language_keys, "excluded_languages": excluded, "adapter": adapter, "supported": supported, "reason": reason, "generated_at": state.now()}


def run_command(target: Path, scratch: Path, command: list[str], env: dict[str, str], timeout: int) -> dict:
    """Run a fixed build argv in the same fail-closed sandbox as probes."""
    try:
        sandboxed = sandbox_command(target, scratch, command, env)
        completed = subprocess.run(sandboxed, cwd=target, text=True, capture_output=True, timeout=timeout, env={})
        return {"state": "completed", "command": command, "sandbox": sandbox_metadata(), "returncode": completed.returncode, "stdout": completed.stdout[-8000:], "stderr": completed.stderr[-8000:]}
    except AdapterUnavailable as exc:
        return {"state": "unsupported", "command": command, "sandbox": sandbox_metadata(), "returncode": None, "stdout": "", "stderr": str(exc)}
    except FileNotFoundError as exc:
        return {"state": "execution_error", "command": command, "returncode": 127, "stdout": "", "stderr": f"required command is unavailable: {exc}"}
    except subprocess.TimeoutExpired as exc:
        return {"state": "execution_error", "command": command, "returncode": 124, "stdout": (exc.stdout or "")[-8000:], "stderr": (exc.stderr or "")[-8000:] + "\nprobe command timed out"}


def adapter_commands(target: Path, adapter: str, attempt: Path, cmake_target: str | None) -> tuple[list[tuple[list[str], Path, dict[str, str]]], str | None]:
    env: dict[str, str] = {}
    if adapter == "cmake":
        commands = [(["cmake", "-S", "/project", "-B", "/tmp/build"], target, env), (["cmake", "--build", "/tmp/build"] + (["--target", cmake_target] if cmake_target else []), target, env)]
        return commands, None
    if adapter == "cargo":
        env.update({"CARGO_HOME": "/tmp/cargo-home", "CARGO_TARGET_DIR": "/tmp/cargo-target", "CARGO_NET_OFFLINE": "true"})
        return [(["cargo", "build", "--frozen"], target, env)], None
    if adapter == "go":
        env.update({"GOCACHE": "/tmp/go-cache", "GOMODCACHE": "/tmp/go-mod-cache", "GOPROXY": "off"})
        return [(["go", "build", "./..."], target, env)], None
    if adapter == "python":
        script = "import pathlib,sys; root=pathlib.Path(sys.argv[1]); files=sys.argv[2:]; [compile((root / item).read_text(encoding='utf-8'), item, 'exec') for item in files]"
        files = [path.relative_to(target).as_posix() for path in source_files(target) if path.suffix.lower() == ".py"]
        env.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        return [([sys.executable, "-c", script, "/project", *files], attempt, env)], None
    if adapter == "java":
        # Only this Coordinator-owned build scratch is bound as /tmp.  Keep the
        # generated source list there rather than exposing the attempt root.
        classes, source_list = attempt / "build-sandbox" / "classes", attempt / "build-sandbox" / "java_sources.txt"
        classes.mkdir(parents=True, exist_ok=True)
        source_list.write_text("\n".join("/project/" + path.relative_to(target).as_posix() for path in source_files(target) if path.suffix.lower() == ".java") + "\n", encoding="utf-8")
        return [(["javac", "-d", "/tmp/classes", "@/tmp/java_sources.txt"], attempt, env)], None
    if adapter == "javascript":
        commands = [(["node", "--check", "/project/" + path.relative_to(target).as_posix()], attempt, env) for path in source_files(target) if path.suffix.lower() in {".js", ".jsx"}]
        return commands, None
    if adapter == "typescript": return [(["tsc", "--noEmit", "--project", "/project/tsconfig.json"], target, env)], None
    return [], "adapter has no safe built-in command"


def write_profile(target: Path, data: dict, attempt: Path | None = None) -> Path:
    """Persist detection globally or a running build profile beside its attempt.

    ``detect`` has one current project profile. A concurrent ``run`` instead
    owns an immutable profile within its assigned attempt, so independently
    scheduled Bug Validators never overwrite each other's evidence.
    """
    path = (attempt / "build_profile.json") if attempt is not None else (state.control_dir(target) / "build_profile.json")
    if attempt is not None and path.exists():
        raise ValueError(f"build profile already exists and is immutable: {path}")
    state.atomic_json(path, data)
    return path


def configured_adapter(target: Path, requested: str | None) -> str:
    if requested is not None: return requested
    config = state.read_json(state.skill_dir(target) / "config.json", {})
    value = config.get("probe_adapter", "auto") if isinstance(config, dict) else "auto"
    if value not in ADAPTERS:
        raise ValueError(f"configured probe_adapter is unsupported by the LanguageProfile registry: {value}")
    return value


def run_probe(target: Path, bug_id: str, attempt_number: int, requested: str, timeout: int, cmake_target: str | None) -> dict:
    data = profile(target, requested)
    attempt = state.skill_dir(target) / "probes" / safe_component(bug_id) / f"attempt_{attempt_number:03d}"
    # The host Bug Validator preparation pass owns reproduction.json/probe.* in
    # this immutable attempt directory.  Build evidence is coordinator-owned
    # and may be added exactly once beside those artifacts.
    if attempt.exists() and not (attempt / "reproduction.json").is_file():
        raise ValueError(f"probe attempt exists without a prepared reproduction contract: {attempt}")
    attempt.mkdir(parents=True, exist_ok=True)
    if (attempt / "build_result.json").exists():
        raise ValueError(f"build result already exists and is immutable: {attempt / 'build_result.json'}")
    profile_path = write_profile(target, data, attempt)
    result = {"schema_version": 1, "bug_id": bug_id, "attempt": attempt_number, "attempt_dir": str(attempt), "profile_path": str(profile_path), "profile": data, "commands": [], "started_at": state.now()}
    if not data["supported"]:
        result.update({"state": "unsupported", "ok": False, "reason": data["reason"] or "unsupported adapter"})
    else:
        commands, reason = adapter_commands(target, data["adapter"], attempt, cmake_target)
        if reason:
            result.update({"state": "unsupported", "ok": False, "reason": reason})
        else:
            scratch = attempt / "build-sandbox"
            result["commands"] = [run_command(target, scratch, command, env, timeout) for command, _cwd, env in commands]
            unsupported = [item for item in result["commands"] if item["state"] == "unsupported"]
            result["ok"] = bool(result["commands"]) and all(item["state"] == "completed" and item["returncode"] == 0 for item in result["commands"])
            result["state"] = "unsupported" if unsupported else "completed"
            if unsupported:
                result["reason"] = unsupported[0]["stderr"]
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
