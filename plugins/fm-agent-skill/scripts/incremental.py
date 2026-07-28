#!/usr/bin/env python3
"""Apply Coordinator-approved incremental selections and sidecar plans."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import project, state


def decision_path(target: Path) -> Path:
    return state.control_dir(target) / "incremental_decision.json"


def decision(target: Path) -> dict:
    data = state.read_json(decision_path(target), {})
    if not isinstance(data, dict): data = {}
    data.setdefault("schema_version", 1); data.setdefault("included", {}); data.setdefault("excluded", {}); data.setdefault("removed_artifacts", [])
    if not isinstance(data["included"], dict) or not isinstance(data["excluded"], dict): raise ValueError("incremental decision has invalid selection maps")
    return data


def merge_selection(target: Path, record: Path, default_reason: str) -> dict:
    payload = state.read_json(record, {})
    if not isinstance(payload, dict): raise ValueError("selection record must be a JSON object")
    data = decision(target)
    selected = payload.get("selected_function_ids", payload.get("included", []))
    excluded = payload.get("excluded_function_ids", payload.get("excluded", []))
    if isinstance(selected, dict): selected = list(selected)
    if isinstance(excluded, dict): excluded = list(excluded)
    if not isinstance(selected, list) or not isinstance(excluded, list) or not all(isinstance(item, str) for item in selected + excluded):
        raise ValueError("selection record must use string selected_function_ids and excluded_function_ids arrays")
    known = {item.get("id") for item in state.source_index(target).get("functions", []) if isinstance(item, dict)}
    for function_id in selected:
        if function_id not in known: raise ValueError(f"selection references unknown function: {function_id}")
        data["included"][function_id] = default_reason; data["excluded"].pop(function_id, None)
    for function_id in excluded:
        if function_id not in known: raise ValueError(f"selection references unknown function: {function_id}")
        if function_id not in data["included"]: data["excluded"][function_id] = default_reason
    data["generated_at"] = state.now(); state.atomic_json(decision_path(target), data)
    return data


def apply_plan(target: Path, plan_path: Path) -> dict:
    plan = state.read_json(plan_path, {})
    updates = plan.get("sidecar_updates") if isinstance(plan, dict) else None
    if not isinstance(updates, dict): raise ValueError("plan must contain sidecar_updates object")
    root = (state.fm_dir(target) / "extracted_functions").resolve(); applied = []
    for rel, update in updates.items():
        if not isinstance(rel, str) or not isinstance(update, dict): raise ValueError("invalid sidecar update")
        artifact = (root / rel).resolve()
        if root not in artifact.parents or not artifact.is_file(): raise ValueError(f"plan references missing extracted artifact: {rel}")
        spec, info = update.get("spec"), update.get("info")
        if not isinstance(spec, dict) or not isinstance(info, dict): raise ValueError(f"plan update requires spec and info objects: {rel}")
        state.atomic_json(Path(f"{artifact}.spec.json"), spec); state.atomic_json(Path(f"{artifact}.info.json"), info); applied.append(rel)
    report = state.fm_dir(target) / "incremental_updated_specs.json"
    prior = state.read_json(report, {}); artifacts = set(prior.get("artifacts", [])) if isinstance(prior, dict) else set()
    artifacts.update(applied)
    state.atomic_json(report, {"schema_version": 1, "generated_at": state.now(), "artifacts": sorted(artifacts)})
    return {"applied": len(applied), "artifacts": sorted(applied)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Coordinator-approved incremental Skill artifacts.")
    parser.add_argument("action", choices=("init", "include", "exclude", "merge-selection", "apply-plan", "show")); parser.add_argument("--project", required=True)
    parser.add_argument("--function"); parser.add_argument("--reason"); parser.add_argument("--record"); parser.add_argument("--plan")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "init":
            data = {"schema_version": 1, "generated_at": state.now(), "included": {}, "excluded": {}, "removed_artifacts": []}; state.atomic_json(decision_path(target), data); result = data
        elif args.action in ("include", "exclude"):
            if not args.function or not args.reason: parser.error("include/exclude require --function and --reason")
            result = decision(target); result["included" if args.action == "include" else "excluded"][args.function] = args.reason; state.atomic_json(decision_path(target), result)
        elif args.action == "merge-selection":
            if not args.record or not args.reason: parser.error("merge-selection requires --record and --reason")
            result = merge_selection(target, Path(args.record), args.reason)
        elif args.action == "apply-plan":
            if not args.plan: parser.error("apply-plan requires --plan")
            result = apply_plan(target, Path(args.plan))
        else: result = decision(target)
        code = 0
    except ValueError as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(code)


if __name__ == "__main__": main()
