#!/usr/bin/env python3
"""Deterministic bridge between FM-Agent jobs and host-native semantic workers.

This script never invokes Claude, Codex, or another model.  It owns the fragile
parts of semantic dispatch that must not be reconstructed in an ad-hoc host
Workflow: scheduler leases, exact worker identity, assigned paths, deterministic
dispatch, receipt validation, and retry state. Every verification job is sent
to FM-Agent's semantic Worker; this bridge never substitutes a confidence-based
verdict for the A-to-B reasoner.
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
    "domain_context": "fm-agent-skill:fm-domain-context-worker",
    "spec_batch": "fm-agent-skill:fm-spec-batch-worker",
    "verify_function": "fm-agent-skill:fm-verify-function-worker",
    "verify_batch": "fm-agent-skill:fm-verify-function-worker",
}
PUBLIC_INTERFACE_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".inc", ".inl"}


def _active(target: Path, phase: str) -> dict:
    if phase not in SUPPORTED_PHASES:
        raise ValueError(f"semantic executor does not support phase: {phase}")
    record = state.active_record(target)
    if not isinstance(record, dict) or record.get("status") != "running":
        raise ValueError("semantic execution requires a running FM-Agent analysis")
    if phase != record.get("current_phase") and phase not in record.get("phases", []) and not (
        phase == "verification" and record.get("mode") == "full"
        or phase == "verify_affected" and record.get("mode") == "incremental"
    ):
        raise ValueError(f"phase is not part of the active DAG: {phase}")
    if record.get("snapshot_commit") != state.current_snapshot_commit(target):
        raise ValueError("active analysis snapshot does not match the current worktree")
    plan = state.read_json(state.control_dir(target) / "job_plans" / f"{phase}.json", None)
    if not isinstance(plan, dict) or plan.get("phase") != phase or plan.get("snapshot_commit") != record["snapshot_commit"]:
        raise ValueError(f"create the deterministic {phase} job plan before semantic execution")
    return record


def _phase_jobs(target: Path, phase: str) -> list[dict]:
    return scheduler.jobs_for_phase(target, phase)


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


def prepare(target: Path, phase: str) -> dict:
    _active(target, phase)
    removed = _remove_unassigned_verification_results(target, phase)
    jobs = _phase_jobs(target, phase)
    totals = {status: sum(1 for job in jobs if job.get("status") == status) for status in ("queued", "running", "retryable", "failed", "succeeded")}
    return {
        "phase": phase,
        "removed_unassigned_results": removed,
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


def _related_artifacts(target: Path, artifacts: list[str]) -> set[str]:
    """Return direct callers/callees needed for FM-Agent top-down contracts."""
    selected = set(artifacts)
    related: set[str] = set()
    graph = state.read_json(state.control_dir(target) / "graph_edges.json", {})
    for edge in graph.get("edges", []) if isinstance(graph, dict) else []:
        if not isinstance(edge, dict):
            continue
        caller, callee = edge.get("caller_artifact"), edge.get("callee_artifact")
        if not isinstance(caller, str) or not isinstance(callee, str):
            continue
        if caller in selected:
            related.add(callee)
        if callee in selected:
            related.add(caller)
    return related - selected


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
            "fm_agent_skill/control/graph_edges.json",
        })
        for artifact in job.get("artifacts", []):
            paths.add(f"fm_agent/extracted_functions/{artifact}")
            source = _source_for_artifact(target, artifact)
            if source:
                paths.add(source)
        # Retry workers must be able to inspect their own existing pairs so a
        # valid artifact is preserved and only a rejected pair is rewritten.
        paths.update(path for path in job.get("required_outputs", []) if (target / path).exists())
        for candidate in (state.fm_dir(target) / "spec_prompts").glob("phase_*_topdown_layers.json"):
            payload = state.read_json(candidate, {})
            encoded = json.dumps(payload, ensure_ascii=False)
            if any(artifact in encoded for artifact in job.get("artifacts", [])):
                rel = _relative(target, candidate)
                if rel:
                    paths.add(rel)
                paths.update(
                    source for source in payload.get("source_files", [])
                    if isinstance(source, str) and Path(source).suffix.lower() in PUBLIC_INTERFACE_SUFFIXES
                )
        for artifact in _related_artifacts(target, job.get("artifacts", [])):
            extracted = f"fm_agent/extracted_functions/{artifact}"
            paths.add(extracted)
            paths.add(extracted + ".spec.json")
            paths.add(extracted + ".info.json")
            source = _source_for_artifact(target, artifact)
            if source:
                paths.add(source)
        for candidate in (state.fm_dir(target) / "spec_prompts" / "domain_context").glob("phase_*_types.txt"):
            rel = _relative(target, candidate)
            if rel:
                paths.add(rel)
        manifest = state.read_json(target / "fm_agent/spec_prompts/domain_context/user_knowledge/manifest.json", {})
        paths.update(item["copied_path"] for item in manifest.get("entries", []) if isinstance(item, dict) and isinstance(item.get("copied_path"), str))
    elif kind in {"verify_function", "verify_batch"}:
        for artifact in job["artifacts"]:
            paths.update({
                f"fm_agent/extracted_functions/{artifact}",
                f"fm_agent/extracted_functions/{artifact}.spec.json",
                f"fm_agent/extracted_functions/{artifact}.info.json",
            })
    return sorted(path for path in paths if (target / path).exists())


def _spec_repair_scope(target: Path, job: dict) -> tuple[list[str], list[str]]:
    if job.get("type") != "spec_batch":
        return [], []
    repair, preserve = [], []
    for artifact in job.get("artifacts", []):
        ready, _ = state.sidecars_ready(state.fm_dir(target) / "extracted_functions" / artifact)
        (preserve if ready else repair).append(artifact)
    return sorted(repair), sorted(preserve)


def _ticket(target: Path, job: dict) -> dict:
    worker = WORKERS.get(job.get("type"))
    if worker is None:
        raise ValueError(f"job type must use its dedicated executor: {job.get('type')}")
    definition_name = worker.split(":", 1)[-1]
    definition = Path(__file__).resolve().parents[1] / "agents" / f"{definition_name}.md"
    if not definition.is_file():
        raise ValueError(f"registered worker definition is missing: {worker}")
    repair_artifacts, preserve_artifacts = _spec_repair_scope(target, job)
    ticket = {
        "schema_version": 2,
        "job_id": job["id"],
        "attempt": job.get("attempts"),
        "input_hash": job.get("input_hash"),
        "phase": job["phase"],
        "worker": worker,
        "worker_definition": str(definition),
        "worker_definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
        "project": str(target),
        "job_manifest": f"fm_agent_skill/jobs/{job['id']}.json",
        "read_paths": _read_paths(target, job),
        "write_paths": list(job.get("required_outputs", [])),
    }
    if job.get("type") == "spec_batch":
        ticket.update({
            "repair_artifacts": repair_artifacts,
            "preserve_artifacts": preserve_artifacts,
            "validation_message": job.get("message") if job.get("attempts", 0) else None,
        })
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
    ticket = state.read_json(state.control_dir(target) / "dispatches" / f"{job_id}.json", {})
    completed = scheduler.transition(
        target, job_id, "complete", receipt, None, "execution",
        ticket.get("attempt"), ticket.get("input_hash"), None,
    )
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
