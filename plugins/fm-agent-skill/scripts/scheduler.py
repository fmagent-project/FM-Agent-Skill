#!/usr/bin/env python3
"""Persistent, deterministic job state for Claude FM-Agent workers.

This program never invokes an LLM.  The coordinator uses it immediately before
and after an Agent-tool call so an interrupted run can discover completed work
without trusting an unstructured conversation transcript.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import project, state


JOB_TYPES = {
    "phase_plan", "phase_refine", "domain_context", "spec_batch",
    "verify_function", "bug_validate", "select_relevant_modules",
    "select_relevant_files", "incremental_spec_plan", "reconcile_caller_info",
}
TERMINAL = {"succeeded", "failed", "cancelled"}
RETRYABLE_FAILURES = {"execution", "output", "interrupted"}
FAILURE_CLASSES = RETRYABLE_FAILURES | {"input", "semantic", "cancelled"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_id(value: str, label: str) -> str:
    if not ID.fullmatch(value or ""):
        raise ValueError(f"invalid {label}")
    return value


def _job_path(target: Path, run_id: str, job_id: str) -> Path:
    _safe_id(run_id, "run id"); _safe_id(job_id, "job id")
    return state.plugin_dir(target) / "runs" / run_id / "jobs" / f"{job_id}.json"


def _load(target: Path, run_id: str, job_id: str) -> dict:
    value = state.read_json(_job_path(target, run_id, job_id), {})
    if not isinstance(value, dict) or value.get("id") != job_id or value.get("run_id") != run_id:
        raise ValueError("job does not exist")
    return value


def _save(target: Path, job: dict) -> None:
    job["updated_at"] = state.now()
    state.atomic_json(_job_path(target, job["run_id"], job["id"]), job)


def _inside(target: Path, relative: str, run_id: str) -> str:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("job paths must be project-relative and cannot traverse parent directories")
    normalized = path.as_posix()
    allowed = normalized.startswith("fm_agent/") or normalized.startswith(f"fm_agent_skill/probes/{run_id}/")
    if not allowed:
        raise ValueError("worker output must stay in fm_agent/ or its run-scoped probe directory")
    resolved = (target / path).resolve()
    if target not in resolved.parents and resolved != target:
        raise ValueError("job path escapes project")
    return normalized


def _outputs_exist(target: Path, job: dict) -> list[str]:
    return [item for item in job.get("required_outputs", []) if not (target / item).exists()]


def _validate_semantics(target: Path, job: dict) -> str | None:
    kind = job["type"]
    if kind in {"spec_batch", "reconcile_caller_info"}:
        artifacts = job.get("artifacts", [])
        if not artifacts:
            return "sidecar-writing job has no assigned artifacts"
        for rel in artifacts:
            ready, reason = state.sidecars_ready(target / "fm_agent" / "extracted_functions" / rel)
            if not ready:
                return f"invalid assigned sidecars for {rel}: {reason}"
    if kind == "phase_plan":
        phases = state.read_json(target / "fm_agent" / "phases.json", {})
        if not isinstance(phases, dict) or not isinstance(phases.get("phases"), list) or not phases["phases"]:
            return "phase-plan worker did not create a non-empty fm_agent/phases.json"
    if kind == "domain_context":
        ready, reason = state.specification_context_ready(target)
        if not ready:
            return reason
    return None


def _max_attempts(target: Path, payload: dict) -> int:
    config = state.read_json(state.plugin_dir(target) / "config.json", {})
    configured = config.get("retries", 5) if isinstance(config, dict) else 5
    bug_limit = config.get("bug_validation_max_attempts", 1) if isinstance(config, dict) else 1
    default = bug_limit if payload.get("type") == "bug_validate" else configured
    value = payload.get("max_attempts", default)
    if not isinstance(value, int) or value < 1:
        raise ValueError("max_attempts must be a positive integer")
    return value


def _record_failure(job: dict, failure_class: str, message: str | None) -> None:
    if failure_class not in FAILURE_CLASSES:
        raise ValueError(f"unsupported failure class: {failure_class}")
    job["failure_class"] = failure_class
    job["message"] = message or "worker failed"
    job["failed_at"] = state.now()
    if failure_class in RETRYABLE_FAILURES and job["attempts"] < job["max_attempts"]:
        job["status"] = "retryable"
    else:
        job["status"] = "failed"


def _job_is_complete(target: Path, job: dict) -> str | None:
    missing = _outputs_exist(target, job)
    if missing:
        return "missing required worker output: " + ", ".join(missing)
    return _validate_semantics(target, job)


def create(target: Path, payload: dict) -> dict:
    if not isinstance(payload, dict): raise ValueError("job JSON must be an object")
    run_id, job_id, kind = payload.get("run_id"), payload.get("id"), payload.get("type")
    _safe_id(run_id, "run id"); _safe_id(job_id, "job id")
    if kind not in JOB_TYPES: raise ValueError(f"unsupported job type: {kind}")
    path = _job_path(target, run_id, job_id)
    if path.exists(): raise ValueError("job already exists")
    deps = payload.get("depends_on", [])
    outputs = payload.get("required_outputs", [])
    artifacts = payload.get("artifacts", [])
    if not all(isinstance(item, str) and ID.fullmatch(item) for item in deps): raise ValueError("invalid dependency id")
    if not all(isinstance(item, str) for item in outputs + artifacts): raise ValueError("job paths must be strings")
    normalized_outputs = [_inside(target, item, run_id) for item in outputs]
    if len(set(normalized_outputs)) != len(normalized_outputs): raise ValueError("duplicate required output")
    if kind == "incremental_spec_plan" and normalized_outputs:
        raise ValueError("incremental spec planning returns a plan; it must not write artifacts")
    job = {
        "schema_version": 1, "id": job_id, "run_id": run_id, "type": kind,
        "status": "queued", "depends_on": deps, "required_outputs": normalized_outputs,
        "artifacts": [_artifact_path(item) for item in artifacts], "attempts": 0,
        "max_attempts": _max_attempts(target, payload),
        "created_at": state.now(), "updated_at": state.now(),
    }
    if isinstance(payload.get("input"), dict): job["input"] = payload["input"]
    _save(target, job)
    return job


def _artifact_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact paths must stay under extracted_functions")
    return path.as_posix()


def ready(target: Path, run_id: str) -> list[dict]:
    _safe_id(run_id, "run id")
    root = state.plugin_dir(target) / "runs" / run_id / "jobs"
    result = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        job = state.read_json(path, {})
        if not isinstance(job, dict) or job.get("status") != "queued": continue
        try:
            dependencies = [_load(target, run_id, item) for item in job.get("depends_on", [])]
        except ValueError:
            continue
        if all(item.get("status") == "succeeded" for item in dependencies): result.append(job)
    return result


def transition(target: Path, run_id: str, job_id: str, action: str, result: dict | None, message: str | None, failure_class: str) -> dict:
    job = _load(target, run_id, job_id)
    # Scheduler job manifests created before retry support remain resumable.
    job.setdefault("max_attempts", _max_attempts(target, job))
    if action == "start":
        if job["status"] != "queued": raise ValueError("only a queued job can start")
        if job not in ready(target, run_id): raise ValueError("job dependencies are not complete")
        job["status"] = "running"; job["attempts"] += 1; job["started_at"] = state.now()
    elif action == "complete":
        if job["status"] != "running": raise ValueError("only a running job can complete")
        error = _job_is_complete(target, job)
        if error:
            _record_failure(job, "output", error)
        else:
            job["status"] = "succeeded"; job["completed_at"] = state.now()
            if result is not None: job["result"] = result
    elif action == "fail":
        if job["status"] not in {"queued", "running"}: raise ValueError("only queued or running jobs can fail")
        _record_failure(job, failure_class, message)
    else:  # retry
        if job["status"] != "retryable": raise ValueError("only a retryable job can be requeued")
        if job["attempts"] >= job["max_attempts"]:
            job["status"] = "failed"; job["failed_at"] = state.now(); _save(target, job); return job
        job["status"] = "queued"; job["requeued_at"] = state.now()
    _save(target, job)
    return job


def recover(target: Path, run_id: str) -> dict:
    """Reconcile jobs left running by an interrupted Coordinator process."""
    _safe_id(run_id, "run id")
    root = state.plugin_dir(target) / "runs" / run_id / "jobs"
    recovered, retryable = [], []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        job = state.read_json(path, {})
        if not isinstance(job, dict) or job.get("run_id") != run_id or job.get("status") != "running": continue
        job.setdefault("max_attempts", _max_attempts(target, job))
        error = _job_is_complete(target, job)
        if error:
            _record_failure(job, "interrupted", error)
            if job["status"] == "retryable": retryable.append(job["id"])
        else:
            job["status"] = "succeeded"; job["completed_at"] = state.now(); job["recovered"] = True; recovered.append(job["id"])
        _save(target, job)
    return {"recovered_succeeded": recovered, "retryable": retryable}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record and validate FM-Agent Claude subagent jobs.")
    parser.add_argument("action", choices=("create", "ready", "start", "complete", "fail", "retry", "recover", "show"))
    parser.add_argument("--project", required=True); parser.add_argument("--run-id", required=True)
    parser.add_argument("--job-id"); parser.add_argument("--job-json"); parser.add_argument("--result-json"); parser.add_argument("--message")
    parser.add_argument("--failure-class", choices=tuple(sorted(FAILURE_CLASSES)), default="execution")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "create":
            if not args.job_json: raise ValueError("--job-json is required")
            payload = json.loads(args.job_json)
            if not isinstance(payload, dict) or payload.get("run_id") != args.run_id:
                raise ValueError("job JSON run_id must equal --run-id")
            response = create(target, payload)
        elif args.action == "ready": response = {"jobs": ready(target, args.run_id)}
        elif args.action == "recover": response = recover(target, args.run_id)
        elif args.action == "show":
            if not args.job_id: raise ValueError("--job-id is required")
            response = _load(target, args.run_id, args.job_id)
        else:
            if not args.job_id: raise ValueError("--job-id is required")
            result = json.loads(args.result_json) if args.result_json else None
            response = transition(target, args.run_id, args.job_id, args.action, result, args.message, args.failure_class)
        print(json.dumps(response, ensure_ascii=False, indent=2))
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); sys.exit(2)


if __name__ == "__main__": main()
