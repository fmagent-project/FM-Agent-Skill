#!/usr/bin/env python3
"""Validate phase contracts before pipeline state may advance."""
from __future__ import annotations

import argparse
import json

from _common import project, state


def json_object(path): return isinstance(state.read_json(path, None), dict)


def direct_mismatch_ids(target, mode):
    selected = None
    if mode == "incremental":
        decision = state.read_json(state.control_dir(target) / "incremental_decision.json", {})
        included = decision.get("included") if isinstance(decision, dict) else None
        if not isinstance(included, dict): return set()
        selected = set(included)
    return state.direct_mismatch_ids(target, selected)


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


def bug_validation_current(target, mode):
    candidates = direct_mismatch_ids(target, mode)
    plan_ok, plan_reason = state.semantic_job_plan_ready(target, "bug_validation", candidate_ids=candidates)
    if not plan_ok:
        return False, plan_reason
    return (True, "") if not candidates else state.bug_validation_ready(target, candidates)


def call_graph_ready(target):
    fm = state.fm_dir(target)
    precision = state.read_json(state.control_dir(target) / "call_graph_precision.json", None)
    return (
        isinstance(precision, dict)
        and precision.get("backend") in {"codegraph", "agent-static"}
        and precision.get("precision") in {"exact", "best-effort"}
        and state.phase_layers_ready(target)[0]
    )


def all_ready(*checks):
    for check in checks:
        if isinstance(check, tuple):
            if not check[0]:
                return False, check[1]
        elif not check:
            return False, "required artifact is missing or invalid"
    return True, ""

def validate(target, mode, phase, submodules):
    fm = state.fm_dir(target)
    scoped = state.scoped_functions(target, submodules)
    selected = selected_functions(target)
    checks = {
        "preflight": lambda: state.preflight(target)["ok"],
        "project_understanding": lambda: state.phases_schema_ready(target)[0],
        "phase_cleanup": lambda: state.phases_schema_ready(target)[0],
        "extraction": lambda: bool(state.scoped_functions(target, submodules)),
        "call_graph": lambda: call_graph_ready(target),
        "specification": lambda: all_ready(state.semantic_job_plan_ready(target, "specification", scoped), state.specification_context_ready(target), state.specification_artifacts_ready(target, scoped, submodules)),
        "verification": lambda: all_ready(
            state.semantic_job_plan_ready(target, "verification", scoped),
            state.function_artifacts_ready(target, scoped, submodules),
            state.verification_coverage_ready(target, scoped),
        ),
        "bug_validation": lambda: bug_validation_current(target, mode),
        "finalize": lambda: True,
        "validate_baseline": lambda: baseline_ready(target, submodules),
        "refresh_plan": lambda: state.phases_schema_ready(target)[0],
        "preserve_specs": lambda: (state.control_dir(target) / "preserved_specs.json").is_file(),
        "diff": lambda: (state.control_dir(target) / "diff.json").is_file(),
        "rebuild_graph": lambda: call_graph_ready(target),
        "select_scope": lambda: selection_ready(target),
        "update_specs": lambda: all_ready(state.specification_context_ready(target), state.specification_artifacts_ready(target, scoped, submodules), ((fm / "incremental_updated_specs.json").is_file(), "missing incremental specification update record")),
        "verify_affected": lambda: all_ready(
            (selection_ready(target), "missing incremental selection"),
            state.semantic_job_plan_ready(target, "verify_affected", selected),
            state.selected_verification_ready(target, selected),
            state.verification_coverage_ready(target, selected) if selected else (True, ""),
        ),
    }
    check = checks.get(phase)
    if check is None: return {"ok": False, "reason": f"unknown {mode} phase: {phase}"}
    try:
        outcome = check()
        if isinstance(outcome, tuple):
            ok, reason = bool(outcome[0]), outcome[1]
        else:
            ok, reason = bool(outcome), ""
        if ok and not state.snapshot_sources_clean(target):
            ok, reason = False, "snapshot production source changed during analysis"
    except OSError as exc:
        ok, reason = False, str(exc)
    failure_reason = "" if ok else (reason or f"required artifacts for {phase} are missing or invalid")
    classification = "ready" if ok else "insufficient_specification" if failure_reason.startswith("insufficient_specification:") else "invalid"
    result = {"ok": ok, "phase": phase, "reason": failure_reason, "classification": classification}
    if phase in {"verification", "verify_affected"}:
        result["coverage"] = state.verification_coverage(target, scoped if phase == "verification" else selected)
    return result


def main():
    parser = argparse.ArgumentParser(description="Check a deterministic FM-Agent phase gate.")
    parser.add_argument("--project", required=True); parser.add_argument("--mode", required=True, choices=tuple(state.PHASES)); parser.add_argument("--phase", required=True); parser.add_argument("--submodule", action="append", default=[])
    args = parser.parse_args(); result = validate(project(args), args.mode, args.phase, args.submodule)
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__": main()
