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
    if turn_budget_remaining is not None and turn_budget_remaining < 2000:
        phase = earliest_incomplete(target)
        manifest = checkpoint.commit(target, phase, "running", active_record=state.active_record(target))
        return {
            "action": "checkpoint_and_yield", "dispatches": [],
            "checkpoint_id": manifest["checkpoint_id"], "earliest_incomplete_phase": phase,
        }
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Return bounded FM-Agent Coordinator actions.")
    parser.add_argument("action", choices=("next", "checkpoint"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--turn-budget-remaining", type=int)
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "checkpoint":
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
