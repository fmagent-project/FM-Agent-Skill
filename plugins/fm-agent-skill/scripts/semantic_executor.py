#!/usr/bin/env python3
"""Deterministic bridge between FM-Agent jobs and host-native semantic workers.

This script never invokes Claude, Codex, or another model.  It owns the fragile
parts of semantic dispatch that must not be reconstructed in an ad-hoc host
Workflow: scheduler leases, exact worker identity, assigned paths, deterministic
low-confidence verification, receipt validation, and retry state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from _common import project, state
import scheduler


SUPPORTED_PHASES = {"specification", "verification", "verify_affected"}
WORKERS = {
    "domain_context": "fm-domain-context-worker",
    "spec_batch": "fm-spec-batch-worker",
    "verify_function": "fm-verify-function-worker",
}


def _active(target: Path, phase: str) -> dict:
    if phase not in SUPPORTED_PHASES:
        raise ValueError(f"semantic executor does not support phase: {phase}")
    record = state.active_record(target)
    if not isinstance(record, dict) or record.get("status") != "running":
        raise ValueError("semantic execution requires a running FM-Agent analysis")
    if record.get("current_phase") != phase:
        raise ValueError(f"cannot execute {phase} while current phase is {record.get('current_phase')}")
    if record.get("snapshot_commit") != state.current_snapshot_commit(target):
        raise ValueError("active analysis snapshot does not match the current worktree")
    plan = state.read_json(state.control_dir(target) / "job_plans" / f"{phase}.json", None)
    if not isinstance(plan, dict) or plan.get("phase") != phase or plan.get("snapshot_commit") != record["snapshot_commit"]:
        raise ValueError(f"create the deterministic {phase} job plan before semantic execution")
    return record


def _phase_jobs(target: Path, phase: str) -> list[dict]:
    root = state.skill_dir(target) / "jobs"
    jobs = [state.read_json(path, {}) for path in sorted(root.glob("*.json"))] if root.is_dir() else []
    return [job for job in jobs if isinstance(job, dict) and job.get("phase") == phase]


def _verification_expected(target: Path, phase: str) -> set[str]:
    plan = state.read_json(state.control_dir(target) / "job_plans" / f"{phase}.json", {})
    return {
        f"fm_agent/logic_verification_results/{Path(entry['artifact']).with_suffix('.json').as_posix()}"
        for entry in plan.get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("artifact"), str)
    }


def _remove_unassigned_verification_results(target: Path, phase: str) -> list[str]:
    if phase not in {"verification", "verify_affected"}:
        return []
    expected = _verification_expected(target, phase)
    root = state.fm_dir(target) / "logic_verification_results"
    removed = []
    for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
        rel = path.relative_to(target).as_posix()
        if rel not in expected:
            path.unlink()
            removed.append(rel)
    return removed


def _function(target: Path, artifact: str) -> dict:
    index = state.source_index(target) or {}
    item = next(
        (entry for entry in index.get("functions", []) if isinstance(entry, dict) and entry.get("artifact") == artifact),
        None,
    )
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise ValueError(f"verification artifact is absent from the current analysis index: {artifact}")
    return item


def _low_confidence_result(target: Path, job: dict) -> bool:
    if job.get("type") != "verify_function" or job.get("status") != "queued":
        return False
    artifacts = job.get("artifacts", [])
    if len(artifacts) != 1:
        raise ValueError(f"Verification job {job.get('id')} does not own exactly one artifact")
    rel = artifacts[0]
    artifact = state.fm_dir(target) / "extracted_functions" / rel
    if state.spec_confidence(artifact) != "low":
        return False
    item = _function(target, rel)
    outputs = job.get("required_outputs", [])
    if len(outputs) != 1:
        raise ValueError(f"Verification job {job['id']} does not own exactly one result")
    result = {
        "schema_version": 2,
        "function_id": item["id"],
        "snapshot_commit": state.current_snapshot_commit(target),
        "verdict": "INCONCLUSIVE",
        "reasoning": None,
        "gaps": {
            "missing_evidence": ["independent normative behavioral contract"],
            "reason": "The specification is low-confidence and contains only implementation observations; A-to-B verification cannot establish MATCH or MISMATCH.",
        },
        "error": None,
    }
    state.atomic_json(target / outputs[0], result)
    scheduler.transition(target, job["id"], "start", None, None, "execution")
    completed = scheduler.transition(target, job["id"], "complete", {
        "job_id": job["id"],
        "status": "completed",
        "outputs": outputs,
        "verdict": "INCONCLUSIVE",
        "counts": {"deterministic_shortcut": 1},
        "summary": "Deterministic low-confidence shortcut; no semantic Worker was launched.",
    }, None, "execution")
    if completed.get("status") != "succeeded":
        raise ValueError(completed.get("message", f"failed to complete deterministic shortcut for {job['id']}"))
    return True


def prepare(target: Path, phase: str) -> dict:
    _active(target, phase)
    removed = _remove_unassigned_verification_results(target, phase)
    deterministic_retries = 0
    for job in _phase_jobs(target, phase):
        if job.get("type") != "verify_function" or job.get("status") != "retryable" or len(job.get("artifacts", [])) != 1:
            continue
        artifact = state.fm_dir(target) / "extracted_functions" / job["artifacts"][0]
        if state.spec_confidence(artifact) == "low":
            scheduler.transition(target, job["id"], "retry", None, None, "execution")
            deterministic_retries += 1
    short_circuited = 0
    # Dependencies can become ready as earlier deterministic jobs complete, so
    # repeat until no further low-confidence verification can be admitted.
    while True:
        progressed = False
        ready_ids = {job["id"] for job in scheduler.ready(target)}
        for job in _phase_jobs(target, phase):
            if job.get("id") in ready_ids and _low_confidence_result(target, job):
                short_circuited += 1
                progressed = True
        if not progressed:
            break
    jobs = _phase_jobs(target, phase)
    totals = {status: sum(1 for job in jobs if job.get("status") == status) for status in ("queued", "running", "retryable", "failed", "succeeded")}
    return {
        "phase": phase,
        "removed_unassigned_results": removed,
        "deterministic_retries": deterministic_retries,
        "short_circuited": short_circuited,
        "worker_jobs_remaining": totals["queued"] + totals["running"] + totals["retryable"],
        "totals": totals,
    }


def _relative(target: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(target.resolve()).as_posix()
    except ValueError:
        return None


def _source_for_artifact(target: Path, artifact: str) -> str | None:
    manifest = state.read_json(state.fm_dir(target) / "extraction_manifest.json", {})
    item = next(
        (entry for entry in manifest.get("functions", []) if isinstance(entry, dict) and entry.get("artifact") == artifact),
        None,
    )
    source = item.get("source_path") if isinstance(item, dict) else None
    return source if isinstance(source, str) else None


def _read_paths(target: Path, job: dict) -> list[str]:
    paths = {
        f"fm_agent_skill/jobs/{job['id']}.json",
        "fm_agent_skill/control/analysis_index.json",
    }
    kind = job["type"]
    if kind == "domain_context":
        paths.add("fm_agent/phases.json")
        phases = state.read_json(state.fm_dir(target) / "phases.json", {})
        for phase in phases.get("phases", []):
            for module in phase.get("modules", []) if isinstance(phase, dict) else []:
                paths.update(path for path in module.get("source_files", []) if isinstance(path, str))
    elif kind == "spec_batch":
        paths.update({
            "fm_agent/spec_prompts/system_prompt.md",
            "fm_agent/spec_prompts/domain_context/engine_overview.txt",
            "fm_agent/spec_prompts/domain_context/user_knowledge/manifest.json",
        })
        for artifact in job.get("artifacts", []):
            paths.add(f"fm_agent/extracted_functions/{artifact}")
            source = _source_for_artifact(target, artifact)
            if source:
                paths.add(source)
        for candidate in (state.fm_dir(target) / "spec_prompts").glob("phase_*_topdown_layers.json"):
            payload = state.read_json(candidate, {})
            encoded = json.dumps(payload, ensure_ascii=False)
            if any(artifact in encoded for artifact in job.get("artifacts", [])):
                rel = _relative(target, candidate)
                if rel:
                    paths.add(rel)
        for candidate in (state.fm_dir(target) / "spec_prompts" / "domain_context").glob("phase_*_types.txt"):
            rel = _relative(target, candidate)
            if rel:
                paths.add(rel)
        manifest = state.read_json(target / "fm_agent/spec_prompts/domain_context/user_knowledge/manifest.json", {})
        paths.update(item["copied_path"] for item in manifest.get("entries", []) if isinstance(item, dict) and isinstance(item.get("copied_path"), str))
    elif kind == "verify_function":
        artifact = job["artifacts"][0]
        paths.update({
            f"fm_agent/extracted_functions/{artifact}",
            f"fm_agent/extracted_functions/{artifact}.spec.json",
            f"fm_agent/extracted_functions/{artifact}.info.json",
        })
    return sorted(path for path in paths if (target / path).exists())


def _ticket(target: Path, job: dict) -> dict:
    worker = WORKERS.get(job.get("type"))
    if worker is None:
        raise ValueError(f"job type must use its dedicated executor: {job.get('type')}")
    definition = Path(__file__).resolve().parents[1] / "agents" / f"{worker}.md"
    if not definition.is_file():
        raise ValueError(f"registered worker definition is missing: {worker}")
    ticket = {
        "schema_version": 1,
        "job_id": job["id"],
        "phase": job["phase"],
        "worker": worker,
        "worker_definition": str(definition),
        "worker_definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
        "project": str(target),
        "job_manifest": f"fm_agent_skill/jobs/{job['id']}.json",
        "read_paths": _read_paths(target, job),
        "write_paths": list(job.get("required_outputs", [])),
    }
    path = state.control_dir(target) / "dispatches" / f"{job['id']}.json"
    state.atomic_json(path, ticket)
    return {"ticket_path": path.relative_to(target).as_posix(), **ticket}


def dispatch(target: Path, phase: str, limit: int) -> dict:
    _active(target, phase)
    if limit < 1:
        raise ValueError("dispatch limit must be positive")
    available = [job for job in scheduler.admissible(target)["jobs"] if job.get("phase") == phase]
    actions = []
    for job in available[:limit]:
        started = scheduler.transition(target, job["id"], "start", None, None, "execution")
        actions.append(_ticket(target, started))
    return {
        "action": "host_workers" if actions else "wait_or_finish",
        "phase": phase,
        "dispatches": actions,
        "instruction": "Invoke each registered worker exactly. If the host cannot select it by name, launch a fresh subagent whose only instruction is to read the ticket and worker_definition completely and execute them; never paraphrase either contract or generate a Workflow script.",
    }


def _checked_ticket(target: Path, job_id: str) -> dict:
    job = scheduler._load(target, job_id)
    ticket_path = state.control_dir(target) / "dispatches" / f"{job_id}.json"
    ticket = state.read_json(ticket_path, None)
    worker = WORKERS.get(job.get("type"))
    if not isinstance(ticket, dict) or ticket.get("job_id") != job_id or ticket.get("worker") != worker:
        raise ValueError("job has no valid deterministic dispatch ticket")
    definition = Path(ticket.get("worker_definition", ""))
    if not definition.is_file() or hashlib.sha256(definition.read_bytes()).hexdigest() != ticket.get("worker_definition_sha256"):
        raise ValueError("worker definition changed after the job was dispatched")
    if ticket.get("write_paths") != job.get("required_outputs"):
        raise ValueError("dispatch ticket output paths do not match the job manifest")
    return job


def submit(target: Path, job_id: str, receipt: dict) -> dict:
    job = _checked_ticket(target, job_id)
    if job.get("status") != "running":
        raise ValueError("only a dispatched running job can be submitted")
    completed = scheduler.transition(target, job_id, "complete", receipt, None, "execution")
    return {
        "action": "completed" if completed.get("status") == "succeeded" else "retry_required",
        "job": completed,
    }


def fail(target: Path, job_id: str, failure_class: str, message: str | None) -> dict:
    _checked_ticket(target, job_id)
    failed = scheduler.transition(target, job_id, "fail", None, message, failure_class)
    return {"action": "retry_required" if failed.get("status") == "retryable" else "phase_failed", "job": failed}


def retry(target: Path, job_id: str) -> dict:
    retried = scheduler.transition(target, job_id, "retry", None, None, "execution")
    return {"action": "requeued", "job": retried}


def main() -> None:
    parser = argparse.ArgumentParser(description="Lease and validate exact FM-Agent semantic workers without invoking a model.")
    parser.add_argument("action", choices=("prepare", "dispatch", "submit", "fail", "retry", "phase-receipt"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--phase", choices=tuple(sorted(SUPPORTED_PHASES)))
    parser.add_argument("--job-id")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--receipt-json")
    parser.add_argument("--failure-class", choices=tuple(sorted(scheduler.FAILURE_CLASSES)), default="execution")
    parser.add_argument("--message")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action in {"prepare", "dispatch", "phase-receipt"} and not args.phase:
            raise ValueError(f"{args.action} requires --phase")
        if args.action in {"submit", "fail", "retry"} and not args.job_id:
            raise ValueError(f"{args.action} requires --job-id")
        if args.action == "prepare":
            result = prepare(target, args.phase)
        elif args.action == "dispatch":
            result = dispatch(target, args.phase, args.limit)
        elif args.action == "submit":
            if not args.receipt_json:
                raise ValueError("submit requires --receipt-json")
            result = submit(target, args.job_id, json.loads(args.receipt_json))
        elif args.action == "fail":
            result = fail(target, args.job_id, args.failure_class, args.message)
        elif args.action == "retry":
            result = retry(target, args.job_id)
        else:
            result = scheduler.phase_receipt(target, args.phase)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
