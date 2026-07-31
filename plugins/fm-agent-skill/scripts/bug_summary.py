#!/usr/bin/env python3
"""Write the current Bug Validator summary from completed result reports."""
from __future__ import annotations

import argparse
import json
import sys

from _common import project, state


def selected_ids(target, mode: str) -> set[str] | None:
    if mode == "full":
        return None
    decision = state.read_json(state.control_dir(target) / "incremental_decision.json", {})
    included = decision.get("included") if isinstance(decision, dict) else None
    if not isinstance(included, dict):
        raise ValueError("incremental decision has no included function map")
    return set(included)


def write_summary(target, mode: str) -> dict:
    candidates = state.direct_mismatch_ids(target, selected_ids(target, mode))
    root = state.fm_dir(target) / "bug_validation"
    reports = {}
    for path in root.glob("*.result.json") if root.is_dir() else []:
        report = state.read_json(path, None)
        function_id = report.get("function_id") if isinstance(report, dict) else None
        if not isinstance(function_id, str) or function_id in reports:
            raise ValueError("bug reports have missing or duplicate function identities")
        reports[function_id] = report
    if set(reports) != candidates:
        raise ValueError("bug reports do not match current direct MISMATCH candidates")
    counts = {"confirmed": 0, "rejected": 0, "inconclusive": 0}
    for function_id, report in reports.items():
        status = report.get("confirmation_status")
        if status not in counts:
            raise ValueError(f"bug report has invalid confirmation status for {function_id}")
        counts[status] += 1
    summary = {
        "schema_version": 1,
        "snapshot_commit": state.current_snapshot_commit(target),
        "total_candidates": len(candidates),
        "total_confirmed": counts["confirmed"],
        "total_rejected": counts["rejected"],
        "total_inconclusive": counts["inconclusive"],
        "generated_at": state.now(),
    }
    state.atomic_json(root / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a current-snapshot FM-Agent Bug Validator summary.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--mode", required=True, choices=("full", "incremental"))
    args = parser.parse_args()
    try:
        print(json.dumps(write_summary(project(args), args.mode), ensure_ascii=False, indent=2))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
