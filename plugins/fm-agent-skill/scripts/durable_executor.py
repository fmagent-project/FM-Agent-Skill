#!/usr/bin/env python3
"""Bounded, event-driven Coordinator actions over the durable DAG."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import project, state
import bug_validation_executor
import checkpoint
import scheduler
import semantic_executor


SEMANTIC_TYPES = {"domain_context", "spec_batch", "verify_function", "verify_batch"}


def earliest_incomplete(target: Path) -> str:
    record = state.active_record(target)
    connection = scheduler._connect(target)
    rows = connection.execute(
        "SELECT phase, SUM(CASE WHEN status='succeeded' THEN 0 ELSE 1 END) AS pending "
        "FROM jobs GROUP BY phase"
    ).fetchall()
    connection.close()
    pending = {row["phase"] for row in rows if row["pending"]}
    return next((phase for phase in record.get("phases", []) if phase in pending), record.get("current_phase", "finalize"))


def next_actions(target: Path, limit: int, offset: int = 0, turn_budget_remaining: int | None = None) -> dict:
    if limit < 1: raise ValueError("dispatch limit must be positive")
    # A durable checkpoint is an implementation detail, not a terminal
    # scheduler action. This script cannot start host subagents itself, so a
    # `checkpoint_and_yield` response used to make the Coordinator stop even
    # though ready work remained. Keep the argument for CLI compatibility,
    # but never convert an ordinary low-context signal into a halted run.
    _ = turn_budget_remaining
    admitted = scheduler.admissible(target)["jobs"]
    selected = admitted[offset:offset + limit]
    actions = []
    for job in selected:
        if job["type"] in SEMANTIC_TYPES:
            started = scheduler.transition(target, job["id"], "start", None, None, "execution")
            actions.append(semantic_executor._ticket(target, started))
        elif job["type"] == "bug_validate":
            scheduler.transition(target, job["id"], "start", None, None, "execution")
            actions.append(bug_validation_executor.next_action(target, job["id"]))
        else:
            # Non-semantic legacy workers retain their named contract.  The
            # ticket is compact and never grants a command outside assigned
            # read/write paths.
            started = scheduler.transition(target, job["id"], "start", None, None, "execution")
            actions.append({
                "action": "host_worker", "job_id": started["id"],
                "type": started["type"], "attempt": started["attempts"],
                "input_hash": started["input_hash"],
                "job_manifest": f"fm_agent_skill/jobs/{started['id']}.json",
                "write_paths": started.get("required_outputs", []),
            })
    aggregate = scheduler.aggregate(target)
    return {
        "action": "dispatch" if actions else "wait_for_completion_event" if aggregate["pending"] else "dag_converged",
        "dispatches": actions,
        "page": {"offset": offset, "limit": limit, "returned": len(actions)},
        "backpressure": scheduler._capacity(target),
        "earliest_incomplete_phase": earliest_incomplete(target),
        "scheduler": aggregate,
        "continuation": "submit each completion immediately, then call next again to fill the free slot",
    }


def barrier(target: Path, phase: str = "bug_validation") -> dict:
    """Return the machine-readable join point for a host-coordinated phase.

    This never infers completion from result files.  The scheduler status is
    authoritative; callers may read reports only after ``dag_converged``.
    """
    jobs = [
        job for job in scheduler.current_jobs_for_phase(target, phase)
        if job.get("type") == "bug_validate" and not job.get("legacy_contract")
    ]
    by_status = {
        status: sum(1 for job in jobs if job.get("status") == status)
        for status in ("queued", "running", "retryable", "failed", "succeeded")
    }
    pending = [
        {"job_id": job.get("id"), "status": job.get("status"), "attempt": job.get("attempts", 0)}
        for job in jobs if job.get("status") != "succeeded"
    ]
    if by_status["failed"]:
        action = "phase_failed"
    elif pending:
        action = "wait_for_completion_event"
    else:
        action = "dag_converged"
    return {
        "action": action,
        "phase": phase,
        "dag_converged": action == "dag_converged",
        "jobs": by_status,
        "pending": pending,
        "rule": "Only dag_converged permits bug_summary.py and pipeline phase-complete.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Return bounded FM-Agent Coordinator actions.")
    parser.add_argument("action", choices=("next", "barrier", "checkpoint"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--turn-budget-remaining", type=int)
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "barrier":
            result = barrier(target)
        elif args.action == "checkpoint":
            phase = earliest_incomplete(target)
            result = checkpoint.commit(target, phase, "running", active_record=state.active_record(target))
        else:
            result = next_actions(target, args.limit, args.offset, args.turn_budget_remaining)
        code = 0
    except (ValueError, RuntimeError, OSError) as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
