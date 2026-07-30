#!/usr/bin/env python3
"""Create one private Git snapshot worktree for the active analysis.

The snapshot uses a private index and ``commit-tree`` so it captures the
current working tree without changing the user's branch, index, or HEAD.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from _common import project, state


MARKER = "isolation.json"
ACTIVE_REF = "refs/fm-agent-skill/active"
BASELINE_REF = "refs/fm-agent-skill/baseline"
GENERATED_DIRS = ("fm_agent", "fm_agent_skill", ".codegraph")
IGNORE_LINES = tuple(f"/{name}/" for name in GENERATED_DIRS)


def marker_path(target: Path) -> Path:
    return state.skill_dir(target) / MARKER


def marker(target: Path) -> dict:
    return state.read_json(marker_path(target), {})


def _run(target: Path, *args: str, env: dict | None = None) -> str:
    completed = subprocess.run(["git", "-C", str(target), *args], text=True, capture_output=True, env=env, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed.stdout.strip()


def ensure_gitignore(target: Path) -> dict:
    """Add only the generated-directory rules needed by the Skill."""
    tracked = _run(target, "ls-files", "--", *GENERATED_DIRS).splitlines()
    if tracked:
        raise RuntimeError("generated analysis paths are already tracked: " + ", ".join(tracked[:5]))
    path = target / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = current.splitlines()
    missing = [line for line in IGNORE_LINES if line not in lines]
    if missing:
        suffix = "" if not current or current.endswith("\n") else "\n"
        path.write_text(current + suffix + "\n".join(missing) + "\n", encoding="utf-8")
    return {"path": str(path), "added": missing}


def _copy_outputs(source: Path, snapshot: Path) -> None:
    for name in ("fm_agent", "fm_agent_skill"):
        origin, destination = source / name, snapshot / name
        if not origin.exists() or destination.exists():
            continue
        if origin.is_dir(): shutil.copytree(origin, destination, symlinks=True)


def create(target: Path) -> dict:
    existing = marker(target)
    if isinstance(existing.get("snapshot"), str) and Path(existing["snapshot"]).is_dir():
        raise RuntimeError("an FM-Agent snapshot worktree is already active; resume or clean it first")
    if _run(target, "rev-parse", "--is-inside-work-tree") != "true":
        raise RuntimeError("FM-Agent Skill requires a Git worktree")
    ensure_gitignore(target)
    parent = Path(tempfile.mkdtemp(prefix="fm-agent-skill-worktree-"))
    snapshot = parent / "project"
    private_index = parent / "index"
    env = dict(os.environ, GIT_INDEX_FILE=str(private_index), GIT_AUTHOR_NAME="FM-Agent Skill", GIT_AUTHOR_EMAIL="fm-agent-skill@local", GIT_COMMITTER_NAME="FM-Agent Skill", GIT_COMMITTER_EMAIL="fm-agent-skill@local")
    try:
        head = _run(target, "rev-parse", "HEAD")
        _run(target, "read-tree", "HEAD", env=env)
        _run(target, "add", "-A", env=env)
        _run(target, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", *GENERATED_DIRS, env=env)
        tree = _run(target, "write-tree", env=env)
        head_tree = _run(target, "rev-parse", "HEAD^{tree}")
        commit = head if tree == head_tree else _run(target, "commit-tree", tree, "-p", head, "-m", "fm-agent-skill snapshot", env=env)
        _run(target, "update-ref", ACTIVE_REF, commit)
        _run(target, "worktree", "add", "--detach", "--quiet", str(snapshot), commit)
        _copy_outputs(target, snapshot)
        data = {"schema_version": 2, "source_project": str(target), "snapshot": str(snapshot), "snapshot_commit": commit, "created_at": state.now()}
        state.atomic_json(marker_path(target), data)
        state.atomic_json(marker_path(snapshot), data)
        return data
    except Exception:
        subprocess.run(["git", "-C", str(target), "worktree", "remove", "--force", str(snapshot)], text=True, capture_output=True)
        subprocess.run(["git", "-C", str(target), "update-ref", "-d", ACTIVE_REF], text=True, capture_output=True)
        shutil.rmtree(parent, ignore_errors=True)
        raise


def _remove(snapshot: Path, source: Path) -> None:
    completed = subprocess.run(["git", "-C", str(source), "worktree", "remove", "--force", str(snapshot)], text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "could not remove snapshot worktree")
    shutil.rmtree(snapshot.parent, ignore_errors=True)


def sync(snapshot: Path) -> dict:
    """Promote a successful snapshot and copy only generated artifacts back."""
    data = marker(snapshot); source_value = data.get("source_project"); commit = data.get("snapshot_commit")
    if not isinstance(source_value, str) or not isinstance(commit, str):
        return {"synced": False, "reason": "no active FM-Agent snapshot marker"}
    source = Path(source_value)
    if not source.is_dir() or not snapshot.is_dir():
        return {"synced": False, "reason": "active FM-Agent snapshot is unavailable"}
    _run(source, "update-ref", BASELINE_REF, commit)
    marker_path(snapshot).unlink(missing_ok=True)
    for name in ("fm_agent", "fm_agent_skill"):
        origin, destination = snapshot / name, source / name
        if not origin.exists():
            continue
        if destination.exists(): shutil.rmtree(destination)
        shutil.copytree(origin, destination, symlinks=True)
    marker_path(source).unlink(missing_ok=True)
    _run(source, "update-ref", "-d", ACTIVE_REF)
    _remove(snapshot, source)
    return {"synced": True, "source_project": str(source), "baseline_commit": commit}


def discard(snapshot: Path) -> dict:
    data = marker(snapshot)
    if isinstance(data.get("snapshot"), str) and Path(data["snapshot"]).is_dir() and Path(data["snapshot"]).resolve() != snapshot.resolve():
        snapshot = Path(data["snapshot"]).resolve(); data = marker(snapshot)
    source_value = data.get("source_project")
    if not isinstance(source_value, str): return {"discarded": False, "reason": "no active FM-Agent snapshot marker"}
    source = Path(source_value)
    marker_path(snapshot).unlink(missing_ok=True); marker_path(source).unlink(missing_ok=True)
    subprocess.run(["git", "-C", str(source), "update-ref", "-d", ACTIVE_REF], text=True, capture_output=True)
    _remove(snapshot, source)
    return {"discarded": True, "source_project": str(source)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage FM-Agent Skill private snapshot worktrees.")
    parser.add_argument("action", choices=("create", "sync", "discard", "show", "ensure-gitignore")); parser.add_argument("--project", required=True)
    args = parser.parse_args(); target = project(args)
    try:
        result = {"create": lambda: create(target), "sync": lambda: sync(target), "discard": lambda: discard(target), "show": lambda: marker(target), "ensure-gitignore": lambda: ensure_gitignore(target)}[args.action](); code = 0
    except RuntimeError as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(code)


if __name__ == "__main__": main()
