#!/usr/bin/env python3
"""Execute one reviewed Bug Validator reproduction without arbitrary commands.

Workers may design a small probe, but only this Coordinator-owned runner may
execute it.  It accepts a narrow JSON contract in the immutable attempt
directory and derives every command from its language adapter.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from _common import project, state
from probe_runner import safe_component
from fm_agent_core.languages import profile_for_key


MAX_OUTPUT_BYTES = 16_000


def attempt_dir(target: Path, bug_id: str, attempt: int) -> Path:
    if attempt < 1:
        raise ValueError("attempt must be positive")
    return state.skill_dir(target) / "probes" / safe_component(bug_id) / f"attempt_{attempt:03d}"


def read_contract(target: Path, bug_id: str, attempt: int) -> tuple[Path, dict]:
    root = attempt_dir(target, bug_id, attempt)
    contract = state.read_json(root / "reproduction.json", None)
    if not isinstance(contract, dict):
        raise ValueError("missing or invalid reproduction.json")
    expected = {
        "schema_version", "bug_id", "attempt", "snapshot_commit", "language",
        "public_entrypoint", "probe_file", "expected_marker", "not_confirmed_marker",
        "timeout_seconds",
    }
    if set(contract) != expected:
        raise ValueError("reproduction contract has unexpected or missing fields")
    if contract["schema_version"] != 1 or contract["bug_id"] != bug_id or contract["attempt"] != attempt:
        raise ValueError("reproduction contract identity does not match requested attempt")
    if contract["snapshot_commit"] != state.current_snapshot_commit(target):
        raise ValueError("reproduction contract snapshot does not match current analysis worktree")
    language = contract.get("language")
    profile = profile_for_key(language) if isinstance(language, str) else None
    if profile is None or profile.probe_extension is None:
        raise ValueError(f"unsupported FM-Agent reproduction language: {language}")
    if not isinstance(contract.get("public_entrypoint"), str) or not contract["public_entrypoint"].strip():
        raise ValueError("reproduction contract requires a public entrypoint explanation")
    if contract.get("expected_marker") != "CONFIRMED" or contract.get("not_confirmed_marker") != "NOT CONFIRMED":
        raise ValueError("reproduction contract must use the fixed confirmation markers")
    if not isinstance(contract.get("timeout_seconds"), int) or not 1 <= contract["timeout_seconds"] <= 120:
        raise ValueError("reproduction timeout_seconds must be between 1 and 120")
    expected_probe = "probe" + profile.probe_extension
    if contract.get("probe_file") != expected_probe:
        raise ValueError(f"reproduction probe_file must be {expected_probe}")
    probe = root / expected_probe
    if not probe.is_file() or probe.resolve().parent != root.resolve():
        raise ValueError("reproduction probe is missing or outside its attempt directory")
    return root, contract


class AdapterUnavailable(ValueError):
    """An expected project ecosystem or sandbox is not available locally."""


def _virtual_probe(target: Path, root: Path, contract: dict) -> str:
    return "/project/" + (root / contract["probe_file"]).relative_to(target).as_posix()


def _rust_runner(target: Path, root: Path, contract: dict, scratch: Path) -> tuple[list[str], dict[str, str]]:
    manifest = target / "Cargo.toml"
    if not manifest.is_file():
        raise AdapterUnavailable("Rust dynamic reproduction requires Cargo.toml")
    package = re.search(r"(?ms)^\s*\[package\].*?^\s*name\s*=\s*['\"]([^'\"]+)['\"]", manifest.read_text(encoding="utf-8", errors="replace"))
    if package is None:
        raise AdapterUnavailable("Cargo.toml has no package name")
    runner = scratch / "rust-runner"; source = runner / "src"; source.mkdir(parents=True)
    # The Worker supplies only Rust source.  The Coordinator owns the generated
    # Cargo manifest and its fixed, path-only dependency on the public crate.
    shutil.copy2(root / contract["probe_file"], source / "main.rs")
    crate_name = package.group(1)
    (runner / "Cargo.toml").write_text(
        "[package]\nname = \"fm-agent-probe\"\nversion = \"0.0.0\"\nedition = \"2021\"\n"
        f"\n[dependencies]\n{crate_name} = {{ path = \"/project\" }}\n",
        encoding="utf-8",
    )
    environment = {"FM_AGENT_RUST_CRATE": crate_name.replace("-", "_"), "CARGO_HOME": "/tmp/cargo-home", "CARGO_TARGET_DIR": "/tmp/cargo-target", "CARGO_NET_OFFLINE": "true"}
    return [shutil.which("cargo") or "cargo", "run", "--offline", "--manifest-path", "/tmp/rust-runner/Cargo.toml"], environment


def command_for(target: Path, root: Path, contract: dict, scratch: Path) -> tuple[list[str], dict[str, str]]:
    """Return only Coordinator-owned argv and environment for one ecosystem."""
    language, probe = contract["language"], _virtual_probe(target, root, contract)
    if language == "python":
        return [sys.executable, "-c", "import runpy; runpy.run_path(__import__('sys').argv[1], run_name='__main__')", probe], {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "FM_AGENT_PROJECT_ROOT": "/project"}
    if language == "javascript":
        node = shutil.which("node")
        if not node: raise AdapterUnavailable("Node.js is unavailable")
        return [node, probe], {"FM_AGENT_PROJECT_ROOT": "/project", "FM_AGENT_PUBLIC_ENTRY": "/project"}
    if language == "typescript":
        tsx = shutil.which("tsx")
        if not tsx: raise AdapterUnavailable("TypeScript dynamic reproduction requires the approved tsx runtime")
        return [tsx, probe], {"FM_AGENT_PROJECT_ROOT": "/project", "FM_AGENT_PUBLIC_ENTRY": "/project"}
    if language == "go":
        go = shutil.which("go")
        if not go or not (target / "go.mod").is_file(): raise AdapterUnavailable("Go dynamic reproduction requires go and go.mod")
        return [go, "run", probe], {"GOCACHE": "/tmp/go-cache", "GOMODCACHE": "/tmp/go-mod-cache", "GOPROXY": "off", "FM_AGENT_PROJECT_ROOT": "/project"}
    if language == "rust": return _rust_runner(target, root, contract, scratch)
    profile = profile_for_key(language)
    ecosystems = ", ".join(profile.runtime_ecosystems) if profile else ""
    raise AdapterUnavailable(f"no approved sandboxed dynamic adapter for {language}{': ' + ecosystems if ecosystems else ''}")


def sandbox_command(target: Path, scratch: Path, command: list[str]) -> list[str]:
    """Execute from a read-only project view with no network and a fresh /tmp."""
    bwrap = shutil.which("bwrap")
    if not bwrap: raise AdapterUnavailable("bubblewrap is required; unsafe probe execution is intentionally disabled")
    scratch.mkdir(parents=True, exist_ok=True)
    return [
        bwrap, "--die-with-parent", "--new-session", "--unshare-net",
        "--ro-bind", "/", "/", "--dir", "/project", "--ro-bind", str(target), "/project",
        "--bind", str(scratch), "/tmp", "--proc", "/proc", "--dev", "/dev", "--chdir", "/project", "--",
        *command,
    ]


def output_classification(stdout: str, returncode: int) -> tuple[str, str]:
    if returncode != 0:
        return "runtime_error", "probe exited with a non-zero status"
    confirmed = bool(re.search(r"(?m)^CONFIRMED(?:\s|$)", stdout))
    not_confirmed = bool(re.search(r"(?m)^NOT CONFIRMED(?:\s|$)", stdout))
    if confirmed and not not_confirmed:
        return "confirmed", "probe reproduced the specified behavior mismatch"
    if not_confirmed and not confirmed:
        return "not_reproduced", "probe completed without reproducing the candidate"
    return "inconclusive", "probe did not emit exactly one required confirmation marker"


def run(target: Path, bug_id: str, attempt: int) -> dict:
    root, contract = read_contract(target, bug_id, attempt)
    result_path = root / "reproduction_result.json"
    if result_path.exists():
        raise ValueError(f"reproduction result already exists and is immutable: {result_path}")
    result = {
        "schema_version": 1,
        "bug_id": bug_id,
        "attempt": attempt,
        "snapshot_commit": contract["snapshot_commit"],
        "language": contract["language"],
        "public_entrypoint": contract["public_entrypoint"],
        "probe_path": (root / contract["probe_file"]).relative_to(target).as_posix(),
        "command": [],
        "started_at": state.now(),
    }
    try:
        # This directory is coordinator-owned, transient execution state; the
        # submitted probe and its immutable result stay at the attempt root.
        scratch = root / "sandbox"
        inner_command, adapter_env = command_for(target, root, contract, scratch)
        command = sandbox_command(target, scratch, inner_command)
        environment = {"PATH": os.environ.get("PATH", ""), "HOME": "/tmp/home", "TMPDIR": "/tmp", **adapter_env}
        result["command"] = command
        result["sandbox"] = {"engine": "bubblewrap", "network": "disabled", "project": "read-only", "tmp": "private"}
        try:
            completed = subprocess.run(command, cwd=target, text=True, capture_output=True, timeout=contract["timeout_seconds"], env=environment)
            stdout, stderr = completed.stdout[-MAX_OUTPUT_BYTES:], completed.stderr[-MAX_OUTPUT_BYTES:]
            classification, reason = output_classification(stdout, completed.returncode)
            result.update({"state": "completed", "classification": classification, "reason": reason, "returncode": completed.returncode, "stdout": stdout, "stderr": stderr})
        except FileNotFoundError as exc:
            result.update({"state": "execution_error", "classification": "runtime_error", "reason": f"required command is unavailable: {exc}", "returncode": 127, "stdout": "", "stderr": ""})
        except subprocess.TimeoutExpired as exc:
            result.update({"state": "execution_error", "classification": "runtime_error", "reason": "probe command timed out", "returncode": 124, "stdout": (exc.stdout or "")[-MAX_OUTPUT_BYTES:], "stderr": (exc.stderr or "")[-MAX_OUTPUT_BYTES:]})
    except AdapterUnavailable as exc:
        result.update({"state": "unsupported", "classification": "inconclusive", "reason": str(exc), "returncode": None, "stdout": "", "stderr": ""})
    result["ended_at"] = state.now()
    state.atomic_json(result_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one reviewed FM-Agent Bug Validator dynamic reproduction.")
    parser.add_argument("run", nargs="?", default="run", choices=("run",))
    parser.add_argument("--project", required=True)
    parser.add_argument("--bug-id", required=True)
    parser.add_argument("--attempt", required=True, type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(run(project(args), args.bug_id, args.attempt), ensure_ascii=False, indent=2))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
