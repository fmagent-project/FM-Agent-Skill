#!/usr/bin/env python3
"""Validate phase contracts before pipeline state may advance."""
from __future__ import annotations

import argparse
import json

from _common import project, state


def json_object(path): return isinstance(state.read_json(path, None), dict)


def has_direct_mismatch(target):
    results = state.fm_dir(target) / "logic_verification_results"
    for path in results.rglob("*.json") if results.is_dir() else []:
        if state.read_json(path, {}).get("verdict") == "MISMATCH":
            return True
    return False


def call_graph_ready(target):
    fm = state.fm_dir(target)
    precision = state.read_json(state.control_dir(target) / "call_graph_precision.json", None)
    return (
        isinstance(precision, dict)
        and precision.get("backend") in {"codegraph", "agent-static"}
        and precision.get("precision") in {"exact", "best-effort"}
        and state.phase_layers_ready(target)[0]
    )

def validate(target, mode, phase, submodules):
    fm = state.fm_dir(target)
    checks = {
        "preflight": lambda: state.preflight(target)["ok"],
        "project_understanding": lambda: json_object(fm / "phases.json"),
        "phase_cleanup": lambda: json_object(fm / "phases.json"),
        "extraction": lambda: bool(state.scoped_functions(target, submodules)),
        "call_graph": lambda: call_graph_ready(target),
        "specification": lambda: state.specification_artifacts_ready(target, state.scoped_functions(target, submodules), submodules)[0],
        "verification": lambda: state.function_artifacts_ready(target, state.scoped_functions(target, submodules), submodules)[0],
        "bug_validation": lambda: (not has_direct_mismatch(target)) or json_object(fm / "bug_validation" / "summary.json"),
        "finalize": lambda: True,
        "validate_baseline": lambda: state.scoped_functions(target, submodules) != [],
        "refresh_plan": lambda: json_object(fm / "phases.json"),
        "preserve_specs": lambda: (state.control_dir(target) / "preserved_specs.json").is_file(),
        "diff": lambda: (state.control_dir(target) / "diff.json").is_file(),
        "rebuild_graph": lambda: call_graph_ready(target),
        "select_scope": lambda: json_object(state.control_dir(target) / "incremental_decision.json"),
        "update_specs": lambda: state.specification_artifacts_ready(target, state.scoped_functions(target, submodules), submodules)[0] and (fm / "incremental_updated_specs.json").is_file(),
        "verify_affected": lambda: state.function_artifacts_ready(target, state.scoped_functions(target, submodules), submodules)[0],
    }
    check = checks.get(phase)
    if check is None: return {"ok": False, "reason": f"unknown {mode} phase: {phase}"}
    try: ok = bool(check())
    except OSError: ok = False
    return {"ok": ok, "phase": phase, "reason": "" if ok else f"required artifacts for {phase} are missing or invalid"}


def main():
    parser = argparse.ArgumentParser(description="Check a deterministic FM-Agent phase gate.")
    parser.add_argument("--project", required=True); parser.add_argument("--mode", required=True, choices=tuple(state.PHASES)); parser.add_argument("--phase", required=True); parser.add_argument("--submodule", action="append", default=[])
    args = parser.parse_args(); result = validate(project(args), args.mode, args.phase, args.submodule)
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__": main()
