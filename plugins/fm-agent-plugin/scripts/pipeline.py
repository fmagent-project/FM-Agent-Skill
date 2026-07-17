#!/usr/bin/env python3
"""Persist phase transitions only after a deterministic stage gate passes."""
from __future__ import annotations

import argparse
import json
import uuid

from _common import common_scope, project, scope, state
from locking import release
from reset_full_artifacts import reset
from stage_gate import validate


def save(target, record):
    state.atomic_json(state.plugin_dir(target) / "runs" / f"{record['id']}.json", record)
    state.atomic_json(state.plugin_dir(target) / "active.json", record)


def main():
    parser = argparse.ArgumentParser(description="Record gated FM-Agent analysis progress.")
    parser.add_argument("action", choices=("prepare", "phase-start", "phase-complete", "phase-fail", "advance", "complete", "fail", "noop"))
    common_scope(parser); parser.add_argument("--mode", choices=("full", "incremental")); parser.add_argument("--run-id"); parser.add_argument("--phase"); parser.add_argument("--message", default=""); parser.add_argument("--config-json", default="{}")
    args = parser.parse_args(); target = project(args)
    if args.action == "prepare":
        if not args.mode: parser.error("prepare requires --mode")
        try: effective_config = json.loads(args.config_json)
        except json.JSONDecodeError: parser.error("--config-json must be JSON")
        args.one_phase = effective_config.get("one_phase", args.one_phase)
        if not args.submodules: args.submodules = effective_config.get("submodules", [])
        if not args.extra_edge: args.extra_edge = effective_config.get("extra_edge")
        if not args.knowledge: args.knowledge = effective_config.get("knowledge", [])
        run_id = args.run_id or f"run-{uuid.uuid4().hex[:12]}"; fingerprint, inputs = scope(args, effective_config)
        record = {"id": run_id, "mode": args.mode, "status": "running", "started_at": state.now(), "current_phase": state.PHASES[args.mode][0], "phases": state.PHASES[args.mode], "phase_status": {}, "fingerprint": fingerprint, "inputs": inputs}
    else:
        if not args.run_id: parser.error(f"{args.action} requires --run-id")
        record = state.read_json(state.plugin_dir(target) / "runs" / f"{args.run_id}.json", None)
        if not isinstance(record, dict): raise SystemExit("run record not found")
        phase = args.phase or record.get("current_phase")
        if args.action in {"phase-start", "advance"}:
            if phase not in record["phases"]: raise SystemExit("unknown phase")
            if args.action == "phase-start" and record["mode"] == "full" and phase == "phase_cleanup":
                reset(target)
            record["current_phase"] = phase; record["phase_status"][phase] = {"status": "running", "started_at": state.now()}
        elif args.action == "phase-complete":
            if phase not in record["phases"]: raise SystemExit("unknown phase")
            gate = validate(target, record["mode"], phase, record.get("inputs", {}).get("submodules", []), record["id"])
            if not gate["ok"]: raise SystemExit(gate["reason"])
            record["phase_status"][phase] = {"status": "succeeded", "ended_at": state.now()}; index = record["phases"].index(phase)
            record["current_phase"] = record["phases"][index + 1] if index + 1 < len(record["phases"]) else phase
        elif args.action == "phase-fail":
            record["phase_status"][phase] = {"status": "failed", "ended_at": state.now(), "message": args.message}; record.update({"status": "failed", "ended_at": state.now(), "failure": args.message})
        elif args.action == "complete":
            missing = [phase for phase in record["phases"] if record["phase_status"].get(phase, {}).get("status") != "succeeded"]
            if missing: raise SystemExit("cannot complete: phase gates not passed: " + ", ".join(missing))
            record.update({"status": "succeeded", "ended_at": state.now()})
            commit = state.git(target, "rev-parse", "HEAD")
            state.atomic_json(state.plugin_dir(target) / "baseline.json", {"schema_version": 3, "analysis_commit": commit, "observed_commit": commit, "observed_at": record["ended_at"], "source_snapshot": state.source_snapshot(target, record["inputs"].get("submodules", [])), "fingerprint": record["fingerprint"], "inputs": record["inputs"], "run_id": record["id"], "completed_at": record["ended_at"]})
        elif args.action == "fail": record.update({"status": "failed", "ended_at": state.now(), "failure": args.message})
        elif args.action == "noop": record.update({"status": "noop", "ended_at": state.now(), "message": args.message})
    save(target, record)
    if args.action == "complete":
        release(target, record["id"], "idle")
    elif args.action == "fail":
        release(target, record["id"], "failed")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
