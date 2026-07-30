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
MAX_REPORT_BYTES = 4096
REPORT_KEYS = {"job_id", "status", "outputs", "verdict", "classification", "escalation", "counts", "summary", "plan_path"}
BUG_CLASSIFICATIONS = {"confirmed", "not_reproduced", "rejected", "inconclusive"}
BUG_NEGATIVE_CLASSIFICATIONS = BUG_CLASSIFICATIONS - {"confirmed"}
TYPE_CAP_KEYS = {
    "spec_batch": "spec_concurrency",
    "verify_function": "verify_concurrency",
    "bug_validate": "bug_validation_concurrency",
    "incremental_spec_plan": "read_only_plan_concurrency",
}
DEFAULT_CAPS = {
    "max_active_subagents": 10,
    "spec_concurrency": 4,
    "verify_concurrency": 8,
    "bug_validation_concurrency": 1,
    "read_only_plan_concurrency": 2,
}

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
    if not (rel.startswith("fm_agent/") or rel.startswith("fm_agent_skill/probes/") or rel.startswith("fm_agent_skill/worker_reports/")): raise ValueError("worker output must stay in fm_agent/, fm_agent_skill/probes/, or its assigned worker report")
    if target not in (target / path).resolve().parents: raise ValueError("job path escapes project")
    return rel
def _artifact(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts: raise ValueError("artifact paths must stay under extracted_functions")
    return path.as_posix()
def _phase(value):
    if not isinstance(value, str) or not ID.fullmatch(value): raise ValueError("phase must be a valid phase id")
    return value
def _caps(target):
    saved = state.read_json(state.skill_dir(target) / "config.json", {})
    result = dict(DEFAULT_CAPS)
    if isinstance(saved, dict):
        for key in result:
            value = saved.get(key)
            if isinstance(value, int) and value > 0: result[key] = value
    return result
def _type_cap(job_type, caps):
    return caps.get(TYPE_CAP_KEYS.get(job_type), 1)
def _running(target):
    root = state.skill_dir(target) / "jobs"
    jobs = [state.read_json(path, {}) for path in sorted(root.glob("*.json"))] if root.is_dir() else []
    return [job for job in jobs if isinstance(job, dict) and job.get("status") == "running"]
def _capacity(target):
    caps, running = _caps(target), _running(target)
    by_type = {kind: sum(1 for job in running if job.get("type") == kind) for kind in JOB_TYPES}
    return {"caps": caps, "active": len(running), "by_type": by_type}
def _admission_error(target, job):
    capacity = _capacity(target); caps = capacity["caps"]
    if capacity["active"] >= caps["max_active_subagents"]:
        return f"global subagent capacity reached ({capacity['active']}/{caps['max_active_subagents']})"
    limit = _type_cap(job["type"], caps); active = capacity["by_type"].get(job["type"], 0)
    if active >= limit: return f"{job['type']} capacity reached ({active}/{limit})"
    return None
def _validate_report(job, result):
    # Keep interrupted analyses created by the pre-receipt scheduler resumable.
    # New manifests always carry phase + receipt requirements.
    if job.get("legacy_contract"):
        return None
    if not isinstance(result, dict): return "worker report must be a JSON object"
    try: size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError): return "worker report must be JSON-serializable"
    if size > MAX_REPORT_BYTES: return f"worker report exceeds {MAX_REPORT_BYTES} bytes; write detail to its assigned output"
    unknown = set(result) - REPORT_KEYS
    if unknown: return "worker report has unsupported keys: " + ", ".join(sorted(unknown))
    if result.get("job_id") != job["id"]: return "worker report job_id does not match manifest"
    if not isinstance(result.get("status"), str) or not result["status"].strip(): return "worker report requires non-empty status"
    if "outputs" in result and (not isinstance(result["outputs"], list) or not all(isinstance(item, str) for item in result["outputs"])): return "worker report outputs must be a string array"
    if "verdict" in result and result["verdict"] not in state.VERDICTS: return "worker report has invalid verdict"
    if "summary" in result and (not isinstance(result["summary"], str) or len(result["summary"]) > 500): return "worker report summary must be at most 500 characters"
    if job["type"] == "bug_validate" and result.get("classification") not in BUG_CLASSIFICATIONS: return "Bug Validator report requires a valid classification"
    if job["type"] == "incremental_spec_plan":
        expected = f"fm_agent_skill/worker_reports/{job['id']}.json"
        if result.get("plan_path") != expected: return "incremental plan report must name its assigned plan_path"
    return None
def _limit(target, payload):
    config = state.read_json(state.skill_dir(target) / "config.json", {})
    # Pre-receipt jobs retain their historical single Bug Validator attempt.
    default = (config.get("bug_validation_max_attempts", 5) if "phase" in payload else 1) if isinstance(config, dict) and payload.get("type") == "bug_validate" else (config.get("retries", 5) if isinstance(config, dict) else 5)
    value = payload.get("max_attempts", default)
    if not isinstance(value, int) or value < 1: raise ValueError("max_attempts must be a positive integer")
    return value
def _negative_attempts(target, payload):
    config = state.read_json(state.skill_dir(target) / "config.json", {})
    default = config.get("bug_validation_negative_retries", 2) if isinstance(config, dict) else 2
    value = payload.get("negative_retries", default)
    if not isinstance(value, int) or value < 0: raise ValueError("negative_retries must be a non-negative integer")
    return value + 1
def _validate(target, job, result=None):
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
    if job["type"] == "bug_validate" and not job.get("legacy_contract"):
        report = state.read_json(target / job["bug_result_path"], {})
        attempts = report.get("attempts") if isinstance(report, dict) else None
        index = int(job.get("negative_attempt_index", 1))
        if not isinstance(attempts, list) or len(attempts) < index: return "Bug Validator result must append the current probe to attempts"
        if result is not None:
            latest = attempts[-1]
            if not isinstance(latest, dict) or latest.get("classification") != result.get("classification"):
                return "Bug Validator result attempt classification does not match its receipt"
    return None
def _fail(job, kind, message):
    if kind not in FAILURE_CLASSES: raise ValueError("unsupported failure class")
    if job["type"] == "bug_validate" and not job.get("legacy_contract") and kind in RETRYABLE_FAILURES:
        runtime_attempts = int(job.get("runtime_attempts", 0)) + 1
        job["runtime_attempts"] = runtime_attempts
        retryable = runtime_attempts < job["max_attempts"]
    else: retryable = kind in RETRYABLE_FAILURES and job["attempts"] < job["max_attempts"]
    job.update({"failure_class": kind, "message": message or "worker failed", "failed_at": state.now(), "status": "retryable" if retryable else "failed"})
def create(target, payload):
    if not isinstance(payload, dict): raise ValueError("job JSON must be an object")
    job_id, kind = payload.get("id"), payload.get("type"); _safe_id(job_id)
    if kind not in JOB_TYPES: raise ValueError("unsupported job type")
    if _path(target, job_id).exists(): raise ValueError("job already exists")
    legacy_contract = "phase" not in payload
    phase = _phase(payload.get("phase", "unspecified")); deps, outputs, artifacts = payload.get("depends_on", []), payload.get("required_outputs", []), payload.get("artifacts", [])
    if not all(isinstance(item, str) and ID.fullmatch(item) for item in deps): raise ValueError("invalid dependency id")
    if not all(isinstance(item, str) for item in outputs + artifacts): raise ValueError("job paths must be strings")
    outputs = [_inside(target, item) for item in outputs]
    if len(set(outputs)) != len(outputs): raise ValueError("duplicate required output")
    worker_report = f"fm_agent_skill/worker_reports/{job_id}.json"
    if worker_report in outputs and outputs.count(worker_report) != 1: raise ValueError("worker report may appear only once")
    if any(item.startswith("fm_agent_skill/worker_reports/") and item != worker_report for item in outputs): raise ValueError("worker report path must match its job id")
    if kind == "incremental_spec_plan" and not legacy_contract and outputs != [worker_report]: raise ValueError("incremental spec planning must write only its assigned worker report")
    bug_results = [item for item in outputs if item.startswith("fm_agent/bug_validation/") and item.endswith(".result.json")]
    if kind == "bug_validate" and not legacy_contract and len(bug_results) != 1: raise ValueError("Bug Validator must assign exactly one fm_agent/bug_validation/*.result.json output")
    job = {"schema_version": 2, "id": job_id, "phase": phase, "type": kind, "status": "queued", "depends_on": deps, "required_outputs": outputs, "artifacts": [_artifact(item) for item in artifacts], "attempts": 0, "max_attempts": _limit(target, payload), "created_at": state.now(), "updated_at": state.now()}
    if legacy_contract: job["legacy_contract"] = True
    elif kind == "bug_validate": job.update({"bug_result_path": bug_results[0], "negative_max_attempts": _negative_attempts(target, payload), "negative_attempts": 0, "runtime_attempts": 0})
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
def admissible(target):
    """Return a deterministic, capacity-respecting subset of ready jobs."""
    capacity = _capacity(target); caps = capacity["caps"]; active = capacity["active"]
    by_type = dict(capacity["by_type"]); result = []
    for job in ready(target):
        if active >= caps["max_active_subagents"]: break
        limit = _type_cap(job["type"], caps); used = by_type.get(job["type"], 0)
        if used >= limit: continue
        result.append(job); active += 1; by_type[job["type"]] = used + 1
    return {"capacity": capacity, "jobs": result}
def phase_receipt(target, phase):
    phase = _phase(phase); root = state.skill_dir(target) / "jobs"
    jobs = [state.read_json(path, {}) for path in sorted(root.glob("*.json"))] if root.is_dir() else []
    jobs = [job for job in jobs if isinstance(job, dict) and job.get("phase") == phase]
    totals = {status: sum(1 for job in jobs if job.get("status") == status) for status in ("queued", "running", "retryable", "failed", "succeeded")}
    escalations = []
    for job in jobs:
        result = job.get("result", {}) if isinstance(job.get("result"), dict) else {}
        verdict = result.get("verdict")
        reason = None
        if job.get("status") in {"failed", "retryable"}: reason = job.get("failure_class", job.get("status"))
        elif verdict in {"MISMATCH", "DEPENDENCY_RISK", "INCONCLUSIVE", "ERROR"}: reason = verdict
        elif result.get("escalation") not in {None, "", "none", "NONE", False}: reason = "worker_escalation"
        if reason: escalations.append({"job_id": job.get("id"), "type": job.get("type"), "reason": reason})
    receipt = {"schema_version": 1, "phase": phase, "generated_at": state.now(), "totals": totals, "gate_ready": totals["queued"] == totals["running"] == totals["retryable"] == totals["failed"] == 0, "escalations": escalations}
    path = state.control_dir(target) / "phase_receipts" / f"{phase}.json"; state.atomic_json(path, receipt)
    return {"receipt_path": path.relative_to(target).as_posix(), **receipt}
def transition(target, job_id, action, result, message, failure_class):
    job = _load(target, job_id); job.setdefault("max_attempts", _limit(target, job))
    if action == "start":
        if job["status"] != "queued" or job not in ready(target): raise ValueError("job is not ready")
        error = _admission_error(target, job)
        if error: raise ValueError(error)
        job["status"] = "running"; job["attempts"] += 1; job["started_at"] = state.now()
        if job["type"] == "bug_validate" and not job.get("legacy_contract"):
            job["negative_attempt_index"] = int(job.get("negative_attempts", 0)) + 1
    elif action == "complete":
        if job["status"] != "running": raise ValueError("only a running job can complete")
        error = _validate_report(job, result) or _validate(target, job, result)
        if error: _fail(job, "output", error)
        elif job["type"] == "bug_validate" and not job.get("legacy_contract") and result["classification"] in BUG_NEGATIVE_CLASSIFICATIONS:
            negative_attempts = int(job.get("negative_attempts", 0)) + 1
            job["negative_attempts"] = negative_attempts; job["result"] = result
            if negative_attempts < int(job["negative_max_attempts"]):
                job.update({"status": "retryable", "retry_reason": "negative_result", "message": "Bug Validator did not confirm the candidate; repeat probe required"})
            else: job.update({"status": "succeeded", "completed_at": state.now(), "negative_validation_exhausted": True})
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
        if job["type"] == "bug_validate" and not job.get("legacy_contract") and error is None:
            error = "interrupted Bug Validator has no completed classification receipt"
        if error:
            _fail(job, "interrupted", error)
            if job["status"] == "retryable": retryable.append(job["id"])
        else: job["status"] = "succeeded"; job["completed_at"] = state.now(); job["recovered"] = True; succeeded.append(job["id"])
        _save(target, job)
    return {"recovered_succeeded": succeeded, "retryable": retryable}
def main():
    parser = argparse.ArgumentParser(description="Record, admit, and validate bounded current FM-Agent host worker jobs.")
    parser.add_argument("action", choices=("create", "ready", "admissible", "capacity", "phase-receipt", "start", "complete", "fail", "retry", "recover", "show")); parser.add_argument("--project", required=True); parser.add_argument("--job-id"); parser.add_argument("--phase"); parser.add_argument("--job-json"); parser.add_argument("--result-json"); parser.add_argument("--message"); parser.add_argument("--failure-class", choices=tuple(sorted(FAILURE_CLASSES)), default="execution")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "create":
            if not args.job_json: raise ValueError("--job-json is required")
            response = create(target, json.loads(args.job_json))
        elif args.action == "ready": response = {"jobs": ready(target)}
        elif args.action == "admissible": response = admissible(target)
        elif args.action == "capacity": response = _capacity(target)
        elif args.action == "phase-receipt":
            if not args.phase: raise ValueError("--phase is required")
            response = phase_receipt(target, args.phase)
        elif args.action == "recover": response = recover(target)
        else:
            if not args.job_id: raise ValueError("--job-id is required")
            response = _load(target, args.job_id) if args.action == "show" else transition(target, args.job_id, args.action, json.loads(args.result_json) if args.result_json else None, args.message, args.failure_class)
        print(json.dumps(response, ensure_ascii=False, indent=2))
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); sys.exit(2)
if __name__ == "__main__": main()
