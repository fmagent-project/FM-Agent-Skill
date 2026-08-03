#!/usr/bin/env python3
"""Block Claude from stopping an unfinished Bug Validation run."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


# These states mean that the Coordinator still owns an unfinished run.  A
# failed phase is deliberately excluded: pipeline.py fail() has already made
# that run terminal and the user must be allowed to inspect the failure.
CONTINUABLE_STATUSES = frozenset({"running", "resumable", "interrupted"})


def requires_continuation(active: object) -> bool:
    """Return whether an active record still represents unfinished work."""
    return isinstance(active, dict) and active.get("status") in CONTINUABLE_STATUSES


def output(decision: str, reason: str, message: str) -> None:
    print(json.dumps({"decision": decision, "reason": reason, "systemMessage": message}, ensure_ascii=False))


def read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def block(reason: str, message: str) -> int:
    output("block", reason, message)
    return 0


def supervisor_call(plugin_root: str, action: str, project: Path, session_id: str | None = None) -> dict:
    supervisor = Path(plugin_root) / "hooks" / "continuation_supervisor.py"
    if not supervisor.is_file():
        return {"status": "failed", "reason": "continuation supervisor is missing"}
    try:
        command = [sys.executable, str(supervisor), action, "--project", str(project)]
        if session_id:
            command.extend(["--session-id", session_id])
        completed = subprocess.run(
            command,
            text=True, capture_output=True, timeout=20, check=False,
        )
        value = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "reason": f"continuation supervisor failed: {exc}"}
    if completed.returncode != 0 or not isinstance(value, dict):
        return {"status": "failed", "reason": value.get("reason", "continuation supervisor returned invalid state") if isinstance(value, dict) else "invalid supervisor output"}
    return value


def continuation_decision(plugin_root: str, project: Path, stop_hook_active: bool, session_id: str | None = None) -> int:
    """Create/launch one native-host continuation and report the hook result."""
    supervisor = supervisor_call(plugin_root, "launch" if stop_hook_active else "ensure", project, session_id)
    if stop_hook_active and supervisor.get("status") in {"started", "launched"}:
        output("approve", "Native continuation supervisor accepted", "The next turn was handed to the installed Claude/Codex CLI; the durable checkpoint remains authoritative.")
        return 0
    if supervisor.get("status") in {"pending", "starting", "launching"}:
        return block("FM-Agent continuation ticket is pending", "The host continuation supervisor must launch the next native Claude/Codex turn before this run may stop.")
    return block("FM-Agent continuation could not be accepted", str(supervisor.get("reason", "native continuation was not launched")))


def source_active_record(project: Path) -> dict:
    """Find durable active state even when the disposable marker was deleted."""
    candidates = (
        project / "fm_agent_skill" / "active.json",
        project / "fm_agent_skill" / "checkpoint" / "active.json",
        project / "fm_agent" / "analysis_status.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        value = read_json(path, None)
        if value is None:
            return {"__invalid__": True, "__path__": str(path)}
        if isinstance(value, dict) and value:
            if value.get("status") == "in_progress":
                value = {**value, "status": "running"}
            return value
    return {}


def resolve_project(project: Path) -> tuple[Path | None, str | None]:
    """Resolve the active snapshot, accepting only our private /tmp shape."""
    marker_path = project / "fm_agent_skill" / "isolation.json"
    if not marker_path.is_file():
        return None, "isolation marker is missing"
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
    try:
        hook_input = json.load(sys.stdin)
    except (ValueError, OSError):
        hook_input = {}
    stop_hook_active = isinstance(hook_input, dict) and hook_input.get("stop_hook_active") is True
    hook_session_id = None
    if isinstance(hook_input, dict):
        for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
            if isinstance(hook_input.get(key), str) and hook_input[key]:
                hook_session_id = hook_input[key]
                break
        session = hook_input.get("session")
        if hook_session_id is None and isinstance(session, dict) and isinstance(session.get("id"), str):
            hook_session_id = session["id"]
    project_value = os.environ.get("CLAUDE_PROJECT_DIR")
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not project_value or not plugin_root:
        output("approve", "FM-Agent project context is unavailable", "No active FM-Agent project context was found.")
        return 0
    source_project = Path(project_value).expanduser().resolve()
    project = source_project
    # Ordinary Claude projects have no FM-Agent marker and remain stoppable.
    # Every continuable FM-Agent phase, however, requires isolation metadata;
    # never fall back to the original project directory.
    source_active = source_active_record(project)
    if source_active.get("__invalid__"):
        return block("FM-Agent active state is unreadable", "Cannot inspect durable active state before resolving isolation: " + source_active.get("__path__", "unknown"))
    marker_path = project / "fm_agent_skill" / "isolation.json"
    if not marker_path.is_file():
        if requires_continuation(source_active):
            return block("FM-Agent isolation marker is missing", "An unfinished FM-Agent run has no fm_agent_skill/isolation.json; refusing to inspect or stop against the original project.")
        output("approve", "no active isolated FM-Agent run", "No active isolated FM-Agent run requires continuation.")
        return 0
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
    phase = active.get("current_phase")
    if not requires_continuation(active):
        output("approve", "FM-Agent run is terminal", "No active FM-Agent phase requires continuation.")
        return 0
    if phase != "bug_validation":
        # Stages 1–7 are streaming.  In a normal run pipeline.py complete
        # sets status=idle after the final stage-7 gate, so
        # requires_continuation already returns False at line 166 and we
        # never reach here.  Reaching here means the phase itself is still
        # active — launch the native CLI to continue it.
        # Before continuing, verify no stale Bug Validation jobs are lurking
        # from a prior interrupted batched run.
        barrier = Path(plugin_root) / "scripts" / "durable_executor.py"
        try:
            completed = subprocess.run(
                [sys.executable, str(barrier), "barrier", "--project", str(project)],
                text=True, capture_output=True, timeout=20, check=False,
            )
            bv_data = json.loads(completed.stdout) if completed.stdout.strip() else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            bv_data = {}
        if isinstance(bv_data, dict) and bv_data.get("action") not in {"dag_converged", "noop", None} and not (bv_data.get("dag_converged") is True and not bv_data.get("pending")):
            return block("FM-Agent stale Bug Validation jobs exist", "A prior Bug Validation run has unfinished jobs; resume or explicitly fail that phase before starting a new run.")
        return continuation_decision(plugin_root, source_project, stop_hook_active, hook_session_id)
    # The scheduler ledger is deliberately durable outside the temporary
    # snapshot (checkpoint.root() resolves the source project from the
    # validated isolation marker).  Do not inspect a guessed path inside the
    # snapshot: doing so produces a false "state missing" error even while
    # the barrier can safely locate the source ledger.  A missing/corrupt
    # ledger is still a hard stop because the barrier below rejects it.
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
    if phase == "bug_validation" and (action in {"wait_for_completion_event", "dispatch"} or data.get("dag_converged") is False):
        return continuation_decision(plugin_root, source_project, stop_hook_active, hook_session_id)
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
    if phase == "bug_validation" and action == "dag_converged" and data.get("dag_converged") is True and not data.get("pending"):
        output("approve", "Bug Validation barrier converged", "All Bug Validator jobs reached a legal terminal state.")
        return 0
    if phase == "bug_validation" and action == "noop":
        output("approve", "Bug Validation was never started", "No Bug Validation jobs were created; the analysis completed with static findings only.")
        return 0
    if phase == "bug_validation" and action not in {"phase_failed"}:
        return block("FM-Agent barrier is non-terminal or malformed", "Only dag_converged permits Bug Validation to end; preserve the current job_id and attempt.")

    # The first Stop event records a ticket and blocks. On hook re-entry the
    # supervisor starts a native Claude/Codex continuation, and only then may
    # this turn be approved. `stop_hook_active` is therefore never a blanket
    # approval path.
    return continuation_decision(plugin_root, source_project, stop_hook_active, hook_session_id)


if __name__ == "__main__":
    raise SystemExit(main())
