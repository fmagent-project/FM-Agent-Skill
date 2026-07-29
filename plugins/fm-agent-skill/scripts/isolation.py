#!/usr/bin/env python3
"""Create and reconcile a throwaway Git worktree for `--isolate`.

This is a Skill-owned implementation. It never invokes the original FM-Agent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from _common import project, state


MARKER = "isolation.json"


def marker_path(target: Path) -> Path:
    return state.skill_dir(target) / MARKER


def marker(target: Path) -> dict:
    return state.read_json(marker_path(target), {})


def _copy_tree(source: Path, destination: Path, ignore: set[str]) -> None:
    for item in source.iterdir():
        if item.name in ignore: continue
        target = destination / item.name
        if item.is_dir(): shutil.copytree(item, target, dirs_exist_ok=True, symlinks=True)
        else: shutil.copy2(item, target, follow_symlinks=False)


def create(target: Path) -> dict:
    existing = marker(target)
    if isinstance(existing.get("snapshot"), str) and Path(existing["snapshot"]).is_dir():
        raise RuntimeError("an isolated FM-Agent analysis already exists; resume or finish it before starting another")
    if state.git(target, "rev-parse", "--is-inside-work-tree") != "true":
        raise RuntimeError("--isolate requires a Git worktree")
    parent = Path(tempfile.mkdtemp(prefix="fm-agent-skill-isolate-")); snapshot = parent / "project"
    completed = subprocess.run(["git", "-C", str(target), "worktree", "add", "--detach", "--quiet", str(snapshot), "HEAD"], text=True, capture_output=True)
    if completed.returncode:
        shutil.rmtree(parent, ignore_errors=True); raise RuntimeError(completed.stderr.strip() or "could not create isolated Git worktree")
    try:
        _copy_tree(target, snapshot, {".git"})
        data = {"schema_version": 1, "source_project": str(target), "snapshot": str(snapshot), "created_at": state.now()}
        state.atomic_json(marker_path(target), data)
        state.atomic_json(marker_path(snapshot), data)
        return data
    except Exception:
        subprocess.run(["git", "-C", str(target), "worktree", "remove", "--force", str(snapshot)], text=True, capture_output=True)
        shutil.rmtree(parent, ignore_errors=True); raise


def sync(snapshot: Path) -> dict:
    data = marker(snapshot); source_value = data.get("source_project")
    if not isinstance(source_value, str) or not source_value: return {"synced": False, "reason": "no isolated snapshot marker"}
    source = Path(source_value)
    if not source.is_dir() or not snapshot.is_dir(): return {"synced": False, "reason": "no isolated snapshot marker"}
    marker_path(snapshot).unlink(missing_ok=True)
    for name in ("fm_agent", "fm_agent_skill"):
        origin, destination = snapshot / name, source / name
        if not origin.exists(): continue
        if destination.exists(): shutil.rmtree(destination)
        shutil.copytree(origin, destination, symlinks=True)
    marker_path(source).unlink(missing_ok=True)
    completed = subprocess.run(["git", "-C", str(source), "worktree", "remove", "--force", str(snapshot)], text=True, capture_output=True)
    if completed.returncode: raise RuntimeError(completed.stderr.strip() or "could not remove isolated Git worktree")
    shutil.rmtree(snapshot.parent, ignore_errors=True)
    return {"synced": True, "source_project": str(source)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage throwaway FM-Agent Skill isolated worktrees.")
    parser.add_argument("action", choices=("create", "sync", "show")); parser.add_argument("--project", required=True)
    args = parser.parse_args(); target = project(args)
    try:
        result = create(target) if args.action == "create" else sync(target) if args.action == "sync" else marker(target)
        code = 0
    except RuntimeError as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(code)


if __name__ == "__main__": main()
