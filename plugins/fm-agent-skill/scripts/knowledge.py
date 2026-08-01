#!/usr/bin/env python3
"""Materialize immutable, fingerprinted user knowledge for specification workers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile

from _common import project, state


def safe_name(index: int, source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name).strip("-.") or "knowledge.md"
    return f"{index:03d}-{stem}"


def materialize(target: Path) -> dict:
    record = state.active_record(target)
    inputs = record.get("inputs") if isinstance(record, dict) else None
    knowledge = inputs.get("knowledge") if isinstance(inputs, dict) else None
    if not isinstance(knowledge, list):
        raise ValueError("active analysis has no immutable knowledge input list")
    root = state.fm_dir(target) / "spec_prompts" / "domain_context" / "user_knowledge"
    validated = []
    for index, item in enumerate(knowledge, start=1):
        source_value = item.get("path") if isinstance(item, dict) else None
        expected_hash = item.get("sha256") if isinstance(item, dict) else None
        source = Path(source_value).resolve() if isinstance(source_value, str) else None
        if source is None or not source.is_file() or source.suffix.lower() not in {".md", ".markdown"}:
            raise ValueError("knowledge input is missing or is not Markdown")
        if not isinstance(expected_hash, str) or state.file_hash(source) != expected_hash:
            raise ValueError(f"knowledge input changed after analysis dispatch: {source}")
        validated.append((index, source, expected_hash))
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="user-knowledge-", dir=root.parent))
    backup = Path(tempfile.mkdtemp(prefix="user-knowledge-backup-", dir=root.parent))
    backup.rmdir()
    entries = []
    try:
        for index, source, expected_hash in validated:
            destination = staging / safe_name(index, source)
            shutil.copy2(source, destination)
            entries.append({
                "original_path": str(source),
                "copied_path": (root / destination.name).relative_to(target).as_posix(),
                "sha256": expected_hash,
            })
        manifest = {"schema_version": 1, "snapshot_commit": state.current_snapshot_commit(target), "entries": entries}
        state.atomic_json(staging / "manifest.json", manifest)
        had_previous = root.exists()
        if had_previous:
            root.replace(backup)
        try:
            staging.replace(root)
        except Exception:
            if had_previous and backup.exists() and not root.exists():
                backup.replace(root)
            raise
        if had_previous:
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not root.exists():
            backup.replace(root)
        elif backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        raise
    return {"materialized": len(entries), "manifest": (root / "manifest.json").relative_to(target).as_posix()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("materialize",))
    parser.add_argument("--project", required=True)
    args = parser.parse_args(); target = project(args)
    try:
        result, code = materialize(target), 0
    except (OSError, ValueError) as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(code)


if __name__ == "__main__":
    main()
