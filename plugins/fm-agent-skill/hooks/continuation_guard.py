#!/usr/bin/env python3
"""Block Claude from stopping an unfinished Bug Validation run."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def output(decision: str, reason: str, message: str) -> None:
    print(json.dumps({"decision": decision, "reason": reason, "systemMessage": message}, ensure_ascii=False))


def block(reason: str, message: str) -> int:
    output("block", reason, message)
    return 0


def resolve_project(project: Path) -> tuple[Path | None, str | None]:
    """Resolve the active snapshot, accepting only our private /tmp shape."""
    marker_path = project / "fm_agent_skill" / "isolation.json"
    if not marker_path.is_file():
        return project, None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"isolation marker is unreadable: {exc}"
    snapshot = marker.get("snapshot") if isinstance(marker, dict) else None
    if not isinstance(snapshot, str) or not snapshot:
        return None, "isolation marker has no snapshot path"
    try:
        resolved = Path(snapshot).expanduser().resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if not resolved.is_dir() or not resolved.is_relative_to(temp_root):
            return None, "snapshot is outside the private temporary root"
        if resolved.name != "project" or not resolved.parent.name.startswith("fm-agent-skill-worktree-"):
            return None, "snapshot path is not an FM-Agent private worktree"
    except (OSError, RuntimeError) as exc:
        return None, f"snapshot path cannot be validated: {exc}"
    return resolved, None


def main() -> int:
    # Claude may re-enter Stop hooks after a blocking decision. Honor the
    # marker once to prevent an infinite hook loop; the durable barrier remains
    # authoritative for the next Coordinator turn.
    try:
        hook_input = json.load(sys.stdin)
    except (ValueError, OSError):
        hook_input = {}
    if isinstance(hook_input, dict) and hook_input.get("stop_hook_active") is True:
        output("approve", "Stop hook recursion guard", "Stop hook already blocked this turn; preserve the durable run for the next continuation turn.")
        return 0
    project_value = os.environ.get("CLAUDE_PROJECT_DIR")
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not project_value or not plugin_root:
        output("approve", "FM-Agent project context is unavailable", "No active FM-Agent project context was found.")
        return 0
    project = Path(project_value).expanduser().resolve()
    project, resolve_error = resolve_project(project)
    if resolve_error:
        return block("FM-Agent snapshot validation failed", resolve_error)
    if project is None:
        return block("FM-Agent project cannot be located", "The active analysis worktree could not be located safely.")
    active_path = project / "fm_agent_skill" / "active.json"
    if not active_path.is_file():
        output("approve", "no active FM-Agent run", "No active FM-Agent run requires continuation.")
        return 0
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return block("FM-Agent active state is unreadable", "The active analysis state is damaged; do not stop until it is diagnosed.")
    if not isinstance(active, dict):
        return block("FM-Agent active state is invalid", "The active analysis state is not a JSON object.")
    if active.get("current_phase") != "bug_validation":
        output("approve", "Bug Validation is not the current phase", "The current FM-Agent phase does not require the Bug Validation barrier.")
        return 0
    if active.get("status") not in {"running", "resumable", "failed", "interrupted"}:
        return block("FM-Agent Bug Validation state is invalid", "Bug Validation is current but its active run status is not resumable.")
    database = project / "fm_agent_skill" / "checkpoint" / "state.db"
    if not database.is_file():
        return block("FM-Agent scheduler state is missing", "The Bug Validation state database cannot be located safely.")
    barrier = Path(plugin_root) / "scripts" / "durable_executor.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(barrier), "barrier", "--project", str(project)],
            text=True, capture_output=True, timeout=20, check=False,
        )
        data = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return block("FM-Agent barrier check failed", f"The durable barrier could not be verified: {exc}")
    if completed.returncode != 0 or not isinstance(data, dict):
        return block("FM-Agent barrier returned an error", data.get("error", "The barrier returned invalid state.") if isinstance(data, dict) else "invalid barrier output")
    action = data.get("action")
    if action in {"wait_for_completion_event", "dispatch"} or data.get("dag_converged") is False:
        pending = data.get("pending", [])
        output(
            "block",
            "Bug Validation is not converged",
            "Do not stop the FM-Agent run. Continue the exact pending Bug Validator tickets, submit their receipts, and re-run durable_executor.py barrier. Pending: "
            + json.dumps(pending, ensure_ascii=False),
        )
        return 0
    if action == "phase_failed":
        report_path = project / "fm_agent_skill" / "control" / "terminal_report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        except (OSError, ValueError) as exc:
            return block("phase_failed report is unreadable", f"Run terminal_report.py and persist an incomplete report before stopping: {exc}")
        if (
            isinstance(report, dict)
            and report.get("status") == "incomplete"
            and report.get("official_result_available") is False
            and report.get("snapshot_commit") == active.get("snapshot_commit")
            and report.get("analysis_fingerprint") == active.get("fingerprint")
        ):
            output("approve", "Bug Validation exhausted its retry budget", "An incomplete phase-failure report is persisted; stop and report only that failure.")
            return 0
        return block("phase_failed report is stale or incomplete", "Run terminal_report.py for the current snapshot and analysis instance; persist status=incomplete before stopping.")
    if action == "dag_converged" and data.get("dag_converged") is True and not data.get("pending"):
        output("approve", "Bug Validation barrier converged", "All Bug Validator jobs reached a legal terminal state.")
        return 0
    return block("FM-Agent barrier is non-terminal or malformed", "Only dag_converged permits Bug Validation to end; preserve the current job_id and attempt.")


if __name__ == "__main__":
    raise SystemExit(main())
