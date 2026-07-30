#!/usr/bin/env python3
"""Persist the single current FM-Agent analysis after each gated transition."""
from __future__ import annotations

import argparse
import json

from _common import common_scope, project, scope, state
from locking import heartbeat, release
from isolation import sync as sync_isolation
from reset_full_artifacts import clear_transient, reset, reset_incremental_artifacts
from stage_gate import validate


def save(target, record):
    state.atomic_json(state.skill_dir(target) / "active.json", record)


def load_active(target):
    record = state.active_record(target)
    if not record:
        raise SystemExit("no active FM-Agent analysis")
    return record


def phase_receipt_ready(target, phase):
    """Require a joined scheduler receipt only when this phase created new jobs."""
    jobs_dir = state.skill_dir(target) / "jobs"
    jobs = [state.read_json(path, {}) for path in jobs_dir.glob("*.json")] if jobs_dir.is_dir() else []
    current = [job for job in jobs if isinstance(job, dict) and job.get("phase") == phase and not job.get("legacy_contract")]
    if not current:
        return True
    receipt = state.read_json(state.control_dir(target) / "phase_receipts" / f"{phase}.json", {})
    return isinstance(receipt, dict) and receipt.get("phase") == phase and receipt.get("gate_ready") is True


def main():
    parser = argparse.ArgumentParser(description="Record gated FM-Agent current-analysis progress.")
    parser.add_argument("action", choices=("prepare", "resume", "phase-start", "phase-complete", "phase-fail", "advance", "complete", "fail", "noop"))
    common_scope(parser); parser.add_argument("--mode", choices=("full", "incremental")); parser.add_argument("--phase"); parser.add_argument("--message", default=""); parser.add_argument("--config-json", default="{}")
    args = parser.parse_args(); target = project(args)
    if args.action == "prepare":
        if not args.mode: parser.error("prepare requires --mode")
        try: effective_config = json.loads(args.config_json)
        except json.JSONDecodeError: parser.error("--config-json must be JSON")
        args.one_phase = effective_config.get("one_phase", args.one_phase)
        if not args.submodules: args.submodules = effective_config.get("submodules", [])
        if not args.extra_edge: args.extra_edge = effective_config.get("extra_edge")
        if not args.knowledge: args.knowledge = effective_config.get("knowledge", [])
        fingerprint, inputs = scope(args, effective_config)
        snapshot_commit = state.git(target, "rev-parse", "HEAD")
        record = {"schema_version": 2, "mode": args.mode, "status": "running", "started_at": state.now(), "current_phase": state.PHASES[args.mode][0], "phases": state.PHASES[args.mode], "phase_status": {}, "phase_history": {}, "fingerprint": fingerprint, "inputs": inputs, "snapshot_commit": snapshot_commit, "resume": {"count": 0}}
    else:
        record = load_active(target); phase = args.phase or record.get("current_phase")
        if args.action == "resume":
            if record.get("status") not in state.RESUMABLE_STATUSES: raise SystemExit("analysis is not resumable")
            next_phase = None
            for candidate in record.get("phases", []):
                status = record.get("phase_status", {}).get(candidate, {}).get("status")
                if status == "succeeded":
                    gate = validate(target, record["mode"], candidate, record.get("inputs", {}).get("submodules", []))
                    if not gate["ok"]: raise SystemExit(f"completed phase is no longer valid: {candidate}: {gate['reason']}")
                elif next_phase is None: next_phase = candidate
            next_phase = next_phase or "finalize"
            prior = record.get("phase_status", {}).get(next_phase)
            if isinstance(prior, dict):
                record.setdefault("phase_history", {}).setdefault(next_phase, []).append(prior); record["phase_status"].pop(next_phase, None)
            resume = record.setdefault("resume", {"count": 0}); resume["count"] = int(resume.get("count", 0)) + 1; resume["last_resumed_at"] = state.now(); resume["last_resumed_from_phase"] = next_phase
            record.update({"status": "running", "current_phase": next_phase}); record.pop("ended_at", None); record.pop("failure", None)
        elif args.action in {"phase-start", "advance"}:
            if phase not in record["phases"]: raise SystemExit("unknown phase")
            if args.action == "phase-start" and record["mode"] == "full" and phase == "phase_cleanup": reset(target)
            if args.action == "phase-start" and record["mode"] == "incremental" and phase == "refresh_plan": reset_incremental_artifacts(target)
            previous = record["phase_status"].get(phase, {}); attempt = int(previous.get("attempt", 0)) + 1
            record["current_phase"] = phase; record["phase_status"][phase] = {"status": "running", "started_at": state.now(), "attempt": attempt}
        elif args.action == "phase-complete":
            if phase not in record["phases"]: raise SystemExit("unknown phase")
            if not phase_receipt_ready(target, phase): raise SystemExit(f"scheduler phase receipt is missing or not ready: {phase}")
            gate = validate(target, record["mode"], phase, record.get("inputs", {}).get("submodules", []))
            if not gate["ok"]: raise SystemExit(gate["reason"])
            record["phase_status"][phase] = {"status": "succeeded", "ended_at": state.now()}; index = record["phases"].index(phase)
            record["current_phase"] = record["phases"][index + 1] if index + 1 < len(record["phases"]) else phase
        elif args.action == "phase-fail": record["phase_status"][phase] = {"status": "failed", "ended_at": state.now(), "message": args.message}; record.update({"status": "failed", "ended_at": state.now(), "failure": args.message})
        elif args.action == "complete":
            missing = [item for item in record["phases"] if record["phase_status"].get(item, {}).get("status") != "succeeded"]
            if missing: raise SystemExit("cannot complete: phase gates not passed: " + ", ".join(missing))
            record.update({"status": "succeeded", "ended_at": state.now()})
            commit = state.git(target, "rev-parse", "HEAD")
            if record.get("snapshot_commit") != commit: raise SystemExit("analysis worktree moved away from its saved snapshot commit")
            state.atomic_json(state.skill_dir(target) / "baseline.json", {"schema_version": 4, "baseline_commit": commit, "fingerprint": record["fingerprint"], "inputs": record["inputs"], "completed_at": record["ended_at"]})
            state.version_log(target, commit)
        elif args.action == "fail": record.update({"status": "failed", "ended_at": state.now(), "failure": args.message})
        elif args.action == "noop": record.update({"status": "noop", "ended_at": state.now(), "message": args.message})
    record["updated_at"] = state.now(); save(target, record)
    if args.action in {"resume", "phase-start", "phase-complete"}: heartbeat(target)
    if args.action == "complete":
        clear_transient(target); release(target, "idle"); sync_isolation(target)
    elif args.action == "fail":
        release(target, "failed")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
