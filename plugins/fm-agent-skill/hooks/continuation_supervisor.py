#!/usr/bin/env python3
"""Durable native Claude/Codex continuation supervisor.

This process is only a workflow bridge.  It never calls a model API.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

CONTINUABLE = {"running", "resumable", "interrupted"}
ACTIVE_TICKETS = {"pending", "starting", "started", "launching", "launched"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def ticket_path(project: Path) -> Path:
    return project / "fm_agent_skill" / "control" / "continuation" / "active.json"


@contextlib.contextmanager
def ticket_lock(project: Path):
    lock = ticket_path(project).with_name("active.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def active_record(project: Path) -> dict:
    for path in (project / "fm_agent_skill" / "active.json", project / "fm_agent_skill" / "checkpoint" / "active.json", project / "fm_agent" / "analysis_status.json"):
        value = read_json(path, {})
        if isinstance(value, dict) and value:
            return {**value, "status": "running"} if value.get("status") == "in_progress" else value
    return {}


def ticket(project: Path) -> dict:
    value = read_json(ticket_path(project), {})
    return value if isinstance(value, dict) else {}


def update_ticket(project: Path, updates: dict) -> dict:
    with ticket_lock(project):
        value = ticket(project)
        value.update(updates)
        atomic_json(ticket_path(project), value)
        return value


def _session_id(value: str | None) -> str | None:
    return value or os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CODEX_SESSION_ID") or None


def _ensure_ticket_unlocked(project: Path, session_id: str | None = None) -> dict:
    active = active_record(project)
    if active.get("status") not in CONTINUABLE:
        return {"status": "terminal", "reason": "FM-Agent run is terminal"}
    current = ticket(project)
    identity = current.get("snapshot_commit") == active.get("snapshot_commit") and current.get("fingerprint") == active.get("fingerprint") and current.get("project") == str(project)
    incoming = _session_id(session_id)
    if current.get("status") in ACTIVE_TICKETS and identity:
        if incoming and current.get("session_id") and incoming != current.get("session_id"):
            return {"status": "failed", "reason": "continuation session mismatch", "expected_session_id": current.get("session_id"), "received_session_id": incoming}
        return current
    # Do not repeatedly relaunch a native process which exited before the DAG
    # converged.  A normal run may explicitly recover it later.
    if current.get("status") in {"failed", "finished"} and identity and current.get("retryable") is False:
        return {"status": "failed", "reason": "previous continuation exited before terminal convergence; next ordinary run is required", "ticket_id": current.get("ticket_id")}
    value = {
        "schema_version": 1, "ticket_id": uuid.uuid4().hex, "status": "pending",
        "host": os.environ.get("FM_AGENT_CONTINUATION_HOST", "claude").lower(),
        "project": str(project), "snapshot_commit": active.get("snapshot_commit"),
        "fingerprint": active.get("fingerprint"), "phase": active.get("current_phase"),
        "session_id": incoming, "created_at": now(), "updated_at": now(),
        "attempt": int(current.get("attempt", 0)) + 1,
    }
    atomic_json(ticket_path(project), value)
    return value


def ensure_ticket(project: Path, session_id: str | None = None) -> dict:
    with ticket_lock(project):
        return _ensure_ticket_unlocked(project, session_id)


def prompt(ticket_value: dict) -> str:
    return ("Continue the active FM-Agent run from its durable checkpoint. Do not summarize, ask for confirmation, or start a new analysis. "
            "Read the existing active checkpoint and execute the next bounded durable_executor action using only Claude/Codex native subagents. "
            "Submit receipts, checkpoint after progress, and stop only after terminal_report.py reports an official terminal result or an explicit exhausted failure. Continuation ticket: "
            + str(ticket_value.get("ticket_id")))


def native_command(ticket_value: dict, executable: str) -> list[str]:
    session = ticket_value.get("session_id")
    if ticket_value.get("host", "claude") == "codex":
        return [executable, "-C", ticket_value["project"], "resume", *( [session] if session else ["--last"] ), prompt(ticket_value)]
    return [executable, "--resume", session, "-p", prompt(ticket_value)] if session else [executable, "--continue", "-p", prompt(ticket_value)]


def _alive(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def launch(project: Path, session_id: str | None = None) -> dict:
    with ticket_lock(project):
        value = _ensure_ticket_unlocked(project, session_id)
        if value.get("status") in {"terminal", "failed"}:
            return value
        current = ticket(project)
        if current.get("status") in {"starting", "started", "launching", "launched"} and _alive(current.get("pid")):
            return current
        executable = shutil.which(str(value.get("host", "claude")))
        if not executable:
            value.update({"status": "failed", "retryable": True, "reason": f"native host executable is unavailable: {value.get('host')}", "updated_at": now()})
            atomic_json(ticket_path(project), value)
            return value
        value.update({"status": "starting", "updated_at": now(), "command": native_command(value, executable)})
        atomic_json(ticket_path(project), value)
        delay = float(os.environ.get("FM_AGENT_CONTINUATION_DELAY_SECONDS", "0.25"))
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run-child", "--project", str(project)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        value.update({"pid": child.pid, "delay_seconds": delay, "launched_at": now(), "updated_at": now()})
        atomic_json(ticket_path(project), value)
    # Observe the native process briefly. Popen alone is not success.
    deadline = time.monotonic() + float(os.environ.get("FM_AGENT_CONTINUATION_OBSERVE_SECONDS", "3"))
    while time.monotonic() < deadline:
        current = ticket(project)
        if current.get("status") in {"started", "failed", "finished"}:
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.poll()
            return current
        if not _alive(current.get("pid")):
            break
        time.sleep(0.05)
    child.poll()
    return ticket(project)


def run_child(project: Path) -> int:
    value = ticket(project)
    delay = float(value.get("delay_seconds", 0.25))
    if delay > 0:
        time.sleep(min(delay, 30))
    executable = shutil.which(str(value.get("host", "claude")))
    if not executable:
        update_ticket(project, {"status": "failed", "retryable": True, "reason": "native host executable disappeared", "updated_at": now()})
        return 2
    log_dir = project / "fm_agent_skill" / "control" / "continuation" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{value.get('ticket_id', 'continuation')}.log"
    command = native_command(value, executable)
    with log_path.open("ab") as output:
        child = subprocess.Popen(command, cwd=project, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, env={**os.environ, "FM_AGENT_CONTINUATION_CHILD": "1"})
        startup = float(os.environ.get("FM_AGENT_CONTINUATION_STARTUP_SECONDS", "0.5"))
        time.sleep(max(0.0, startup))
        if child.poll() is not None:
            update_ticket(project, {"status": "failed", "retryable": False, "returncode": child.returncode, "finished_at": now(), "updated_at": now(), "reason": "native continuation exited during startup window"})
            return child.returncode or 1
        update_ticket(project, {"status": "started", "native_pid": child.pid, "child_started_at": now(), "updated_at": now()})
        returncode = child.wait()
    active = active_record(project)
    converged = active.get("status") not in CONTINUABLE
    final_updates = {"status": "finished" if returncode == 0 and converged else "failed", "retryable": False, "returncode": returncode, "finished_at": now(), "updated_at": now()}
    if returncode != 0:
        final_updates["reason"] = "native Claude/Codex continuation exited unsuccessfully; inspect the continuation log"
    elif not converged:
        final_updates["reason"] = "native continuation exited before durable FM-Agent convergence; next ordinary run is required"
    update_ticket(project, final_updates)
    return returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge durable FM-Agent continuation to the native Claude/Codex CLI.")
    parser.add_argument("action", choices=("ensure", "launch", "run-child"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--session-id")
    args = parser.parse_args()
    project = Path(args.project).expanduser().resolve()
    if args.action == "ensure":
        value = ensure_ticket(project, args.session_id)
    elif args.action == "launch":
        value = launch(project, args.session_id)
    else:
        return run_child(project)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("status") not in {"failed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
