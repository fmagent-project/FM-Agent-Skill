#!/usr/bin/env python3
"""Atomic project lock for the one current FM-Agent workspace."""
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


def age_seconds(record):
    value = record.get("heartbeat_at") or record.get("started_at")
    try: return max(0.0, (state.dt.datetime.now(state.dt.timezone.utc) - state.dt.datetime.fromisoformat(value)).total_seconds())
    except (TypeError, ValueError): return float("inf")


def terminal_active(target):
    return state.active_record(target).get("status") in {"succeeded", "failed", "noop"}


def acquire(target, force_stale=False):
    root = state.plugin_dir(target); root.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "host": socket.gethostname(), "status": "running", "started_at": state.now(), "heartbeat_at": state.now()}
    try:
        fd = os.open(lock_path(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing, ttl = read_lock(target), load(target)["lock_ttl_seconds"]
        if terminal_active(target) or (force_stale and age_seconds(existing) > ttl):
            lock_path(target).unlink(missing_ok=True); return acquire(target)
        raise RuntimeError("another FM-Agent analysis is active; confirm it stopped before taking over a stale lock")
    with os.fdopen(fd, "w", encoding="utf-8") as handle: json.dump(payload, handle, ensure_ascii=False, indent=2)
    return payload


def heartbeat(target):
    record = read_lock(target)
    if not record: raise RuntimeError("no active FM-Agent lock")
    record["heartbeat_at"] = state.now(); state.atomic_json(lock_path(target), record)
    active = state.active_record(target)
    if active.get("status") == "running": active["heartbeat_at"] = record["heartbeat_at"]; state.atomic_json(status_path(target), active)
    return record


def reclaim_for_resume(target, take_over=False):
    existing = read_lock(target)
    if not existing: return acquire(target)
    grace = load(target).get("resume_grace_seconds", 600)
    if not take_over and age_seconds(existing) < grace:
        raise RuntimeError("interrupted analysis still has a fresh heartbeat; wait or explicitly confirm lock takeover")
    lock_path(target).unlink(missing_ok=True); return acquire(target)


def release(target, status="idle"):
    lock_path(target).unlink(missing_ok=True)
    return {"status": status, "ended_at": state.now()}


def main():
    parser = argparse.ArgumentParser(description="Manage FM-Agent's current-workspace lock.")
    parser.add_argument("action", choices=("acquire", "heartbeat", "release", "resume", "status")); parser.add_argument("--project", required=True); parser.add_argument("--force-stale", action="store_true"); parser.add_argument("--take-over", action="store_true"); parser.add_argument("--status", default="idle")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "status": result = read_lock(target) or state.active_record(target)
        elif args.action == "acquire": result = acquire(target, args.force_stale)
        elif args.action == "resume": result = reclaim_for_resume(target, args.take_over)
        elif args.action == "heartbeat": result = heartbeat(target)
        else: result = release(target, args.status)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr); raise SystemExit(2)


if __name__ == "__main__": main()
