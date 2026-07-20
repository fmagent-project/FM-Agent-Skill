#!/usr/bin/env python3
"""Atomic, recoverable repository-local analysis locking."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys

from _common import project, state
from config import load


def lock_path(target): return state.plugin_dir(target) / "active.lock"
def status_path(target): return state.plugin_dir(target) / "active.json"

def read_lock(target): return state.read_json(lock_path(target), {})

def stale(target, record, ttl):
    started = record.get("heartbeat_at") or record.get("started_at")
    if not isinstance(started, str): return True
    try:
        age = (state.dt.datetime.now(state.dt.timezone.utc) - state.dt.datetime.fromisoformat(started)).total_seconds()
    except ValueError: return True
    if age <= ttl: return False
    run_id = record.get("run_id")
    run = state.read_json(state.plugin_dir(target) / "runs" / f"{run_id}.json", {}) if run_id else {}
    return not isinstance(run, dict) or run.get("status") != "running"


def age_seconds(record):
    started = record.get("heartbeat_at") or record.get("started_at")
    if not isinstance(started, str):
        return float("inf")
    try:
        return max(0.0, (state.dt.datetime.now(state.dt.timezone.utc) - state.dt.datetime.fromisoformat(started)).total_seconds())
    except ValueError:
        return float("inf")


def terminal_run(target, record):
    """Return the completed run record that makes an existing lock safe to reclaim."""
    run_id = record.get("run_id")
    if not isinstance(run_id, str): return None
    run = state.read_json(state.plugin_dir(target) / "runs" / f"{run_id}.json", {})
    if isinstance(run, dict) and run.get("id") == run_id and run.get("status") in {"succeeded", "failed", "noop"}:
        return run
    active = state.read_json(status_path(target), {})
    if isinstance(active, dict) and active.get("id") == run_id and active.get("status") in {"succeeded", "failed", "noop"}:
        return active
    return None

def publish(target, record): state.atomic_json(status_path(target), record)

def acquire(target, run_id, force_stale=False):
    root = state.plugin_dir(target); root.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "pid": os.getpid(), "host": socket.gethostname(), "status": "running", "started_at": state.now(), "heartbeat_at": state.now()}
    path = lock_path(target)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = read_lock(target); ttl = load(target)["lock_ttl_seconds"]
        if terminal_run(target, existing):
            path.unlink(missing_ok=True)
            return acquire(target, run_id, False)
        if force_stale and stale(target, existing, ttl):
            path.unlink(missing_ok=True)
            return acquire(target, run_id, False)
        raise RuntimeError("another FM-Agent analysis is active; use --force-stale only after its TTL and inactive run check")
    with os.fdopen(fd, "w", encoding="utf-8") as handle: json.dump(payload, handle, ensure_ascii=False, indent=2)
    publish(target, payload); return payload

def heartbeat(target, run_id):
    record = read_lock(target)
    if record.get("run_id") != run_id: raise RuntimeError("lock is not owned by this run")
    record["heartbeat_at"] = state.now(); state.atomic_json(lock_path(target), record)
    # Keep the complete run record in active.json.  Publishing the abbreviated
    # lock payload here used to erase current_phase and phase_status.
    active = state.read_json(status_path(target), {})
    if isinstance(active, dict) and active.get("id") == run_id and active.get("status") == "running":
        active["heartbeat_at"] = record["heartbeat_at"]
        state.atomic_json(status_path(target), active)
    else:
        publish(target, record)
    return record


def reclaim_for_resume(target, run_id, take_over=False):
    """Acquire an interrupted run's lock without silently replacing live work."""
    existing = read_lock(target)
    if not existing:
        return acquire(target, run_id, False)
    owner = existing.get("run_id")
    if owner != run_id:
        if terminal_run(target, existing):
            lock_path(target).unlink(missing_ok=True)
            return acquire(target, run_id, False)
        raise RuntimeError("another FM-Agent analysis owns the active lock")
    grace = load(target).get("resume_grace_seconds", 600)
    if not take_over and age_seconds(existing) < grace:
        raise RuntimeError("interrupted run still has a fresh heartbeat; wait or explicitly confirm lock takeover")
    lock_path(target).unlink(missing_ok=True)
    return acquire(target, run_id, False)

def release(target, run_id, status="idle"):
    record = read_lock(target)
    if record and record.get("run_id") != run_id: raise RuntimeError("lock is not owned by this run")
    lock_path(target).unlink(missing_ok=True)
    active = state.read_json(status_path(target), {})
    # `active.json` is the canonical complete run record. Do not replace it
    # with the much smaller lock record after pipeline.py has finalized.
    if isinstance(active, dict) and active.get("id") == run_id and active.get("status") in {"succeeded", "failed", "noop"}:
        return active
    record.update({"run_id": run_id, "status": status, "ended_at": state.now()}); publish(target, record); return record

def main():
    parser = argparse.ArgumentParser(description="Manage FM-Agent's atomic repository lock.")
    parser.add_argument("action", choices=("acquire", "heartbeat", "release", "resume", "status")); parser.add_argument("--project", required=True); parser.add_argument("--run-id"); parser.add_argument("--force-stale", action="store_true"); parser.add_argument("--take-over", action="store_true"); parser.add_argument("--status", default="idle")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "status": result = read_lock(target) or state.read_json(status_path(target), {})
        elif args.action == "acquire":
            if not args.run_id: parser.error("acquire requires --run-id")
            result = acquire(target, args.run_id, args.force_stale)
        elif args.action == "resume":
            if not args.run_id: parser.error("resume requires --run-id")
            result = reclaim_for_resume(target, args.run_id, args.take_over)
        elif args.action == "heartbeat":
            if not args.run_id: parser.error("heartbeat requires --run-id")
            result = heartbeat(target, args.run_id)
        else:
            if not args.run_id: parser.error("release requires --run-id")
            result = release(target, args.run_id, args.status)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr); raise SystemExit(2)

if __name__ == "__main__": main()
