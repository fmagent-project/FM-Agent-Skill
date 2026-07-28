#!/usr/bin/env python3
"""Host-neutral deterministic FM-Agent Skill executor.

This module deliberately never imports or invokes the original FM-Agent. The
Claude/Codex Coordinator invokes its actions between semantic worker jobs.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import shutil

from _common import project, state
from artifact_index import build as build_index


FUNCTION = re.compile(r"^\s*(?:[\w:<>,~*&]+\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?\{", re.MULTILINE)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or "function"


def _python_functions(path: Path) -> list[tuple[str, int, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    return [(node.name, node.lineno, getattr(node, "end_lineno", node.lineno)) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _fallback_functions(path: Path) -> list[tuple[str, int, int]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    found = []
    for match in FUNCTION.finditer(text):
        start = text.count("\n", 0, match.start()) + 1
        depth = 0; end_offset = match.end() - 1
        for offset in range(match.end() - 1, len(text)):
            if text[offset] == "{": depth += 1
            elif text[offset] == "}":
                depth -= 1
                if depth == 0:
                    end_offset = offset; break
        found.append((match.group(1), start, text.count("\n", 0, end_offset) + 1))
    return found or [(path.stem, 1, max(1, text.count("\n") + 1))]


def extract(target: Path, submodules: list[str]) -> dict:
    root = state.fm_dir(target) / "extracted_functions"
    if root.exists(): shutil.rmtree(root)
    manifest = []
    for source in state.source_files(target):
        rel = source.relative_to(target).as_posix()
        if submodules and not any(rel == item.rstrip("/") or rel.startswith(item.rstrip("/") + "/") for item in submodules): continue
        try: functions = _python_functions(source) if source.suffix.lower() == ".py" else _fallback_functions(source)
        except (SyntaxError, OSError): functions = _fallback_functions(source)
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        for index, (name, start, end) in enumerate(functions, 1):
            artifact = Path(rel).parent / f"{_safe(Path(rel).stem)}-{_safe(name)}-{index}{source.suffix.lower()}"
            destination = root / artifact; destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("".join(lines[start - 1:end]), encoding="utf-8")
            manifest.append({"artifact": artifact.as_posix(), "source_path": rel, "name": name, "line_start": start, "line_end": end})
    state.atomic_json(state.fm_dir(target) / "extraction_manifest.json", {"schema_version": 1, "generated_at": state.now(), "functions": manifest})
    state.atomic_json(state.fm_dir(target) / "fm_agent_file_list.json", sorted(item["artifact"] for item in manifest))
    index = build_index(target, submodules)
    return {"function_count": len(manifest), "index": index}


def _source_path(target: Path, value: str) -> str:
    path = Path(value)
    try: return (path if path.is_absolute() else target / path).resolve().relative_to(target.resolve()).as_posix()
    except ValueError: return value.replace("\\", "/").lstrip("./")


def _validate_edges(target: Path, payload: dict) -> list[dict]:
    edges = payload.get("edges") if isinstance(payload, dict) else None
    if not isinstance(edges, list): raise ValueError("edge payload must contain an edges array")
    known = {item.get("artifact") for item in state.source_index(target).get("functions", []) if isinstance(item, dict)}
    result = []; seen = set()
    for edge in edges:
        if not isinstance(edge, dict): raise ValueError("every edge must be an object")
        caller, callee = edge.get("caller_artifact"), edge.get("callee_artifact")
        if not isinstance(caller, str) or not isinstance(callee, str) or caller not in known or callee not in known or caller == callee:
            raise ValueError("edge must reference two distinct current extracted artifacts")
        key = (caller, callee)
        if key not in seen:
            result.append({"caller_artifact": caller, "callee_artifact": callee, "kind": "calls", "evidence": edge.get("evidence", "host-static")}); seen.add(key)
    return result


def record_agent_edges(target: Path, edge_file: Path) -> dict:
    fm = state.fm_dir(target).resolve(); candidate = edge_file.resolve()
    if fm not in candidate.parents: raise ValueError("agent-static edge candidate must be written under fm_agent/")
    edges = _validate_edges(target, state.read_json(candidate, {}))
    data = {"schema_version": 1, "backend": "agent-static", "generated_at": state.now(), "edges": edges}
    state.atomic_json(state.control_dir(target) / "agent_static_edges.json", data)
    return {"edge_count": len(edges)}


def _graph_data(target: Path, manifest: list[dict], export_path: Path | None) -> tuple[list[dict], str, str]:
    if export_path is None:
        saved = state.read_json(state.control_dir(target) / "agent_static_edges.json", {})
        return _validate_edges(target, saved) if saved else [], "agent-static", "best-effort"
    export = state.read_json(export_path, {})
    nodes, edges = export.get("nodes"), export.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("CodeGraph export must contain nodes and edges arrays")
    by_source: dict[str, list[dict]] = {}
    for item in manifest:
        if isinstance(item, dict) and isinstance(item.get("source_path"), str):
            by_source.setdefault(item["source_path"], []).append(item)
    node_artifacts = {}; unmapped = []
    for node in nodes:
        if not isinstance(node, dict): continue
        node_id, line = node.get("node_id"), node.get("line_start")
        if not isinstance(node_id, (int, str)) or not isinstance(line, int): continue
        source = _source_path(target, str(node.get("path", "")))
        matches = [item for item in by_source.get(source, []) if item.get("line_start", 0) <= line <= item.get("line_end", 0)]
        if matches:
            matches.sort(key=lambda item: item.get("line_end", 0) - item.get("line_start", 0))
            node_artifacts[str(node_id)] = matches[0]["artifact"]
        elif source in by_source:
            unmapped.append(f"{source}:{line}")
    if unmapped:
        raise ValueError(f"CodeGraph nodes do not map to current extraction: {', '.join(unmapped[:3])}")
    resolved = []
    for edge in edges:
        if not isinstance(edge, dict): continue
        caller = node_artifacts.get(str(edge.get("source_node_id")))
        callee = node_artifacts.get(str(edge.get("target_node_id")))
        if caller and callee and caller != callee:
            resolved.append({"caller_artifact": caller, "callee_artifact": callee, "kind": edge.get("kind", "calls")})
    return resolved, "codegraph", "exact"


def _layers(entries: list[dict], edges: list[dict]) -> list[dict]:
    ids = {item["id"] for item in entries}; by_artifact = {item["artifact"]: item["id"] for item in entries}
    outgoing = {function_id: set() for function_id in ids}; incoming = {function_id: set() for function_id in ids}
    for edge in edges:
        caller = by_artifact.get(edge.get("caller_artifact")); callee = by_artifact.get(edge.get("callee_artifact"))
        if caller in ids and callee in ids and callee not in outgoing[caller]:
            outgoing[caller].add(callee); incoming[callee].add(caller)
    remaining = set(ids); result = []
    while remaining:
        ready = sorted(item for item in remaining if not (incoming[item] & remaining))
        if not ready: ready = [sorted(remaining)[0]]
        result.append({"layer": len(result) + 1, "functions": [{"function_id": item, "artifact": next(value["artifact"] for value in entries if value["id"] == item)} for item in ready]})
        remaining.difference_update(ready)
    return result


def graph(target: Path, codegraph_export: Path | None = None) -> dict:
    phases = state.read_json(state.fm_dir(target) / "phases.json", {})
    index = state.source_index(target) or {}; functions = index.get("functions", [])
    manifest = state.read_json(state.fm_dir(target) / "extraction_manifest.json", {}).get("functions", [])
    source_for = {item.get("artifact"): item.get("source_path") for item in manifest if isinstance(item, dict)}
    edges, backend, precision = _graph_data(target, manifest, codegraph_export)
    by_source = {}
    for item in functions:
        by_source.setdefault(source_for.get(item.get("artifact"), item.get("path")), []).append(item)
    written = []
    for position, phase in enumerate(phases.get("phases", []), 1):
        number = phase.get("phase", position); sources = []
        for module in phase.get("modules", []): sources.extend(module.get("source_files", []))
        sources = sorted(set(sources)); entries = [item for source in sources for item in by_source.get(source, [])]
        layers = _layers(entries, edges)
        for layer in layers:
            for function in layer["functions"]:
                function["source_file"] = source_for.get(function["artifact"], "")
        payload = {"phase": number, "phase_name": phase.get("name", f"phase-{number}"), "source_files": sources, "total_layers": len(layers), "layers": layers}
        path = state.fm_dir(target) / "spec_prompts" / f"phase_{number:02d}_topdown_layers.json"; state.atomic_json(path, payload); written.append(path.name)
    state.atomic_json(state.control_dir(target) / "graph_edges.json", {"schema_version": 1, "backend": backend, "precision": precision, "edges": edges, "generated_at": state.now()})
    state.atomic_json(state.control_dir(target) / "call_graph_precision.json", {"backend": backend, "precision": precision, "reason": "CodeGraph export mapped to extracted artifacts" if backend == "codegraph" else "deterministic extraction inventory; semantic edge resolution is delegated to the Coordinator", "generated_at": state.now()})
    return {"layers": written, "function_count": len(functions), "edge_count": len(edges), "backend": backend}


def preserve_specs(target: Path) -> dict:
    root = state.fm_dir(target) / "extracted_functions"; saved = {}
    for artifact in root.rglob("*") if root.is_dir() else []:
        if not artifact.is_file() or state.is_metadata_sidecar(artifact): continue
        spec, info = Path(f"{artifact}.spec.json"), Path(f"{artifact}.info.json")
        if spec.is_file() and info.is_file():
            rel = artifact.relative_to(root).as_posix()
            saved[rel] = {"source_hash": state.file_hash(artifact), "spec": state.read_json(spec, {}), "info": state.read_json(info, {})}
    files = {source.relative_to(target).as_posix(): state.file_hash(source) for source in state.source_files(target)}
    data = {"schema_version": 1, "generated_at": state.now(), "files": files, "artifacts": saved}
    state.atomic_json(state.control_dir(target) / "preserved_specs.json", data); return {"preserved": len(saved)}


def restore_specs(target: Path) -> dict:
    """Restore only unchanged paired sidecars after a fresh extraction."""
    saved = state.read_json(state.control_dir(target) / "preserved_specs.json", {}).get("artifacts", {})
    root = state.fm_dir(target) / "extracted_functions"; restored = []
    for rel, value in saved.items() if isinstance(saved, dict) else []:
        if not isinstance(value, dict): continue
        artifact = root / rel
        if not artifact.is_file() or state.file_hash(artifact) != value.get("source_hash"): continue
        spec, info = value.get("spec"), value.get("info")
        if not isinstance(spec, dict) or not isinstance(info, dict): continue
        state.atomic_json(Path(f"{artifact}.spec.json"), spec)
        state.atomic_json(Path(f"{artifact}.info.json"), info)
        restored.append(rel)
    state.atomic_json(state.control_dir(target) / "restored_specs.json", {"schema_version": 1, "generated_at": state.now(), "artifacts": restored})
    return {"restored": len(restored)}


def diff(target: Path) -> dict:
    preserved = state.read_json(state.control_dir(target) / "preserved_specs.json", {})
    old = preserved.get("artifacts", {})
    current = {item["artifact"]: item for item in state.source_index(target).get("functions", [])} if state.source_index(target) else {}
    manifest = state.read_json(state.fm_dir(target) / "extraction_manifest.json", {}).get("functions", [])
    source_for = {item.get("artifact"): item.get("source_path") for item in manifest if isinstance(item, dict)}
    old_files = preserved.get("files", {}) if isinstance(preserved.get("files", {}), dict) else {}
    current_files = {source.relative_to(target).as_posix(): state.file_hash(source) for source in state.source_files(target)}
    changed_files = {path for path in set(old_files) | set(current_files) if old_files.get(path) != current_files.get(path)}
    added = sorted(key for key in current if key not in old)
    removed = sorted(key for key in old if key not in current)
    modified = sorted(key for key in current if key in old and current[key].get("source_hash") != old[key].get("source_hash"))
    file_changed = sorted(key for key in current if source_for.get(key) in changed_files)
    results = state.fm_dir(target) / "logic_verification_results"
    for artifact in set(file_changed) | set(modified) | set(removed):
        (results / Path(artifact).with_suffix(".json")).unlink(missing_ok=True)
    data = {"schema_version": 1, "generated_at": state.now(), "added": added, "modified": modified, "removed": removed, "file_changes": sorted(changed_files), "file_changed_artifacts": file_changed}
    state.atomic_json(state.control_dir(target) / "diff.json", data); return data


def select(target: Path) -> dict:
    changes = state.read_json(state.control_dir(target) / "diff.json", {})
    affected = set(changes.get("added", [])) | set(changes.get("modified", [])) | set(changes.get("file_changed_artifacts", []))
    functions = state.source_index(target).get("functions", []) if state.source_index(target) else []
    by_artifact = {item.get("artifact"): item.get("id") for item in functions}
    included = {item["id"]: "file-change" for item in functions if item.get("artifact") in affected}
    edges = state.read_json(state.control_dir(target) / "graph_edges.json", {}).get("edges", [])
    changed = True
    while changed:
        changed = False
        for edge in edges if isinstance(edges, list) else []:
            if not isinstance(edge, dict): continue
            caller, callee = edge.get("caller_artifact"), edge.get("callee_artifact")
            caller_id, callee_id = by_artifact.get(caller), by_artifact.get(callee)
            if caller_id in included and callee_id and callee_id not in included: included[callee_id] = "callee-propagation"; changed = True
            if callee_id in included and caller_id and caller_id not in included: included[caller_id] = "caller-propagation"; changed = True
    excluded = {item["id"]: "unchanged" for item in functions if item.get("id") not in included}
    data = {"schema_version": 1, "generated_at": state.now(), "included": included, "excluded": excluded, "removed_artifacts": changes.get("removed", [])}
    state.atomic_json(state.control_dir(target) / "incremental_decision.json", data); return {"included": len(included), "excluded": len(excluded), "removed": len(data["removed_artifacts"])}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic FM-Agent Skill executor actions without original FM-Agent.")
    parser.add_argument("action", choices=("extract", "graph", "record-agent-edges", "preserve-specs", "restore-specs", "diff", "select")); parser.add_argument("--project", required=True); parser.add_argument("--submodule", action="append", default=[]); parser.add_argument("--codegraph-export"); parser.add_argument("--edges-file")
    args = parser.parse_args(); target = project(args)
    if args.action == "record-agent-edges" and not args.edges_file: parser.error("record-agent-edges requires --edges-file")
    result = {"extract": lambda: extract(target, args.submodule), "graph": lambda: graph(target, Path(args.codegraph_export) if args.codegraph_export else None), "record-agent-edges": lambda: record_agent_edges(target, Path(args.edges_file)), "preserve-specs": lambda: preserve_specs(target), "restore-specs": lambda: restore_specs(target), "diff": lambda: diff(target), "select": lambda: select(target)}[args.action]()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
