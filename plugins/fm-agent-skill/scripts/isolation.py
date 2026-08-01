#!/usr/bin/env python3
"""Create one private Git snapshot worktree for the active analysis.

The snapshot uses a private index and ``commit-tree`` so it captures the
current working tree without changing the user's branch, index, or HEAD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from _common import project, state
from fm_agent_core.languages import profile_for_key


MARKER = "isolation.json"
ACTIVE_REF = "refs/fm-agent-skill/active"
BASELINE_REF = "refs/fm-agent-skill/baseline"
GENERATED_DIRS = ("fm_agent", "fm_agent_skill", ".codegraph")
IGNORE_LINES = tuple(f"/{name}/" for name in GENERATED_DIRS)
_SKIP_SCAN_DIRS = frozenset({
    ".git", ".hvigor", ".codegraph", ".test", ".gradle",
    "build", "target", "node_modules", "fm_agent", "fm_agent_skill",
})


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


def _json5_object(path: Path) -> dict:
    """Parse the JSON subset emitted by OhPM, accepting comments/trailing commas.

    This deliberately supports only the lock/package metadata shape we inspect;
    invalid or more exotic JSON5 is rejected rather than guessed.
    """
    raw = path.read_text(encoding="utf-8", errors="strict")
    output: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(raw):
        char = raw[index]
        following = raw[index + 1] if index + 1 < len(raw) else ""
        if quote is not None:
            output.append(char)
            if char == "\\":
                if index + 1 < len(raw):
                    output.append(raw[index + 1]); index += 2; continue
            elif char == quote:
                quote = None
            index += 1; continue
        if char in {"'", '"'}:
            # OhPM's generated files use JSON strings. Reject single-quoted
            # JSON5 rather than silently transforming package metadata.
            if char == "'":
                raise ValueError("single-quoted JSON5 strings are not accepted")
            quote = char; output.append(char); index += 1; continue
        if char == "/" and following == "/":
            index = raw.find("\n", index)
            if index < 0: break
            output.append("\n"); index += 1; continue
        if char == "/" and following == "*":
            end = raw.find("*/", index + 2)
            if end < 0: raise ValueError("unterminated JSON5 comment")
            index = end + 2; continue
        output.append(char); index += 1
    normalized = re.sub(r",\s*([}\]])", r"\1", "".join(output))
    value = json.loads(normalized)
    if not isinstance(value, dict): raise ValueError("JSON5 document must be an object")
    return value


def _contained_regular_tree(root: Path, project_root: Path) -> None:
    """Allow OhPM links inside the snapshot project, never outside it."""
    if root.is_symlink() or project_root not in root.resolve().parents:
        raise ValueError("dependency directory is not a real project-local directory")
    source_root = project_root.resolve()
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            candidate = current_path / name
            if candidate.is_symlink():
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError as exc:
                    raise ValueError(f"dependency tree has a broken symbolic link: {candidate.relative_to(project_root)}") from exc
                if resolved != source_root and source_root not in resolved.parents:
                    raise ValueError(f"dependency link escapes project: {candidate.relative_to(project_root)}")


def _lockfile_packages(lockfile: Path) -> set[tuple[str, str]]:
    lock = _json5_object(lockfile)
    if not isinstance(lock.get("lockfileVersion"), int) or not isinstance(lock.get("packages"), dict):
        raise ValueError("lockfile has no valid lockfileVersion/packages section")
    packages: set[tuple[str, str]] = set()
    for value in lock["packages"].values():
        if not isinstance(value, dict): continue
        name, version = value.get("name"), value.get("version")
        if isinstance(name, str) and name and isinstance(version, str) and version:
            packages.add((name, version))
    return packages


def _package_identity(manifest: Path) -> tuple[str, str] | None:
    """Read the two simple JSON5 fields needed for an OhPM package binding."""
    text = manifest.read_text(encoding="utf-8", errors="strict")
    values = {}
    for field in ("name", "version"):
        match = re.search(rf"(?<![\w$])['\"]?{field}['\"]?\s*:\s*(['\"])(.*?)\1", text)
        if match is None:
            return None
        values[field] = match.group(2)
    return values["name"], values["version"]


def _dependency_manifests(dependency_root: Path) -> list[Path]:
    """Include package manifests reached through safe OhPM workspace links."""
    found = {path.resolve() for path in dependency_root.rglob("oh-package.json5")}
    for current, directories, files in os.walk(dependency_root, followlinks=False):
        for name in [*directories, *files]:
            candidate = Path(current) / name
            if not candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
            manifest = resolved / "oh-package.json5" if resolved.is_dir() else None
            if manifest is not None and manifest.is_file():
                found.add(manifest.resolve())
    return sorted(found)


def _validate_oh_modules(dependency_root: Path, lockfile: Path, source: Path) -> dict:
    """Bind every installed package manifest to the adjacent OhPM lockfile."""
    _contained_regular_tree(dependency_root, source)
    packages = _lockfile_packages(lockfile)
    manifests = _dependency_manifests(dependency_root)
    if not manifests:
        raise ValueError("oh_modules contains no package metadata")
    for manifest in manifests:
        identity = _package_identity(manifest)
        if identity is None or identity not in packages:
            raise ValueError(f"dependency metadata is absent from {lockfile.name}: {manifest.relative_to(source)}")
    return {
        "dependency_path": dependency_root.relative_to(source).as_posix(),
        "lockfile_path": lockfile.relative_to(source).as_posix(),
        "lockfile_sha256": hashlib.sha256(lockfile.read_bytes()).hexdigest(),
        "package_count": len(manifests),
    }


def _walk_project_directories(source: Path):
    for current, directories, _ in os.walk(source, followlinks=False):
        directories[:] = [
            name for name in directories
            if name not in _SKIP_SCAN_DIRS and not (Path(current) / name).is_symlink()
        ]
        yield Path(current), directories


def _hydrate_arkts_dependencies(source: Path, snapshot: Path) -> dict:
    """Copy only lock-bound ArkTS dependencies that Git intentionally omits.

    A missing or unsafe dependency tree does not block static analysis. The
    marker records why dynamic ArkTS validation must later be inconclusive.
    """
    policy = profile_for_key("arkts").dependency_hydration
    assert policy is not None
    arkts_seen = False
    candidates: list[tuple[Path, Path]] = []
    for current, directories in _walk_project_directories(source):
        arkts_seen = arkts_seen or any(path.suffix.lower() == ".ets" for path in current.glob("*.ets"))
        if policy.directory_name not in directories:
            continue
        dependency_root = current / policy.directory_name
        directories.remove(policy.directory_name)
        lockfile = next((current / name for name in policy.lockfile_names if (current / name).is_file()), None)
        if lockfile is None:
            return {"status": "unavailable", "reason": f"{dependency_root.relative_to(source)} has no adjacent OhPM lockfile", "entries": []}
        candidates.append((dependency_root, lockfile))
    if not arkts_seen:
        return {"status": "not_applicable", "entries": []}
    if not candidates:
        return {"status": "unavailable", "reason": "no project-local lock-bound oh_modules directory", "entries": []}
    entries = []
    try:
        for dependency_root, lockfile in candidates:
            entry = _validate_oh_modules(dependency_root, lockfile, source)
            destination = snapshot / entry["dependency_path"]
            if destination.exists():
                raise ValueError(f"snapshot already contains {entry['dependency_path']}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # OhPM uses in-tree links to its .ohpm store. They have already
            # been proven contained above, so preserve that layout verbatim.
            shutil.copytree(dependency_root, destination, symlinks=True)
            entries.append(entry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        for entry in entries:
            shutil.rmtree(snapshot / entry["dependency_path"], ignore_errors=True)
        return {"status": "unavailable", "reason": str(exc), "entries": []}
    return {"status": "hydrated", "entries": entries}


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
        arkts_dependencies = _hydrate_arkts_dependencies(target, snapshot)
        data = {"schema_version": 3, "source_project": str(target), "snapshot": str(snapshot), "snapshot_commit": commit, "created_at": state.now(), "arkts_dependencies": arkts_dependencies}
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
