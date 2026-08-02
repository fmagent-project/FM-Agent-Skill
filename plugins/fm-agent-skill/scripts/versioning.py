#!/usr/bin/env python3
"""Runtime/checkpoint identity for safe cross-session continuation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from _common import project, state


STATE_SCHEMA_VERSION = 2
SCHEDULER_SCHEMA_VERSION = 2
WORKER_PROMPT_POLICIES = {"invalidate", "allow_prompt_only"}
CONTRACT_START = "<!-- fm-agent-execution-contract:start -->"
CONTRACT_END = "<!-- fm-agent-execution-contract:end -->"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_commit(plugin: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(plugin), "rev-parse", "HEAD"],
        text=True, capture_output=True, check=False,
    )
    return completed.stdout.strip() or "unversioned"


def _worker_hash(plugin: Path, transform) -> str:
    digest = hashlib.sha256()
    for path in sorted((plugin / "agents").glob("*.md")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(transform(path.read_text(encoding="utf-8")).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _execution_contract(text: str) -> str:
    """Hash an explicitly delimited semantic contract when one is provided.

    Existing Workers have not yet been split into a prompt-only and execution
    contract region, so their full text remains execution-relevant.  This is
    deliberately conservative: a prose edit cannot silently preserve jobs.
    """
    start, end = text.find(CONTRACT_START), text.find(CONTRACT_END)
    if start >= 0 and end > start:
        return text[start + len(CONTRACT_START):end]
    return text


def worker_execution_hash(plugin: Path) -> str:
    return _worker_hash(plugin, _execution_contract)


def worker_prompt_hash(plugin: Path) -> str:
    return _worker_hash(plugin, lambda text: text)


def worker_definition_hash(plugin: Path) -> str:
    """Backward-compatible name for the complete Worker prompt hash."""
    return worker_prompt_hash(plugin)


def _prompt_policy(target: Path | None) -> str:
    if target is None:
        return "invalidate"
    candidates = [state.skill_dir(target) / "config.json"]
    checkpoint_config = state.skill_dir(target) / "checkpoint" / "current" / "recovery" / "config.json"
    candidates.append(checkpoint_config)
    for path in candidates:
        value = state.read_json(path, {})
        policy = value.get("worker_prompt_change_policy") if isinstance(value, dict) else None
        if policy in WORKER_PROMPT_POLICIES:
            return policy
    return "invalidate"


def runtime_version(target: Path | None = None) -> dict:
    plugin = Path(__file__).resolve().parents[1]
    manifest = json.loads((plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "plugin_version": manifest["version"],
        "source_commit": _source_commit(plugin),
        "worker_execution_hash": worker_execution_hash(plugin),
        "worker_prompt_hash": worker_prompt_hash(plugin),
        "worker_prompt_change_policy": _prompt_policy(target),
        "worker_definition_hash": worker_definition_hash(plugin),
        "state_schema_version": STATE_SCHEMA_VERSION,
        "scheduler_schema_version": SCHEDULER_SCHEMA_VERSION,
    }


def compare(saved: dict, current: dict | None = None) -> dict:
    current = current or runtime_version()
    keys = (
        "plugin_version", "source_commit", "worker_execution_hash",
        "state_schema_version", "scheduler_schema_version",
    )
    differences = {
        key: {"checkpoint": saved.get(key), "runtime": current.get(key)}
        for key in keys if saved.get(key) != current.get(key)
    }
    saved_policy = saved.get("worker_prompt_change_policy", "invalidate")
    current_policy = current.get("worker_prompt_change_policy", "invalidate")
    if saved_policy not in WORKER_PROMPT_POLICIES or current_policy not in WORKER_PROMPT_POLICIES:
        differences["worker_prompt_change_policy"] = {"checkpoint": saved_policy, "runtime": current_policy}
    elif saved_policy != current_policy:
        differences["worker_prompt_change_policy"] = {"checkpoint": saved_policy, "runtime": current_policy}
    elif saved_policy != "allow_prompt_only" and saved.get("worker_prompt_hash") != current.get("worker_prompt_hash"):
        differences["worker_prompt_hash"] = {"checkpoint": saved.get("worker_prompt_hash"), "runtime": current.get("worker_prompt_hash")}
    # Schema migrations must be explicit.  No migration currently changes
    # FM-Agent semantics or scheduler identities.
    migrations: list[str] = []
    return {
        "ok": not differences,
        "compatible": not differences,
        "differences": differences,
        "migrations": migrations,
        "runtime": current,
    }


def checkpoint_compatibility(target: Path) -> dict:
    checkpoint = state.skill_dir(target) / "checkpoint" / "snapshot.json"
    value = state.read_json(checkpoint, {})
    saved = value.get("version") if isinstance(value, dict) else None
    if not isinstance(saved, dict):
        return {"ok": False, "compatible": False, "reason": "checkpoint version record is missing"}
    return compare(saved)


def require_compatible(target: Path) -> dict:
    result = checkpoint_compatibility(target)
    if not result.get("compatible"):
        detail = json.dumps(result.get("differences", result.get("reason")), ensure_ascii=False, sort_keys=True)
        raise RuntimeError("checkpoint/runtime version incompatibility: " + detail)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect FM-Agent runtime/checkpoint versions.")
    parser.add_argument("action", choices=("show", "check"))
    parser.add_argument("--project", required=True)
    args = parser.parse_args(); target = project(args)
    try:
        result = runtime_version(target) if args.action == "show" else checkpoint_compatibility(target)
        code = 0 if result.get("ok", True) else 2
    except (OSError, ValueError, RuntimeError) as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
