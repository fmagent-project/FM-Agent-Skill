#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from _common import project, state
from isolation import marker
import checkpoint
import scheduler

parser = argparse.ArgumentParser(description="Read existing FM-Agent diagnostics without starting analysis.")
parser.add_argument("--project", required=True); parser.add_argument("--bug-id")
args = parser.parse_args(); source = project(args)
isolation = marker(source)
snapshot_value = isolation.get("snapshot") if isinstance(isolation, dict) else None
snapshot_available = isinstance(snapshot_value, str) and Path(snapshot_value).is_dir()
checkpoint_state = None
if not snapshot_available:
    try: checkpoint_state = checkpoint.validate(source, repair_current=False)
    except checkpoint.CheckpointError as exc: checkpoint_state = {"ok": False, "reason": str(exc)}
target = Path(snapshot_value).resolve() if snapshot_available else checkpoint.root(source) / "current"
summary = state.read_json(state.fm_dir(target) / "bug_validation" / "summary.json", {})
run = state.read_json(state.skill_dir(target) / "active.json", {}) if snapshot_available else state.read_json(target / "recovery" / "active.json", {})
control = state.control_dir(target) if snapshot_available else target / "recovery" / "control"
verification = state.read_json(control / "phase_receipts" / "verification.json", {})
if not verification:
    verification = state.read_json(control / "phase_receipts" / "verify_affected.json", {})
resumable = state.inspect_resume(target) if snapshot_available else ({"ok": True, "mode": run.get("mode"), "resume_from_phase": state.first_incomplete_phase(run)} if checkpoint_state and checkpoint_state.get("ok") else {"ok": False, "reason": checkpoint_state.get("reason") if isinstance(checkpoint_state, dict) else "checkpoint unavailable"})
failure = state.read_json(state.skill_dir(source) / "failure.json", {})
status = run.get("status") if isinstance(run, dict) else None
official = status in {"succeeded", "noop"} and not failure
result = {
    "run": run,
    "summary": summary,
    "verification": verification,
    "result_authority": {
        "official_result_available": official,
        "status": "official" if official else "in_progress" if status == "running" else "incomplete",
        "reason": None if official else failure.get("reason") if isinstance(failure, dict) else "pipeline has not completed every phase gate",
        "analysis_project": str(target),
        "snapshot_available": snapshot_available,
    },
    "failure": failure,
    "checkpoint": checkpoint_state,
    "scheduler": scheduler.aggregate(source) if (checkpoint.root(source) / "state.db").is_file() else {},
    "legacy_json_jobs": len(list((state.skill_dir(source) / "jobs").glob("*.json"))),
    "resume": {
        "available": bool(resumable.get("ok")),
        "mode": resumable.get("mode"),
        "resume_from_phase": resumable.get("resume_from_phase"),
        "reason": resumable.get("reason"),
    },
}
if args.bug_id:
    root = state.fm_dir(target) / "bug_validation"
    for candidate in (root / f"{args.bug_id}.md", root / f"{args.bug_id}.result.json"):
        if candidate.is_file(): result["report_path"] = str(candidate); result["report"] = candidate.read_text(errors="replace"); break
print(json.dumps(result, ensure_ascii=False, indent=2))
