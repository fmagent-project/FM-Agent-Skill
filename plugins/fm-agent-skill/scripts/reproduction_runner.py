#!/usr/bin/env python3
"""Execute one reviewed Bug Validator reproduction without arbitrary commands.

Workers may design a small probe, but only this Coordinator-owned runner may
execute it.  It accepts a narrow JSON contract in the immutable attempt
directory and derives every command from its language adapter.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from _common import project, state
from probe_runner import safe_component


LANGUAGE_EXTENSIONS = {
    "c": ".c", "cpp": ".cpp", "python": ".py", "go": ".go",
    "rust": ".rs", "java": ".java", "javascript": ".js",
    "typescript": ".ts", "cuda": ".cu", "arkts": ".ets",
}
RUNTIME_ADAPTERS = {"python", "javascript", "go"}
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
    if language not in LANGUAGE_EXTENSIONS:
        raise ValueError(f"unsupported FM-Agent reproduction language: {language}")
    if not isinstance(contract.get("public_entrypoint"), str) or not contract["public_entrypoint"].strip():
        raise ValueError("reproduction contract requires a public entrypoint explanation")
    if contract.get("expected_marker") != "CONFIRMED" or contract.get("not_confirmed_marker") != "NOT CONFIRMED":
        raise ValueError("reproduction contract must use the fixed confirmation markers")
    if not isinstance(contract.get("timeout_seconds"), int) or not 1 <= contract["timeout_seconds"] <= 120:
        raise ValueError("reproduction timeout_seconds must be between 1 and 120")
    expected_probe = "probe" + LANGUAGE_EXTENSIONS[language]
    if contract.get("probe_file") != expected_probe:
        raise ValueError(f"reproduction probe_file must be {expected_probe}")
    probe = root / expected_probe
    if not probe.is_file() or probe.resolve().parent != root.resolve():
        raise ValueError("reproduction probe is missing or outside its attempt directory")
    return root, contract


def command_for(target: Path, root: Path, contract: dict) -> list[str]:
    probe = root / contract["probe_file"]
    if contract["language"] == "python":
        # runpy preserves the repository root as the process working directory,
        # so probes can import only through the public package entry point.
        return [sys.executable, "-c", "import runpy; runpy.run_path(__import__('sys').argv[1], run_name='__main__')", str(probe)]
    if contract["language"] == "javascript":
        return ["node", str(probe)]
    if contract["language"] == "go":
        return ["go", "run", str(probe)]
    raise ValueError("unsupported dynamic reproduction language")


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
    if contract["language"] not in RUNTIME_ADAPTERS:
        result.update({"state": "unsupported", "classification": "inconclusive", "reason": f"no approved dynamic reproduction adapter for {contract['language']}", "returncode": None, "stdout": "", "stderr": ""})
    else:
        command = command_for(target, root, contract)
        result["command"] = command
        try:
            completed = subprocess.run(command, cwd=target, text=True, capture_output=True, timeout=contract["timeout_seconds"])
            stdout, stderr = completed.stdout[-MAX_OUTPUT_BYTES:], completed.stderr[-MAX_OUTPUT_BYTES:]
            classification, reason = output_classification(stdout, completed.returncode)
            result.update({"state": "completed", "classification": classification, "reason": reason, "returncode": completed.returncode, "stdout": stdout, "stderr": stderr})
        except FileNotFoundError as exc:
            result.update({"state": "execution_error", "classification": "runtime_error", "reason": f"required command is unavailable: {exc}", "returncode": 127, "stdout": "", "stderr": ""})
        except subprocess.TimeoutExpired as exc:
            result.update({"state": "execution_error", "classification": "runtime_error", "reason": "probe command timed out", "returncode": 124, "stdout": (exc.stdout or "")[-MAX_OUTPUT_BYTES:], "stderr": (exc.stderr or "")[-MAX_OUTPUT_BYTES:]})
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
