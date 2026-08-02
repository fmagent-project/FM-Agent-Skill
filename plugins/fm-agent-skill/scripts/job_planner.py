#!/usr/bin/env python3
"""Create complete, deterministic semantic job queues for a pipeline phase."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from _common import project, state
import complexity
import scheduler


SUPPORTED_PHASES = {"specification", "verification", "verify_affected", "bug_validation"}


def _active(target: Path, phase: str) -> dict:
    record = state.active_record(target)
    if not isinstance(record, dict) or record.get("status") != "running":
        raise ValueError("job planning requires a running FM-Agent analysis")
    current = record.get("current_phase")
    # Semantic phases are strictly ordered.  Later queues must not be seeded
    # while an earlier phase is still running; this prevents partial
    # verification/Bug Validation results from being mistaken for a complete
    # pipeline after a Coordinator interruption.
    if phase != current:
        raise ValueError(f"cannot plan {phase} while earliest incomplete phase is {current}")
    if state.current_snapshot_commit(target) != record.get("snapshot_commit"):
        raise ValueError("active analysis snapshot does not match the current worktree")
    return record


def _selected(target: Path, record: dict, phase: str) -> list[dict]:
    submodules = record.get("inputs", {}).get("submodules", [])
    functions = state.scoped_functions(target, submodules if isinstance(submodules, list) else [])
    if phase in {"verify_affected", "bug_validation"} and record.get("mode") == "incremental":
        decision = state.read_json(state.control_dir(target) / "incremental_decision.json", {})
        included = decision.get("included") if isinstance(decision, dict) else None
        if not isinstance(included, dict):
            raise ValueError("incremental job planning requires a valid included-function decision")
        functions = [item for item in functions if item.get("id") in included]
    return functions


def _config(target: Path, record: dict) -> dict:
    saved = state.read_json(state.skill_dir(target) / "config.json", {})
    inputs = record.get("inputs") if isinstance(record.get("inputs"), dict) else {}
    config = dict(saved) if isinstance(saved, dict) else {}
    if isinstance(inputs.get("config"), dict):
        config.update(inputs["config"])
    return config


def _ensure_job(target: Path, payload: dict) -> tuple[str, bool]:
    path = state.skill_dir(target) / "jobs" / f"{payload['id']}.json"
    if not path.exists():
        scheduler.create(target, payload)
        return payload["id"], True
    existing = state.read_json(path, {})
    expected = {
        "id": payload["id"],
        "phase": payload["phase"],
        "type": payload["type"],
        "depends_on": payload.get("depends_on", []),
        "required_outputs": payload.get("required_outputs", []),
        "artifacts": payload.get("artifacts", []),
    }
    if any(existing.get(key) != value for key, value in expected.items()):
        raise ValueError(f"existing job conflicts with deterministic plan: {payload['id']}")
    return payload["id"], False


def _existing_job(target: Path, phase: str, kind: str, artifacts: list[str] | None = None, function_id: str | None = None) -> dict | None:
    matches = []
    connection = scheduler._connect(target)
    rows = connection.execute(
        "SELECT payload_json FROM jobs WHERE phase=? AND type=? ORDER BY job_id",
        (phase, kind),
    ).fetchall()
    connection.close()
    for row in rows:
        job = json.loads(row["payload_json"])
        if not isinstance(job, dict) or job.get("phase") != phase or job.get("type") != kind:
            continue
        if artifacts is not None and job.get("artifacts") != artifacts:
            continue
        if function_id is not None and job.get("input", {}).get("function_id") != function_id:
            continue
        matches.append(job)
    if len(matches) > 1:
        raise ValueError(f"multiple existing jobs own the same {kind} input")
    return matches[0] if matches else None


def _context_outputs(target: Path) -> list[str]:
    phases = state.read_json(state.fm_dir(target) / "phases.json", {}).get("phases", [])
    outputs = [
        "fm_agent/spec_prompts/system_prompt.md",
        "fm_agent/spec_prompts/domain_context/engine_overview.txt",
    ]
    for index, item in enumerate(phases, 1):
        number = item.get("phase", index) if isinstance(item, dict) else index
        outputs.append(f"fm_agent/spec_prompts/domain_context/phase_{number:02d}_types.txt")
    return outputs


def _layer_files(target: Path) -> list[Path]:
    root = state.fm_dir(target) / "spec_prompts"
    files = sorted(root.glob("phase_*_topdown_layers.json"))
    ready, reason = state.phase_layers_ready(target)
    if not ready:
        raise ValueError(reason)
    return files


def _specification(target: Path, record: dict, phase: str) -> dict:
    selected = _selected(target, record, phase)
    expected = {item["artifact"] for item in selected}
    config = _config(target, record)
    created = 0
    context_id = None
    if not state.specification_context_ready(target)[0]:
        existing_context = _existing_job(target, phase, "domain_context", artifacts=[])
        if existing_context:
            context_id, made = existing_context["id"], False
        else:
            context_id, made = _ensure_job(target, {
                "id": f"{phase}-domain-context", "phase": phase, "type": "domain_context",
                "depends_on": [], "required_outputs": _context_outputs(target), "artifacts": [],
            })
        created += int(made)

    entries: list[dict] = []
    seen: set[str] = set()
    phase_terminals: dict[int, list[str]] = {}
    phases = state.read_json(state.fm_dir(target) / "phases.json", {}).get("phases", [])
    phase_dependencies = {
        item.get("phase", index): item.get("depends_on_phases", [])
        for index, item in enumerate(phases, 1) if isinstance(item, dict)
    }
    for layer_file in _layer_files(target):
        data = state.read_json(layer_file, {})
        phase_number = data.get("phase")
        prior_layer_jobs: list[str] = []
        phase_base = [context_id] if context_id else []
        for dependency_phase in phase_dependencies.get(phase_number, []):
            phase_base.extend(phase_terminals.get(dependency_phase, []))
        for layer in data.get("layers", []):
            layer_number = layer.get("layer")
            pending = []
            for function in layer.get("functions", []):
                artifact = function.get("artifact")
                if artifact not in expected:
                    continue
                if artifact in seen:
                    raise ValueError(f"function appears more than once in job plan: {artifact}")
                seen.add(artifact)
                source = state.fm_dir(target) / "extracted_functions" / artifact
                if state.sidecars_ready(source)[0]:
                    entries.append({"artifact": artifact, "job_id": None})
                else:
                    pending.append(artifact)
            current_layer_jobs = []
            for batch_number, batch_info in enumerate(complexity.partition(target, pending, config, "spec_batch"), 1):
                batch = batch_info["artifacts"]
                job_id = f"{phase}-spec-p{phase_number:02d}-l{layer_number:03d}-b{batch_number:03d}"
                dependencies = list(dict.fromkeys([*phase_base, *prior_layer_jobs]))
                outputs = [value for artifact in batch for value in (
                    f"fm_agent/extracted_functions/{artifact}.spec.json",
                    f"fm_agent/extracted_functions/{artifact}.info.json",
                )]
                existing_job = _existing_job(target, phase, "spec_batch", artifacts=batch)
                if existing_job:
                    job_id, made = existing_job["id"], False
                else:
                    _, made = _ensure_job(target, {
                        "id": job_id, "phase": phase, "type": "spec_batch",
                        "depends_on": dependencies, "required_outputs": outputs, "artifacts": batch,
                        "input": {"complexity": batch_info},
                    })
                created += int(made); current_layer_jobs.append(job_id)
                entries.extend({"artifact": artifact, "job_id": job_id} for artifact in batch)
            if current_layer_jobs:
                prior_layer_jobs = current_layer_jobs
        phase_terminals[phase_number] = prior_layer_jobs or phase_base
    if seen != expected:
        missing = sorted(expected - seen)
        raise ValueError("phase layers do not cover selected functions: " + ", ".join(missing[:3]))
    result = _write_plan(target, record, phase, entries, created)
    # Build downstream jobs now. Their per-function dependencies keep them
    # blocked until the corresponding spec batch succeeds, enabling streaming
    # without weakening caller-first specification ordering.
    if config.get("spec_batch_size") is None:
        downstream = "verify_affected" if record.get("mode") == "incremental" else "verification"
        result["streaming_verification_plan"] = _verification(target, record, downstream)["plan_path"]
    return result


def _verification(target: Path, record: dict, phase: str) -> dict:
    functions = _selected(target, record, phase)
    entries, created = [], 0
    pending = []
    spec_plan = state.read_json(state.control_dir(target) / "job_plans" / "specification.json", {})
    if record.get("mode") == "incremental":
        spec_plan = state.read_json(state.control_dir(target) / "job_plans" / "update_specs.json", spec_plan)
    spec_jobs = {
        entry.get("artifact"): entry.get("job_id") for entry in spec_plan.get("entries", [])
        if isinstance(entry, dict) and isinstance(entry.get("artifact"), str)
    }
    function_by_artifact = {item["artifact"]: item for item in functions}
    for item in sorted(functions, key=lambda value: value["artifact"]):
        artifact = item["artifact"]
        source = state.fm_dir(target) / "extracted_functions" / artifact
        output = f"fm_agent/logic_verification_results/{Path(artifact).with_suffix('.json').as_posix()}"
        result = state.read_json(target / output, None)
        if state.verification_result_ready(target, source, item["id"], result)[0]:
            entries.append({"artifact": artifact, "job_id": None}); continue
        pending.append(artifact)
    batches = complexity.partition(target, pending, _config(target, record), "verify_batch")
    # A pair is cheaper and more failure-isolated as two compatibility jobs;
    # normal batches begin at three functions, while simple layers can grow to
    # the configured 10-20 range.
    batches = [
        {**batch, "artifacts": [artifact]}
        for batch in batches if len(batch["artifacts"]) < 3 for artifact in batch["artifacts"]
    ] + [batch for batch in batches if len(batch["artifacts"]) >= 3]
    for ordinal, batch in enumerate(batches, 1):
        artifacts = batch["artifacts"]
        outputs = [f"fm_agent/logic_verification_results/{Path(item).with_suffix('.json').as_posix()}" for item in artifacts]
        dependencies = list(dict.fromkeys(spec_jobs.get(item) for item in artifacts if spec_jobs.get(item)))
        kind = "verify_function" if len(artifacts) == 1 else "verify_batch"
        job_id = f"{phase}-verify-{ordinal:05d}"
        existing_job = _existing_job(target, phase, kind, artifacts=artifacts)
        if existing_job:
            job_id, made = existing_job["id"], False
        else:
            _, made = _ensure_job(target, {
                "id": job_id, "phase": phase, "type": kind,
                "depends_on": dependencies, "required_outputs": outputs, "artifacts": artifacts,
                "input": {
                    "function_ids": [function_by_artifact[item]["id"] for item in artifacts],
                    "complexity": batch,
                },
            })
        created += int(made)
        entries.extend({"artifact": artifact, "job_id": job_id} for artifact in artifacts)
    return _write_plan(target, record, phase, entries, created)


def _bug_validation(target: Path, record: dict, phase: str) -> dict:
    functions = _selected(target, record, phase)
    selected_ids = {item["id"] for item in functions}
    candidates = state.direct_mismatch_ids(target, selected_ids if record.get("mode") == "incremental" else None)
    entries, created = [], 0
    for function_id in sorted(candidates):
        digest = hashlib.sha256(function_id.encode("utf-8")).hexdigest()[:16]
        job_id = f"bug-validation-{digest}"
        output = f"fm_agent/bug_validation/{digest}.result.json"
        existing_job = _existing_job(target, phase, "bug_validate", function_id=function_id)
        if existing_job:
            job_id, made = existing_job["id"], False
        else:
            _, made = _ensure_job(target, {
                "id": job_id, "phase": phase, "type": "bug_validate", "depends_on": [],
                "required_outputs": [output], "artifacts": [],
                "input": {"bug_id": digest, "function_id": function_id, "mode": record["mode"]},
            })
        created += int(made); entries.append({"function_id": function_id, "job_id": job_id})
    return _write_plan(target, record, phase, entries, created)


def _write_plan(target: Path, record: dict, phase: str, entries: list[dict], created: int) -> dict:
    plan = {
        "schema_version": 1, "phase": phase, "snapshot_commit": record["snapshot_commit"],
        "entries": entries, "created_jobs": created, "total_entries": len(entries),
    }
    path = state.control_dir(target) / "job_plans" / f"{phase}.json"
    state.atomic_json(path, plan)
    return {"plan_path": path.relative_to(target).as_posix(), **plan}


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan every semantic job for one current FM-Agent phase.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--phase", required=True, choices=tuple(sorted(SUPPORTED_PHASES)))
    args = parser.parse_args(); target = project(args)
    try:
        record = _active(target, args.phase)
        if args.phase == "specification":
            result = _specification(target, record, args.phase)
        elif args.phase in {"verification", "verify_affected"}:
            result = _verification(target, record, args.phase)
        else:
            result = _bug_validation(target, record, args.phase)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
