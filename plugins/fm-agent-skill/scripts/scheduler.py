#!/usr/bin/env python3
"""Durable current-job state for host FM-Agent workers; no run history."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import project, state

JOB_TYPES = {"phase_plan", "phase_refine", "domain_context", "resolve_agent_static_edges", "spec_batch", "verify_function", "bug_validate", "select_relevant_modules", "select_relevant_files", "incremental_spec_plan", "reconcile_caller_info"}
RETRYABLE_FAILURES = {"execution", "output", "interrupted"}
FAILURE_CLASSES = RETRYABLE_FAILURES | {"input", "semantic", "cancelled"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

def _safe_id(value):
    if not ID.fullmatch(value or ""): raise ValueError("invalid job id")
    return value
def _path(target, job_id): return state.skill_dir(target) / "jobs" / f"{_safe_id(job_id)}.json"
def _load(target, job_id):
    job = state.read_json(_path(target, job_id), {})
    if not isinstance(job, dict) or job.get("id") != job_id: raise ValueError("job does not exist")
    return job
def _save(target, job): job["updated_at"] = state.now(); state.atomic_json(_path(target, job["id"]), job)
def _inside(target, value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts: raise ValueError("job paths must be project-relative and cannot traverse parent directories")
    rel = path.as_posix()
    if not (rel.startswith("fm_agent/") or rel.startswith("fm_agent_skill/probes/")): raise ValueError("worker output must stay in fm_agent/ or fm_agent_skill/probes/")
    if target not in (target / path).resolve().parents: raise ValueError("job path escapes project")
    return rel
def _artifact(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts: raise ValueError("artifact paths must stay under extracted_functions")
    return path.as_posix()
def _limit(target, payload):
    config = state.read_json(state.skill_dir(target) / "config.json", {})
    default = config.get("bug_validation_max_attempts", 1) if isinstance(config, dict) and payload.get("type") == "bug_validate" else (config.get("retries", 5) if isinstance(config, dict) else 5)
    value = payload.get("max_attempts", default)
    if not isinstance(value, int) or value < 1: raise ValueError("max_attempts must be a positive integer")
    return value
def _validate(target, job):
    missing = [item for item in job.get("required_outputs", []) if not (target / item).exists()]
    if missing: return "missing required worker output: " + ", ".join(missing)
    if job["type"] in {"spec_batch", "reconcile_caller_info"}:
        if not job.get("artifacts"): return "sidecar-writing job has no assigned artifacts"
        for rel in job["artifacts"]:
            ok, reason = state.sidecars_ready(target / "fm_agent" / "extracted_functions" / rel)
            if not ok: return f"invalid assigned sidecars for {rel}: {reason}"
    if job["type"] == "phase_plan":
        phases = state.read_json(target / "fm_agent" / "phases.json", {})
        if not isinstance(phases, dict) or not phases.get("phases"): return "phase-plan worker did not create a non-empty fm_agent/phases.json"
    if job["type"] == "domain_context":
        ok, reason = state.specification_context_ready(target)
        if not ok: return reason
    return None
def _fail(job, kind, message):
    if kind not in FAILURE_CLASSES: raise ValueError("unsupported failure class")
    job.update({"failure_class": kind, "message": message or "worker failed", "failed_at": state.now(), "status": "retryable" if kind in RETRYABLE_FAILURES and job["attempts"] < job["max_attempts"] else "failed"})
def create(target, payload):
    if not isinstance(payload, dict): raise ValueError("job JSON must be an object")
    job_id, kind = payload.get("id"), payload.get("type"); _safe_id(job_id)
    if kind not in JOB_TYPES: raise ValueError("unsupported job type")
    if _path(target, job_id).exists(): raise ValueError("job already exists")
    deps, outputs, artifacts = payload.get("depends_on", []), payload.get("required_outputs", []), payload.get("artifacts", [])
    if not all(isinstance(item, str) and ID.fullmatch(item) for item in deps): raise ValueError("invalid dependency id")
    if not all(isinstance(item, str) for item in outputs + artifacts): raise ValueError("job paths must be strings")
    outputs = [_inside(target, item) for item in outputs]
    if len(set(outputs)) != len(outputs): raise ValueError("duplicate required output")
    if kind == "incremental_spec_plan" and outputs: raise ValueError("incremental spec planning returns a plan; it must not write artifacts")
    job = {"schema_version": 1, "id": job_id, "type": kind, "status": "queued", "depends_on": deps, "required_outputs": outputs, "artifacts": [_artifact(item) for item in artifacts], "attempts": 0, "max_attempts": _limit(target, payload), "created_at": state.now(), "updated_at": state.now()}
    if isinstance(payload.get("input"), dict): job["input"] = payload["input"]
    _save(target, job); return job
def ready(target):
    root = state.skill_dir(target) / "jobs"; result = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        job = state.read_json(path, {})
        if not isinstance(job, dict) or job.get("status") != "queued": continue
        try: deps = [_load(target, item) for item in job.get("depends_on", [])]
        except ValueError: continue
        if all(item.get("status") == "succeeded" for item in deps): result.append(job)
    return result
def transition(target, job_id, action, result, message, failure_class):
    job = _load(target, job_id); job.setdefault("max_attempts", _limit(target, job))
    if action == "start":
        if job["status"] != "queued" or job not in ready(target): raise ValueError("job is not ready")
        job["status"] = "running"; job["attempts"] += 1; job["started_at"] = state.now()
    elif action == "complete":
        if job["status"] != "running": raise ValueError("only a running job can complete")
        error = _validate(target, job)
        if error: _fail(job, "output", error)
        else:
            job["status"] = "succeeded"; job["completed_at"] = state.now()
            if result is not None: job["result"] = result
    elif action == "fail":
        if job["status"] not in {"queued", "running"}: raise ValueError("only queued or running jobs can fail")
        _fail(job, failure_class, message)
    else:
        if job["status"] != "retryable": raise ValueError("only a retryable job can be requeued")
        job["status"] = "queued"; job["requeued_at"] = state.now()
    _save(target, job); return job
def recover(target):
    root = state.skill_dir(target) / "jobs"; succeeded, retryable = [], []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        job = state.read_json(path, {})
        if not isinstance(job, dict) or job.get("status") != "running": continue
        job.setdefault("max_attempts", _limit(target, job)); error = _validate(target, job)
        if error:
            _fail(job, "interrupted", error)
            if job["status"] == "retryable": retryable.append(job["id"])
        else: job["status"] = "succeeded"; job["completed_at"] = state.now(); job["recovered"] = True; succeeded.append(job["id"])
        _save(target, job)
    return {"recovered_succeeded": succeeded, "retryable": retryable}
def main():
    parser = argparse.ArgumentParser(description="Record and validate current FM-Agent host worker jobs.")
    parser.add_argument("action", choices=("create", "ready", "start", "complete", "fail", "retry", "recover", "show")); parser.add_argument("--project", required=True); parser.add_argument("--job-id"); parser.add_argument("--job-json"); parser.add_argument("--result-json"); parser.add_argument("--message"); parser.add_argument("--failure-class", choices=tuple(sorted(FAILURE_CLASSES)), default="execution")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "create":
            if not args.job_json: raise ValueError("--job-json is required")
            response = create(target, json.loads(args.job_json))
        elif args.action == "ready": response = {"jobs": ready(target)}
        elif args.action == "recover": response = recover(target)
        else:
            if not args.job_id: raise ValueError("--job-id is required")
            response = _load(target, args.job_id) if args.action == "show" else transition(target, args.job_id, args.action, json.loads(args.result_json) if args.result_json else None, args.message, args.failure_class)
        print(json.dumps(response, ensure_ascii=False, indent=2))
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); sys.exit(2)
if __name__ == "__main__": main()
