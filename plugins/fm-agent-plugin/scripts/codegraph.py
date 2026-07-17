#!/usr/bin/env python3
"""Detect and initialize the optional CodeGraph backend for an FM-Agent run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from _common import project
from config import load


def resolve_command(target: Path, explicit: str | None = None) -> tuple[str | None, str]:
    configured = explicit if explicit is not None else load(target).get("codegraph_path")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate.resolve()), "configured-path"
        found = shutil.which(configured)
        return (found, "configured-command") if found else (None, "configured-command")
    found = shutil.which("codegraph")
    return (found, "PATH") if found else (None, "PATH")


def status(target: Path, explicit: str | None = None) -> dict:
    command, source = resolve_command(target, explicit)
    index = target / ".codegraph" / "codegraph.db"
    index_readable = False
    if index.is_file():
        try:
            connection = sqlite3.connect(f"file:{index.as_posix()}?mode=ro", uri=True)
            try:
                connection.execute("SELECT 1 FROM nodes LIMIT 1")
            finally:
                connection.close()
            index_readable = True
        except sqlite3.Error:
            pass
    return {
        "available": command is not None,
        "command": command,
        "command_source": source,
        "index_path": str(index),
        "index_exists": index.is_file(),
        "index_readable": index_readable,
    }


def initialize(
    target: Path, explicit: str | None = None, rebuild: bool = False
) -> tuple[int, dict]:
    result = status(target, explicit)
    if not result["available"]:
        result["error"] = "CodeGraph command is unavailable"
        return 2, result
    # `codegraph init` reuses an existing index. Rebuilding prevents a full or
    # incremental analysis from reading boundaries for an older source tree.
    # The run skill permits this only after user authorization and under its lock.
    index_dir = target / ".codegraph"
    if rebuild and index_dir.exists():
        try:
            shutil.rmtree(index_dir)
        except OSError as exc:
            result["error"] = f"cannot remove existing .codegraph index: {exc}"
            return 2, result
        result["previous_index_removed"] = True
    completed = subprocess.run(
        [result["command"], "init"], cwd=target, text=True, capture_output=True
    )
    result.update({
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    })
    result.update(status(target, explicit))
    if completed.returncode == 0 and not result["index_exists"]:
        result["error"] = "CodeGraph completed without creating .codegraph/codegraph.db"
        return 2, result
    return completed.returncode, result


def export_index(target: Path) -> tuple[int, dict]:
    result = status(target)
    if not result["index_readable"]:
        result["error"] = "No readable .codegraph/codegraph.db index is available"
        return 2, result
    try:
        connection = sqlite3.connect(f"file:{Path(result['index_path']).as_posix()}?mode=ro", uri=True)
        try:
            nodes = connection.execute(
                "SELECT id, name, qualified_name, file_path, language, start_line, end_line "
                "FROM nodes WHERE kind IN ('function', 'method') "
                "ORDER BY file_path, start_line, id"
            ).fetchall()
            edges = connection.execute(
                "SELECT source, target, kind FROM edges WHERE kind IN ('calls', 'instantiates') "
                "ORDER BY source, target, kind"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return 2, result
    result.update({
        "nodes": [
            {"node_id": item[0], "name": item[1], "qualified_name": item[2], "path": item[3].replace("\\", "/"), "language": item[4], "line_start": item[5], "line_end": item[6]}
            for item in nodes
        ],
        "edges": [{"source_node_id": item[0], "target_node_id": item[1], "kind": item[2]} for item in edges],
    })
    return 0, result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or initialize the optional CodeGraph index.")
    parser.add_argument("action", choices=("status", "init", "export"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--command", help="CodeGraph executable path or command; overrides saved configuration")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="remove the existing generated .codegraph directory before initialization",
    )
    args = parser.parse_args()
    target = project(args)
    if args.action == "status": code, result = 0, status(target, args.command)
    elif args.action == "init": code, result = initialize(target, args.command, args.rebuild)
    else: code, result = export_index(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
