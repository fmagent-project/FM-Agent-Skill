#!/usr/bin/env python3
"""Deterministic semantic Worker complexity estimation and partitioning."""
from __future__ import annotations

import math
from pathlib import Path
import re
import sqlite3

from _common import state
import checkpoint


BRANCH = re.compile(r"\b(if|else\s+if|for|while|case|catch|except|when)\b|&&|\|\||\?")
LANGUAGE_FACTORS = {
    ".c": 1.10, ".cc": 1.15, ".cpp": 1.15, ".cxx": 1.15,
    ".h": 0.90, ".hpp": 0.90, ".java": 1.05, ".kt": 1.05,
    ".ets": 1.10, ".ts": 1.00, ".js": 0.95, ".py": 0.90,
    ".go": 1.00, ".rs": 1.10,
}


def _historical_seconds(target: Path, job_type: str) -> float:
    path = checkpoint.db_path(target)
    if not path.is_file(): return 0.0
    connection = sqlite3.connect(path)
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempts'").fetchone()
        if not exists: return 0.0
        row = connection.execute(
            "SELECT AVG((julianday(completed_at)-julianday(started_at))*86400.0) "
            "FROM attempts a JOIN jobs j ON j.job_id=a.job_id "
            "WHERE j.type=? AND a.status='succeeded' AND completed_at IS NOT NULL",
            (job_type,),
        ).fetchone()
        return max(0.0, float(row[0] or 0.0))
    except sqlite3.Error:
        return 0.0
    finally:
        connection.close()


def estimate(target: Path, artifact: str, job_type: str = "spec_batch") -> dict:
    path = state.fm_dir(target) / "extracted_functions" / artifact
    payload = path.read_bytes() if path.is_file() else b""
    text = payload.decode("utf-8", errors="replace")
    lines = text.count("\n") + (1 if text else 0)
    token_estimate = max(1, math.ceil(len(text) / 4))
    graph = state.read_json(state.control_dir(target) / "graph_edges.json", {})
    neighbors = set()
    for edge in graph.get("edges", []) if isinstance(graph, dict) else []:
        if not isinstance(edge, dict): continue
        caller, callee = edge.get("caller_artifact"), edge.get("callee_artifact")
        if caller == artifact and isinstance(callee, str): neighbors.add(callee)
        if callee == artifact and isinstance(caller, str): neighbors.add(caller)
    info = state.read_json(Path(f"{path}.info.json"), {})
    callees = info.get("callees", []) if isinstance(info, dict) else []
    callee_specs = sum(
        1 for item in callees if isinstance(item, dict)
        if all(isinstance(item.get(key), str) and item[key].strip() for key in state.CALLEE_FIELD_ORDER)
    )
    cyclomatic = 1 + len(BRANCH.findall(text))
    factor = LANGUAGE_FACTORS.get(path.suffix.lower(), 1.10)
    history = _historical_seconds(target, job_type)
    score = math.ceil((
        token_estimate + len(neighbors) * 80 + callee_specs * 120 + cyclomatic * 100 + lines * 2
    ) * factor * (1.0 + min(history, 300.0) / 1200.0))
    return {
        "artifact": artifact, "source_lines": lines, "source_bytes": len(payload),
        "token_estimate": token_estimate, "graph_neighbors": len(neighbors),
        "callee_specs": callee_specs, "cyclomatic_approximation": cyclomatic,
        "language": path.suffix.lower().lstrip(".") or "unknown",
        "historical_worker_seconds": round(history, 3), "complexity_tokens": score,
    }


def partition(target: Path, artifacts: list[str], config: dict, job_type: str) -> list[dict]:
    target_tokens = config.get("worker_target_tokens", 12000)
    max_functions = config.get("worker_max_functions", 20)
    max_bytes = config.get("worker_max_source_bytes", 262144)
    legacy = config.get("spec_batch_size") if job_type == "spec_batch" else None
    if isinstance(legacy, int) and legacy > 0:
        max_functions = min(max_functions, legacy)
    for name, value in (("worker_target_tokens", target_tokens), ("worker_max_functions", max_functions), ("worker_max_source_bytes", max_bytes)):
        if not isinstance(value, int) or value < 1: raise ValueError(f"{name} must be a positive integer")
    estimates = [estimate(target, artifact, job_type) for artifact in artifacts]
    batches: list[dict] = []
    current: list[dict] = []
    current_tokens = current_bytes = 0
    for item in estimates:
        overflow = current and (
            len(current) >= max_functions or current_tokens + item["complexity_tokens"] > target_tokens
            or current_bytes + item["source_bytes"] > max_bytes
        )
        if overflow:
            batches.append(_batch(current)); current = []; current_tokens = current_bytes = 0
        current.append(item); current_tokens += item["complexity_tokens"]; current_bytes += item["source_bytes"]
        if item["complexity_tokens"] >= target_tokens or item["source_bytes"] >= max_bytes:
            batches.append(_batch(current)); current = []; current_tokens = current_bytes = 0
    if current: batches.append(_batch(current))
    return batches


def _batch(items: list[dict]) -> dict:
    return {
        "artifacts": [item["artifact"] for item in items],
        "estimated_tokens": sum(item["complexity_tokens"] for item in items),
        "source_bytes": sum(item["source_bytes"] for item in items),
        "estimates": items,
    }
