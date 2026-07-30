#!/usr/bin/env python3
"""Validate phase contracts before pipeline state may advance."""
from __future__ import annotations

import argparse
import json

from _common import project, state


def json_object(path): return isinstance(state.read_json(path, None), dict)


def has_direct_mismatch(target, mode):
    selected = None
    if mode == "incremental":
        decision = state.read_json(state.control_dir(target) / "incremental_decision.json", {})
        if not isinstance(decision.get("included"), dict): return False
        selected = set(decision["included"])
    results = state.fm_dir(target) / "logic_verification_results"
    for path in results.rglob("*.json") if results.is_dir() else []:
        result = state.read_json(path, {})
        if result.get("verdict") == "MISMATCH" and (selected is None or result.get("function_id") in selected):
            return True
    return False


def selected_functions(target):
    decision = state.read_json(state.control_dir(target) / "incremental_decision.json", {})
    included = decision.get("included", {})
    if not isinstance(included, dict): return []
    return [item for item in state.scoped_functions(target, []) if item.get("id") in included]


def selection_ready(target):
    decision = state.read_json(state.control_dir(target) / "incremental_decision.json", {})
    return isinstance(decision.get("included"), dict) and isinstance(decision.get("removed_artifacts"), list)


def baseline_ready(target, submodules):
    active = state.active_record(target)
    fingerprint = active.get("fingerprint") if isinstance(active, dict) else None
    return isinstance(fingerprint, str) and state.inspect_baseline(target, fingerprint, submodules).get("valid", False)


def bug_summary_current(target):
    summary = state.read_json(state.fm_dir(target) / "bug_validation" / "summary.json", {})
    return isinstance(summary, dict)


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
        "project_understanding": lambda: state.phases_schema_ready(target)[0],
        "phase_cleanup": lambda: state.phases_schema_ready(target)[0],
        "extraction": lambda: bool(state.scoped_functions(target, submodules)),
        "call_graph": lambda: call_graph_ready(target),
        "specification": lambda: state.specification_context_ready(target)[0] and state.specification_artifacts_ready(target, state.scoped_functions(target, submodules), submodules)[0],
        "verification": lambda: state.function_artifacts_ready(target, state.scoped_functions(target, submodules), submodules)[0],
        "bug_validation": lambda: (not has_direct_mismatch(target, mode)) or bug_summary_current(target),
        "finalize": lambda: True,
        "validate_baseline": lambda: baseline_ready(target, submodules),
        "refresh_plan": lambda: state.phases_schema_ready(target)[0],
        "preserve_specs": lambda: (state.control_dir(target) / "preserved_specs.json").is_file(),
        "diff": lambda: (state.control_dir(target) / "diff.json").is_file(),
        "rebuild_graph": lambda: call_graph_ready(target),
        "select_scope": lambda: selection_ready(target),
        "update_specs": lambda: state.specification_context_ready(target)[0] and state.specification_artifacts_ready(target, state.scoped_functions(target, submodules), submodules)[0] and (fm / "incremental_updated_specs.json").is_file(),
        "verify_affected": lambda: selection_ready(target) and state.selected_verification_ready(target, selected_functions(target))[0],
    }
    check = checks.get(phase)
    if check is None: return {"ok": False, "reason": f"unknown {mode} phase: {phase}"}
    try: ok = bool(check()) and state.snapshot_sources_clean(target)
    except OSError: ok = False
    return {"ok": ok, "phase": phase, "reason": "" if ok else f"required artifacts for {phase} are missing, invalid, or the snapshot source changed"}


def main():
    parser = argparse.ArgumentParser(description="Check a deterministic FM-Agent phase gate.")
    parser.add_argument("--project", required=True); parser.add_argument("--mode", required=True, choices=tuple(state.PHASES)); parser.add_argument("--phase", required=True); parser.add_argument("--submodule", action="append", default=[])
    args = parser.parse_args(); result = validate(project(args), args.mode, args.phase, args.submodule)
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__": main()
