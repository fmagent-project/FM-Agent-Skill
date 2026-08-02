#!/usr/bin/env python3
"""Crash-consistent, content-addressed FM-Agent checkpoints.

The temporary Git worktree is an execution cache.  This module keeps the
authoritative current analysis in the source project and can reconstruct a
lost worktree without asking a Worker to regenerate completed artifacts.
It never invokes a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
from typing import Iterable

from _common import project, state
from config import load


CHECKPOINT_SCHEMA_VERSION = 2
STATE_SCHEMA_VERSION = 2
ACTIVE_REF = "refs/fm-agent-skill/active"
RECOVERY_FILES = ("active.json", "config.json")
RECOVERY_DIRS = ("jobs", "control", "probes")
EXCLUDED_PARTS = frozenset({
    "checkpoint", "runtime", "worktree", "worktrees", "build-cache",
    "build_cache", "shared-cache", "shared_cache", "__pycache__", ".git",
})
EXCLUDED_SUFFIXES = (".lock", ".tmp", ".temp", ".swp", ".pyc")
PHASE_NAMES = {
    "preflight": (1, "01-preflight.json"),
    "project_understanding": (2, "02-project-understanding.json"),
    "phase_cleanup": (3, "03-phase-cleanup.json"),
    "extraction": (4, "04-extraction.json"),
    "call_graph": (5, "05-call-graph.json"),
    "specification": (6, "06-specification.json"),
    "verification": (7, "07-verification.json"),
    "verify_affected": (7, "07-verification.json"),
    "bug_validation": (8, "08-bug-validation.json"),
    "finalize": (9, "09-finalize.json"),
    # Incremental-only phases retain a stable filename without colliding with
    # the canonical full-run manifests.
    "validate_baseline": (1, "01-validate-baseline.json"),
    "refresh_plan": (2, "02-refresh-plan.json"),
    "preserve_specs": (3, "03-preserve-specs.json"),
    "diff": (4, "04-diff.json"),
    "rebuild_graph": (5, "05-rebuild-graph.json"),
    "select_scope": (6, "06-select-scope.json"),
    "update_specs": (6, "06-update-specs.json"),
}


class CheckpointError(RuntimeError):
    """A checkpoint is absent, incompatible, or corrupt."""


class InjectedCrash(CheckpointError):
    """Deterministic fault used by crash-consistency tests."""


def _run(target: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(target), *args], text=True, capture_output=True,
        check=False,
    )
    if check and completed.returncode:
        raise CheckpointError(
            completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        )
    return completed.stdout.strip()


def source_project(target: Path) -> Path:
    """Resolve the durable source project from either side of isolation."""
    marker = state.read_json(state.skill_dir(target) / "isolation.json", {})
    value = marker.get("source_project") if isinstance(marker, dict) else None
    if isinstance(value, str) and Path(value).is_dir():
        return Path(value).resolve()
    return target.resolve()


def root(target: Path) -> Path:
    return state.skill_dir(source_project(target)) / "checkpoint"


def db_path(target: Path) -> Path:
    return root(target) / "state.db"


def _connect(target: Path) -> sqlite3.Connection:
    path = db_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            snapshot_commit TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('committing','complete')),
            semantic_status TEXT NOT NULL,
            previous_checkpoint_id TEXT,
            manifest_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS checkpoints_phase_status
            ON checkpoints(phase, status, ordinal);
        """
    )
    return connection


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _manifest_digest(manifest: dict) -> str:
    """Hash the canonical manifest body; the digest field is not self-hashed."""
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    return _sha256_bytes(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))


def _seal_manifest(manifest: dict) -> dict:
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    return manifest


def _sync_sqlite(connection: sqlite3.Connection, target: Path) -> None:
    """Establish a controlled WAL boundary before publishing/accepting HEAD.

    `state.db` is not copied into `current`: it is the durable Coordinator
    ledger itself.  A FULL checkpoint plus fsync makes every committed ledger
    mutation visible in the main database before a manifest claims success.
    """
    row = connection.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
    if row is None or row[0] != 0 or row[1] != row[2]:
        raise CheckpointError("could not establish a complete SQLite WAL checkpoint boundary")
    database = db_path(target)
    _fsync_file(database)
    wal = Path(str(database) + "-wal")
    if wal.is_file():
        _fsync_file(wal)


def _crash(point: str) -> None:
    if os.environ.get("FM_AGENT_CHECKPOINT_CRASH_AT") == point:
        raise InjectedCrash(f"injected checkpoint crash at {point}")


def _allowed(path: Path, base: Path) -> bool:
    relative = path.relative_to(base)
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    return not path.name.endswith(EXCLUDED_SUFFIXES) and not path.is_symlink()


def _files(base: Path) -> Iterable[Path]:
    if not base.is_dir():
        return ()
    return (
        path for path in sorted(base.rglob("*"))
        if path.is_file() and _allowed(path, base)
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object_path(checkpoint_root: Path, digest: str) -> Path:
    return checkpoint_root / "objects" / digest[:2] / digest[2:]


def _store_object(checkpoint_root: Path, payload: bytes) -> tuple[str, int, Path]:
    digest = _sha256_bytes(payload)
    destination = _object_path(checkpoint_root, digest)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.unlink(missing_ok=True)
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_dir(destination.parent)
    elif destination.stat().st_size != len(payload) or state.file_hash(destination) != digest:
        raise CheckpointError(f"content-addressed object is corrupt: {digest}")
    return digest, len(payload), destination


def _inventory(target: Path, active_record: dict | None = None) -> list[dict]:
    """Collect the complete fm_agent mirror and the explicit recovery allowlist."""
    items: list[dict] = []
    fm = state.fm_dir(target)
    for path in _files(fm):
        payload = path.read_bytes()
        items.append({
            "path": "fm_agent/" + path.relative_to(fm).as_posix(),
            "sha256": _sha256_bytes(payload),
            "size": len(payload),
            "payload": payload,
        })
    skill = state.skill_dir(target)
    for name in RECOVERY_FILES:
        path = skill / name
        if name == "active.json" and active_record is not None:
            payload = (json.dumps(active_record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        elif path.is_file() and _allowed(path, skill):
            payload = path.read_bytes()
        else:
            continue
        items.append({
            "path": f"recovery/{name}", "sha256": _sha256_bytes(payload),
            "size": len(payload), "payload": payload,
        })
    for name in RECOVERY_DIRS:
        base = skill / name
        for path in _files(base):
            payload = path.read_bytes()
            items.append({
                "path": f"recovery/{name}/" + path.relative_to(base).as_posix(),
                "sha256": _sha256_bytes(payload), "size": len(payload),
                "payload": payload,
            })
    return sorted(items, key=lambda item: item["path"])


def _materialize(checkpoint_root: Path, inventory: list[dict], destination: Path) -> None:
    for item in inventory:
        output = destination / item["path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        source = _object_path(checkpoint_root, item["sha256"])
        if not source.is_file():
            raise CheckpointError(f"checkpoint object is missing: {item['sha256']}")
        # `current/` is a replaceable mirror. Hard-linking it to immutable
        # objects would let a mirror write mutate the object store through the
        # shared inode, invalidating manifest arbitration. Copy-on-write is
        # not portable here, so copy2 is the safe universal fallback.
        shutil.copy2(source, output)


def _read_head(checkpoint_root: Path) -> str | None:
    try:
        value = (checkpoint_root / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _row(connection: sqlite3.Connection, checkpoint_id: str | None) -> sqlite3.Row | None:
    if not checkpoint_id:
        return None
    return connection.execute(
        "SELECT * FROM checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)
    ).fetchone()


def _last_complete(connection: sqlite3.Connection, before: str | None = None) -> sqlite3.Row | None:
    if before:
        current = _row(connection, before)
        previous = current["previous_checkpoint_id"] if current else None
        while previous:
            candidate = _row(connection, previous)
            if candidate and candidate["status"] == "complete":
                return candidate
            previous = candidate["previous_checkpoint_id"] if candidate else None
    return connection.execute(
        "SELECT * FROM checkpoints WHERE status = 'complete' "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()


def _manifest(row: sqlite3.Row) -> dict:
    value = json.loads(row["manifest_json"])
    if not isinstance(value, dict):
        raise CheckpointError("checkpoint manifest is not an object")
    if value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointError("checkpoint manifest schema is incompatible")
    digest = value.get("manifest_sha256")
    if not isinstance(digest, str) or digest != _manifest_digest(value):
        raise CheckpointError("checkpoint manifest hash is invalid")
    return value


def _version_record(target: Path) -> dict:
    try:
        from versioning import runtime_version
        version = runtime_version(target)
        record = state.active_record(target)
        config = record.get("inputs", {}).get("config") if isinstance(record, dict) else None
        policy = config.get("worker_prompt_change_policy") if isinstance(config, dict) else None
        if policy in {"invalidate", "allow_prompt_only"}:
            version["worker_prompt_change_policy"] = policy
        return version
    except (ImportError, OSError, ValueError, RuntimeError):
        return {
            "state_schema_version": STATE_SCHEMA_VERSION,
            "scheduler_schema_version": 1,
        }


def _phase_info(phase: str, phases: list[str] | None = None) -> tuple[int, str]:
    if phase in PHASE_NAMES:
        return PHASE_NAMES[phase]
    if phases and phase in phases:
        ordinal = phases.index(phase) + 1
        return ordinal, f"{ordinal:02d}-{phase.replace('_', '-')}.json"
    raise CheckpointError(f"unknown checkpoint phase: {phase}")


def commit(
    target: Path,
    phase: str,
    semantic_status: str = "succeeded",
    scheduler_receipt: str | dict | None = None,
    active_record: dict | None = None,
    message: str | None = None,
) -> dict:
    """Atomically publish a complete recoverable phase state.

    Objects are immutable.  ``current`` may be replaced before ``HEAD``; if a
    process dies there, :func:`validate` rebuilds it from the last complete DB
    row selected by the old HEAD.
    """
    target = target.resolve()
    record = active_record or state.active_record(target)
    snapshot_commit = record.get("snapshot_commit") or state.current_snapshot_commit(target)
    fingerprint = record.get("fingerprint", "")
    ordinal, filename = _phase_info(phase, record.get("phases"))
    started_at = record.get("phase_status", {}).get(phase, {}).get("started_at") or state.now()
    checkpoint_root = root(target)
    for directory in (checkpoint_root / "phases", checkpoint_root / "objects"):
        directory.mkdir(parents=True, exist_ok=True)

    connection = _connect(target)
    previous_head = _read_head(checkpoint_root)
    previous_row = _row(connection, previous_head)
    if previous_head and (not previous_row or previous_row["status"] != "complete"):
        previous_row = _last_complete(connection, previous_head)
        previous_head = previous_row["checkpoint_id"] if previous_row else None
    previous_inventory = {
        item["path"]: item for item in (_manifest(previous_row).get("inventory", []) if previous_row else [])
    }

    raw_inventory = _inventory(target, record)
    inventory = []
    for item in raw_inventory:
        digest, size, _ = _store_object(checkpoint_root, item.pop("payload"))
        if digest != item["sha256"] or size != item["size"]:
            raise CheckpointError(f"object identity changed while checkpointing: {item['path']}")
        inventory.append(item)
    _crash("after_objects")

    current_tmp = checkpoint_root / f".current.{os.getpid()}.tmp"
    if current_tmp.exists():
        shutil.rmtree(current_tmp)
    current_tmp.mkdir(parents=True)
    _materialize(checkpoint_root, inventory, current_tmp)
    _fsync_dir(current_tmp)
    current = checkpoint_root / "current"
    previous_current = checkpoint_root / ".current.previous"
    if previous_current.exists():
        shutil.rmtree(previous_current)
    if current.exists():
        os.replace(current, previous_current)
    os.replace(current_tmp, current)
    _fsync_dir(checkpoint_root)
    _crash("after_current")

    current_map = {item["path"]: item for item in inventory}
    produced = [
        item for path, item in current_map.items()
        if path not in previous_inventory or previous_inventory[path]["sha256"] != item["sha256"]
    ]
    preserved = [
        item for path, item in current_map.items()
        if path in previous_inventory and previous_inventory[path]["sha256"] == item["sha256"]
    ]
    removed = [
        {"path": path, "sha256": item["sha256"], "size": item["size"], "tombstone": True}
        for path, item in previous_inventory.items() if path not in current_map
    ]
    seed = {
        "phase": phase, "ordinal": ordinal, "snapshot_commit": snapshot_commit,
        "input_fingerprint": fingerprint, "inventory": inventory,
        "previous_checkpoint_id": previous_head, "completed_at": state.now(),
    }
    checkpoint_id = hashlib.sha256(
        json.dumps(seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt_value = scheduler_receipt
    if receipt_value is None:
        candidate = state.control_dir(target) / "phase_receipts" / f"{phase}.json"
        receipt_value = state.file_hash(candidate) if candidate.is_file() else ""
    manifest = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "phase": phase,
        "ordinal": ordinal,
        "status": "committing",
        "snapshot_commit": snapshot_commit,
        "input_fingerprint": fingerprint,
        "started_at": started_at,
        "completed_at": seed["completed_at"],
        "produced": produced,
        "preserved": preserved,
        "removed": removed,
        "scheduler_receipt": receipt_value,
        "next_phase": _next_phase(record, phase),
        "previous_checkpoint_id": previous_head,
        "inventory": inventory,
        "message": message,
        "version": _version_record(target),
    }
    _seal_manifest(manifest)
    manifest_path = checkpoint_root / "phases" / filename
    _atomic_json(manifest_path, manifest)
    _crash("after_manifest")

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "INSERT OR REPLACE INTO checkpoints "
            "(checkpoint_id,phase,ordinal,snapshot_commit,input_fingerprint,status,semantic_status,"
            "previous_checkpoint_id,manifest_json,created_at,completed_at) "
            "VALUES (?,?,?,?,?,'committing',?,?,?,?,NULL)",
            (
                checkpoint_id, phase, ordinal, snapshot_commit, fingerprint,
                semantic_status, previous_head,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True), state.now(),
            ),
        )
        connection.execute("COMMIT")
        _sync_sqlite(connection, target)
    except Exception:
        connection.execute("ROLLBACK")
        raise
    _crash("after_db")

    _atomic_bytes(checkpoint_root / "HEAD", (checkpoint_id + "\n").encode("ascii"))
    _crash("after_head")

    # The final durable operation marks the semantic phase outcome.  Recovery
    # rejects a HEAD whose row is still `committing`.
    manifest["status"] = semantic_status
    _seal_manifest(manifest)
    _atomic_json(manifest_path, manifest)
    _atomic_json(checkpoint_root / "snapshot.json", manifest)
    _atomic_json(checkpoint_root / "active.json", record)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE checkpoints SET status='complete', semantic_status=?, manifest_json=?, completed_at=? "
            "WHERE checkpoint_id=?",
            (
                semantic_status, json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                state.now(), checkpoint_id,
            ),
        )
        connection.execute("COMMIT")
        _sync_sqlite(connection, target)
    except Exception:
        connection.execute("ROLLBACK")
        raise
    if previous_current.exists():
        shutil.rmtree(previous_current)
    connection.close()
    # Expose a current, explicitly non-official mirror in the source project
    # after every sealed checkpoint. The checkpoint object store and manifest
    # remain authoritative; a presentation-copy failure cannot undo a valid
    # checkpoint.
    try:
        from isolation import publish_progress
        progress = publish_progress(target, manifest, record)
    except (OSError, RuntimeError, ValueError) as exc:
        progress = {"published": False, "reason": str(exc)}
    result = dict(manifest)
    result["progress_mirror"] = progress
    return result


def _next_phase(record: dict, phase: str) -> str | None:
    phases = record.get("phases") if isinstance(record.get("phases"), list) else []
    if phase not in phases:
        return None
    index = phases.index(phase)
    return phases[index + 1] if index + 1 < len(phases) else None


def _select_complete(target: Path) -> tuple[sqlite3.Connection, sqlite3.Row, dict]:
    checkpoint_root = root(target)
    if not checkpoint_root.is_dir():
        raise CheckpointError("persistent checkpoint directory is missing")
    connection = _connect(target)
    head = _read_head(checkpoint_root)
    if not head:
        connection.close()
        raise CheckpointError("checkpoint HEAD is missing")
    row = _row(connection, head)
    if not row or row["status"] != "complete":
        row = _last_complete(connection, head)
    if not row:
        connection.close()
        raise CheckpointError("no complete checkpoint exists; interrupted commit cannot be resumed")
    manifest = _manifest(row)
    return connection, row, manifest


def _verify_inventory(checkpoint_root: Path, inventory: list[dict], current: Path | None = None) -> list[str]:
    errors: list[str] = []
    for item in inventory:
        if not isinstance(item, dict) or not all(key in item for key in ("path", "sha256", "size")):
            errors.append("invalid inventory entry")
            continue
        object_path = _object_path(checkpoint_root, item["sha256"])
        if not object_path.is_file() or object_path.stat().st_size != item["size"] or state.file_hash(object_path) != item["sha256"]:
            errors.append(f"missing or corrupt object: {item['path']}")
            continue
        if current is not None:
            output = current / item["path"]
            if not output.is_file() or output.stat().st_size != item["size"] or state.file_hash(output) != item["sha256"]:
                errors.append(f"current mirror mismatch: {item['path']}")
    return errors


def _verify_phase_manifest(checkpoint_root: Path, manifest: dict) -> list[str]:
    """Require the durable phase file to agree with the sealed ledger copy."""
    _, filename = _phase_info(manifest["phase"])
    path = checkpoint_root / "phases" / filename
    disk = state.read_json(path, None)
    if not isinstance(disk, dict):
        return [f"phase manifest is missing or invalid: {filename}"]
    if disk.get("checkpoint_id") != manifest.get("checkpoint_id"):
        return [f"phase manifest checkpoint id differs: {filename}"]
    if disk.get("manifest_sha256") != manifest.get("manifest_sha256"):
        return [f"phase manifest hash differs: {filename}"]
    if _manifest_digest(disk) != disk.get("manifest_sha256"):
        return [f"phase manifest hash is invalid: {filename}"]
    return []


def _rebuild_current(checkpoint_root: Path, manifest: dict) -> None:
    temporary = checkpoint_root / f".current.recover.{os.getpid()}.tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    _materialize(checkpoint_root, manifest["inventory"], temporary)
    current = checkpoint_root / "current"
    stale = checkpoint_root / ".current.stale"
    if stale.exists():
        shutil.rmtree(stale)
    if current.exists():
        os.replace(current, stale)
    os.replace(temporary, current)
    if stale.exists():
        shutil.rmtree(stale)


def _rebuild_phase_manifests(connection: sqlite3.Connection, row: sqlite3.Row, checkpoint_root: Path) -> None:
    """Restore exactly the latest complete manifest for each accepted phase."""
    accepted: dict[str, dict] = {}
    current: sqlite3.Row | None = row
    while current is not None:
        if current["status"] == "complete" and current["phase"] not in accepted:
            accepted[current["phase"]] = _manifest(current)
        current = _row(connection, current["previous_checkpoint_id"])
    phase_root = checkpoint_root / "phases"
    phase_root.mkdir(parents=True, exist_ok=True)
    expected = set()
    for phase, manifest in accepted.items():
        _, filename = _phase_info(phase)
        expected.add(filename)
        _atomic_json(phase_root / filename, manifest)
    for path in phase_root.glob("*.json"):
        if path.name not in expected:
            path.unlink()
    _fsync_dir(phase_root)


def validate(target: Path, repair_current: bool = True) -> dict:
    """Validate HEAD, every object hash, and the exact current mirror."""
    connection, row, manifest = _select_complete(target)
    checkpoint_root = root(target)
    errors = _verify_inventory(checkpoint_root, manifest.get("inventory", []))
    if errors:
        connection.close()
        raise CheckpointError("; ".join(errors[:8]))
    current_errors = _verify_inventory(
        checkpoint_root, manifest.get("inventory", []), checkpoint_root / "current"
    )
    phase_errors = _verify_phase_manifest(checkpoint_root, manifest)
    expected = {item["path"] for item in manifest.get("inventory", [])}
    actual = {
        path.relative_to(checkpoint_root / "current").as_posix()
        for path in _files(checkpoint_root / "current")
    }
    current_errors.extend(f"unexpected current artifact: {path}" for path in sorted(actual - expected))
    recovered = row["checkpoint_id"] != _read_head(checkpoint_root) or bool(current_errors) or bool(phase_errors)
    if recovered and repair_current:
        _rebuild_current(checkpoint_root, manifest)
        _rebuild_phase_manifests(connection, row, checkpoint_root)
        _atomic_bytes(checkpoint_root / "HEAD", (row["checkpoint_id"] + "\n").encode("ascii"))
        _atomic_json(checkpoint_root / "snapshot.json", manifest)
    elif current_errors or phase_errors:
        connection.close()
        raise CheckpointError("; ".join((current_errors + phase_errors)[:8]))
    connection.close()
    return {
        "ok": True,
        "checkpoint_id": row["checkpoint_id"],
        "phase": row["phase"],
        "semantic_status": row["semantic_status"],
        "snapshot_commit": row["snapshot_commit"],
        "input_fingerprint": row["input_fingerprint"],
        "artifact_count": len(manifest.get("inventory", [])),
        "rolled_back": recovered,
        "manifest": manifest,
    }


def restore(target: Path, destination: Path) -> dict:
    """Restore the exact accepted checkpoint into a detached worktree."""
    checked = validate(target)
    checkpoint_root = root(target)
    current = checkpoint_root / "current"
    fm_source = current / "fm_agent"
    fm_destination = destination / "fm_agent"
    if fm_destination.exists():
        shutil.rmtree(fm_destination)
    if fm_source.is_dir():
        shutil.copytree(fm_source, fm_destination, copy_function=shutil.copy2)
    recovery = current / "recovery"
    skill = state.skill_dir(destination)
    for name in RECOVERY_FILES:
        source = recovery / name
        output = skill / name
        output.unlink(missing_ok=True)
        if source.is_file():
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
    for name in RECOVERY_DIRS:
        source, output = recovery / name, skill / name
        if output.exists():
            shutil.rmtree(output)
        if source.is_dir():
            shutil.copytree(source, output, copy_function=shutil.copy2)
    # Verify destination content, including tombstone semantics: destination is
    # reconstructed from an exact inventory, never merged with stale outputs.
    errors = []
    for item in checked["manifest"].get("inventory", []):
        if item["path"].startswith("fm_agent/"):
            output = destination / item["path"]
        elif item["path"].startswith("recovery/"):
            output = skill / item["path"][len("recovery/"):]
        else:
            continue
        if not output.is_file() or state.file_hash(output) != item["sha256"]:
            errors.append(item["path"])
    if errors:
        raise CheckpointError("restored artifact hash mismatch: " + ", ".join(errors[:8]))
    return {**checked, "destination": str(destination), "restored": True}


def rebuild_worktree(source: Path) -> dict:
    """Create a new detached cache from the active ref and durable checkpoint."""
    source = source.resolve()
    commit = _run(source, "rev-parse", "--verify", ACTIVE_REF, check=False)
    if not commit:
        raise CheckpointError(f"active Git ref is missing: {ACTIVE_REF}")
    checked = validate(source)
    if checked["snapshot_commit"] != commit:
        raise CheckpointError(
            "active Git ref and checkpoint snapshot differ: "
            f"ref={commit}, checkpoint={checked['snapshot_commit']}"
        )
    parent = Path(tempfile.mkdtemp(prefix="fm-agent-skill-worktree-"))
    destination = parent / "project"
    try:
        _run(source, "worktree", "add", "--detach", "--quiet", str(destination), commit)
        restore(source, destination)
        marker = {
            "schema_version": 4,
            "source_project": str(source),
            "snapshot": str(destination),
            "snapshot_commit": commit,
            "checkpoint_head": checked["checkpoint_id"],
            "reconstructed_at": state.now(),
        }
        _atomic_json(state.skill_dir(destination) / "isolation.json", marker)
        _atomic_json(state.skill_dir(source) / "isolation.json", marker)
        return {**marker, "restored_phase": checked["phase"], "artifact_count": checked["artifact_count"]}
    except Exception:
        subprocess.run(
            ["git", "-C", str(source), "worktree", "remove", "--force", str(destination)],
            text=True, capture_output=True,
        )
        shutil.rmtree(parent, ignore_errors=True)
        raise


def inspect_auto_resume(
    source: Path,
    fingerprint: str | None = None,
    config: dict | None = None,
) -> dict:
    """Return or reconstruct an automatically resumable analysis cache."""
    source = source.resolve()
    if _coordinator_lease_active(source):
        return {"ok": False, "reason": "automatic resume conditions differ", "differences": ["coordinator_lease"]}
    marker = state.read_json(state.skill_dir(source) / "isolation.json", {})
    snapshot_value = marker.get("snapshot") if isinstance(marker, dict) else None
    if isinstance(snapshot_value, str) and Path(snapshot_value).is_dir():
        snapshot = Path(snapshot_value).resolve()
        checked = validate(source)
        if state.current_snapshot_commit(snapshot) != checked["snapshot_commit"]:
            return {"ok": False, "reason": "existing worktree snapshot commit differs from checkpoint"}
        if marker.get("checkpoint_head") not in {None, checked["checkpoint_id"]}:
            return {"ok": False, "reason": "existing worktree checkpoint HEAD differs from durable HEAD"}
    else:
        try:
            rebuilt = rebuild_worktree(source)
        except CheckpointError as exc:
            return {"ok": False, "reason": str(exc)}
        snapshot = Path(rebuilt["snapshot"])
        checked = validate(source)
    record = state.active_record(snapshot)
    differences = []
    if record.get("snapshot_commit") != checked["snapshot_commit"]:
        differences.append("snapshot_commit")
    if record.get("fingerprint") != checked["input_fingerprint"]:
        differences.append("checkpoint_fingerprint")
    if fingerprint is not None and record.get("fingerprint") != fingerprint:
        differences.append("requested_fingerprint")
    saved_config = record.get("inputs", {}).get("config")
    source_config = state.read_json(state.skill_dir(source) / "config.json", None)
    if isinstance(source_config, dict) and source_config != saved_config:
        differences.append("source_configuration")
    if config is not None and saved_config != config:
        differences.append("configuration")
    if differences:
        return {
            "ok": False, "reason": "automatic resume conditions differ",
            "differences": differences, "snapshot": str(snapshot),
        }
    return {
        "ok": True, "snapshot": str(snapshot), "analysis": record,
        "checkpoint": checked, "auto_resume": True,
    }


def _coordinator_lease_active(target: Path) -> bool:
    path = db_path(target)
    if not path.is_file():
        return False
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coordinator_leases'"
        ).fetchone()
        if not row:
            return False
        row = connection.execute(
            "SELECT owner, heartbeat_at, expires_at FROM coordinator_leases "
            "WHERE lease_id='active' LIMIT 1"
        ).fetchone()
        if row is None or row["expires_at"] <= state.now():
            return False
        # A coordinator that ran on this machine and whose process no longer
        # exists cannot own the analysis, even if an old broad lock TTL has
        # not elapsed yet.
        owner = row["owner"]
        if isinstance(owner, str) and ":" in owner:
            host, value = owner.rsplit(":", 1)
            try:
                pid = int(value)
                if host == socket.gethostname():
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        return False
                    except PermissionError:
                        # A live process owned by another local account must
                        # retain its lease; only a proven missing PID is stale.
                        pass
            except ValueError:
                pass
        try:
            config = load(target)
            grace = max(
                int(config.get("resume_grace_seconds", 600)),
                int(config.get("worker_lease_seconds", 900)),
            )
            heartbeat = state.dt.datetime.fromisoformat(row["heartbeat_at"])
            age = (state.dt.datetime.now(state.dt.timezone.utc) - heartbeat).total_seconds()
            return age <= grace
        except (TypeError, ValueError):
            return False
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage durable FM-Agent checkpoints.")
    parser.add_argument("action", choices=("commit", "validate", "restore", "rebuild", "inspect"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--phase")
    parser.add_argument("--status", default="succeeded")
    parser.add_argument("--scheduler-receipt", default="")
    parser.add_argument("--destination")
    parser.add_argument("--fingerprint")
    parser.add_argument("--config-json")
    args = parser.parse_args()
    target = project(args)
    try:
        if args.action == "commit":
            if not args.phase:
                raise CheckpointError("commit requires --phase")
            result = commit(target, args.phase, args.status, args.scheduler_receipt)
        elif args.action == "validate":
            result = validate(target)
        elif args.action == "restore":
            if not args.destination:
                raise CheckpointError("restore requires --destination")
            result = restore(target, Path(args.destination).resolve())
        elif args.action == "rebuild":
            result = rebuild_worktree(target)
        else:
            config = json.loads(args.config_json) if args.config_json else None
            result = inspect_auto_resume(target, args.fingerprint, config)
        code = 0 if result.get("ok", True) else 2
    except (CheckpointError, InjectedCrash, OSError, ValueError, sqlite3.Error) as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
