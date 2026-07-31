#!/usr/bin/env python3
"""Create and validate the Skill-owned source/function inventory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import project, state


def language_for(path: str) -> str:
    return {".py": "python", ".go": "go", ".rs": "rust", ".java": "java", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".ets": "arkts", ".cu": "cuda", ".cuh": "cuda"}.get(Path(path).suffix.lower(), "c++")


def build(target, scope):
    root = state.fm_dir(target) / "extracted_functions"
    manifest = state.read_json(state.fm_dir(target) / "extraction_manifest.json", {}).get("functions", [])
    metadata = {item.get("artifact"): item for item in manifest if isinstance(item, dict) and isinstance(item.get("artifact"), str)}
    functions = []
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if not path.is_file() or state.is_metadata_sidecar(path): continue
        rel = path.relative_to(root).as_posix()
        if scope and not any(rel == item.rstrip("/") or rel.startswith(item.rstrip("/") + "/") for item in scope): continue
        details = metadata.get(rel, {})
        function_id = details.get("function_id")
        if not isinstance(function_id, str):
            raise ValueError(f"extraction manifest lacks a stable function identity for {rel}")
        source_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        functions.append({"id": function_id, "path": details.get("source_path", rel), "artifact": rel, "line_start": details.get("line_start", 1), "line_end": details.get("line_end", max(1, len(source_lines))), "language": language_for(details.get("source_path", rel)), "scope": scope})
    data = {"schema_version": 2, "generated_at": state.now(), "snapshot_commit": state.current_snapshot_commit(target), "functions": functions}
    state.atomic_json(state.control_dir(target) / "analysis_index.json", data)
    return data


def main():
    parser = argparse.ArgumentParser(description="Build or validate the Skill control analysis index.")
    parser.add_argument("action", choices=("build", "validate")); parser.add_argument("--project", required=True); parser.add_argument("--submodule", action="append", default=[])
    args = parser.parse_args(); target = project(args)
    if args.action == "build": result = build(target, args.submodule); code = 0 if result["functions"] else 2
    else:
        functions = state.scoped_functions(target, args.submodule)
        ready, reason = state.function_artifacts_ready(target, functions, args.submodule) if functions else (False, "missing indexed functions")
        result = {"ok": ready, "function_count": len(functions), "reason": reason}; code = 0 if ready else 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(code)


if __name__ == "__main__": main()
