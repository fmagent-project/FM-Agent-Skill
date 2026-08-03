#!/usr/bin/env python3
"""The sole deterministic authority for terminal FM-Agent reporting."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import project, state
import checkpoint
from isolation import marker


def _phase_manifests(target: Path) -> list[dict]:
    phases = checkpoint.root(target) / "phases"
    values = [state.read_json(path, {}) for path in sorted(phases.glob("*.json"))] if phases.is_dir() else []
    return [
        value for value in values
        if isinstance(value, dict) and value.get("schema_version") == checkpoint.CHECKPOINT_SCHEMA_VERSION
    ]


def _analysis_target(target: Path) -> Path:
    """Read live artifacts from the isolated snapshot when it still exists."""
    data = marker(target)
    snapshot = data.get("snapshot") if isinstance(data, dict) else None
    if isinstance(snapshot, str) and Path(snapshot).is_dir():
        return Path(snapshot).resolve()
    return target


def _verification_results(target: Path, snapshot_commit: str) -> list[dict]:
    root = state.fm_dir(target) / "logic_verification_results"
    index = state.source_index(target) or {}
    functions = {
        item.get("artifact"): item for item in index.get("functions", [])
        if isinstance(item, dict) and isinstance(item.get("artifact"), str)
    }
    results = []
    for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
        relative = path.relative_to(root).with_suffix("").as_posix()
        item = functions.get(relative) or functions.get(relative + path.suffix)
        if item is None:
            # Result paths are formed by replacing the extracted artifact
            # suffix, so compare against that canonical transformation.
            item = next((
                value for artifact, value in functions.items()
                if Path(artifact).with_suffix(".json").as_posix() == path.relative_to(root).as_posix()
            ), None)
        payload = state.read_json(path, None)
        if not isinstance(item, dict) or not isinstance(payload, dict):
            continue
        artifact = state.fm_dir(target) / "extracted_functions" / item["artifact"]
        valid, _ = state.verification_result_ready(target, artifact, item.get("id"), payload)
        if valid and payload.get("snapshot_commit") == snapshot_commit:
            results.append(payload)
    return results


def _bug_results(target: Path, snapshot_commit: str, candidates: set[str]) -> list[dict]:
    root = state.fm_dir(target) / "bug_validation"
    try:
        import scheduler
        jobs = {
            job.get("bug_result_path"): job
            for job in scheduler.jobs_for_phase(target, "bug_validation")
            if job.get("type") == "bug_validate" and not job.get("legacy_contract")
        }
    except (OSError, RuntimeError, ValueError):
        jobs = {}
    results = []
    for path in sorted(root.glob("*.result.json")) if root.is_dir() else []:
        relative = path.relative_to(target).as_posix()
        job = jobs.get(relative)
        # A file is not an accepted Bug Validator result until the durable
        # scheduler accepted its receipt for this job.
        if jobs and (not isinstance(job, dict) or job.get("status") != "succeeded"):
            continue
        payload = state.read_json(path, None)
        if not isinstance(payload, dict) or payload.get("snapshot_commit") != snapshot_commit:
            continue
        function_id = payload.get("function_id") or payload.get("bug_id")
        if function_id not in candidates:
            continue
        status = payload.get("confirmation_status")
        attempts = payload.get("attempts")
        if status not in state.BUG_FINAL_STATUSES or not isinstance(attempts, list) or not attempts:
            continue
        dynamic = []
        for attempt in attempts:
            valid, _ = state._dynamic_attempt_ready(target, attempt, snapshot_commit)
            if valid:
                dynamic.append(attempt)
        if not dynamic:
            continue
        if status == "confirmed" and not any(item.get("classification") == "confirmed" for item in dynamic):
            continue
        if status == "rejected" and any(item.get("classification") == "confirmed" for item in dynamic):
            continue
        latest = dynamic[-1]
        evidence = latest.get("dynamic_evidence", {})
        result_path = evidence.get("reproduction_result") if isinstance(evidence, dict) else None
        reproduction = state.read_json(target / result_path, {}) if isinstance(result_path, str) else {}
        evidence_reason = reproduction.get("reason") if isinstance(reproduction, dict) else None
        if not isinstance(evidence_reason, str) or not evidence_reason.strip():
            evidence_reason = "recorded dynamic evidence did not provide a more specific reason"
        if status == "rejected":
            interpretation = "not reproduced by the recorded sufficient dynamic attempts; this does not prove that no defect exists"
        elif status == "inconclusive":
            interpretation = "dynamic validation is inconclusive; the candidate remains unresolved"
        else:
            interpretation = "dynamic execution reproduced the contract violation"
        results.append({
            "function_id": function_id,
            "status": status,
            "attempt_count": len(dynamic),
            "result_path": path.relative_to(target).as_posix(),
            "evidence_reason": evidence_reason,
            "interpretation": interpretation,
        })
    return results


def _scheduler_aggregate(target: Path) -> dict:
    try:
        import scheduler
        return scheduler.aggregate(target)
    except (AttributeError, OSError, ValueError, RuntimeError):
        receipts = state.control_dir(target) / "phase_receipts"
        values = [state.read_json(path, {}) for path in sorted(receipts.glob("*.json"))] if receipts.is_dir() else []
        return {"legacy_phase_receipts": [value for value in values if isinstance(value, dict)]}


def official_result_available(target: Path) -> tuple[bool, str | None]:
    authority = state.read_json(checkpoint.root(target) / "official_result.json", {})
    if not isinstance(authority, dict) or authority.get("official_result_available") is not True:
        reason = authority.get("reason") if isinstance(authority, dict) else None
        return False, reason or "the complete FM-Agent DAG and finalize gate have not succeeded"
    return True, None


def build(target: Path) -> dict:
    target = _analysis_target(target)
    checked = checkpoint.validate(target)
    manifests = _phase_manifests(target)
    scheduler = _scheduler_aggregate(target)
    verification = _verification_results(target, checked["snapshot_commit"])
    mismatches = {
        result["function_id"] for result in verification
        if result.get("verdict") == "MISMATCH" and isinstance(result.get("function_id"), str)
    }
    bug_results = _bug_results(target, checked["snapshot_commit"], mismatches)
    confirmed = [item for item in bug_results if item["status"] == "confirmed"]
    rejected = [item for item in bug_results if item["status"] == "rejected"]
    inconclusive = [item for item in bug_results if item["status"] == "inconclusive"]
    official, reason = official_result_available(target)
    # Never report partial dynamic evidence as confirmed bugs. It remains in
    # the durable attempt artifacts for a later accepted completion.
    report_confirmed = confirmed if official else []
    report_rejected = rejected if official else []
    report_inconclusive = inconclusive if official else []
    report = {
        "schema_version": 1,
        "official_result_available": official,
        "status": "official" if official else "incomplete",
        "reason": reason,
        "snapshot_commit": checked["snapshot_commit"],
        "analysis_fingerprint": state.active_record(target).get("fingerprint"),
        "checkpoint_head": checked["checkpoint_id"],
        "phase_manifests": [
            {
                "phase": item.get("phase"), "ordinal": item.get("ordinal"),
                "status": item.get("status"), "checkpoint_id": item.get("checkpoint_id"),
            } for item in manifests
        ],
        "scheduler": scheduler,
        "counts": {
            "verification_results": len(verification),
            "candidates": len(mismatches),
            "confirmed": len(report_confirmed),
            "rejected": len(report_rejected),
            "inconclusive": (
                len(report_inconclusive) + len(mismatches - {item["function_id"] for item in bug_results})
                if official else len(mismatches)
            ),
        },
        "authority_rules": {
            "candidate_requires_schema_valid_mismatch": True,
            "confirmed_requires_dynamic_evidence": True,
            "specification_is_not_bug_evidence": True,
            "rejected_is_not_absence_proof": True,
            "inconclusive_retains_dynamic_evidence_reason": True,
        },
    }
    # An incomplete run deliberately exposes no finding list.  This prevents a
    # Coordinator from laundering static observations into an official answer.
    if official:
        report["findings"] = {
            "candidates": sorted(mismatches),
            "confirmed": report_confirmed,
            "rejected": report_rejected,
            "inconclusive": report_inconclusive,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit the authoritative FM-Agent terminal report.")
    parser.add_argument("--project", required=True)
    args = parser.parse_args(); target = project(args)
    try:
        result, code = build(target), 0
    except (checkpoint.CheckpointError, OSError, ValueError, RuntimeError) as exc:
        result, code = {
            "schema_version": 1, "official_result_available": False,
            "status": "unavailable", "reason": str(exc),
            "counts": {"verification_results": 0, "candidates": 0, "confirmed": 0, "rejected": 0, "inconclusive": 0},
        }, 2
    # Persist the exact structured report so a Stop Hook can distinguish an
    # intentional phase-failure report from an unverified chat summary.
    try:
        state.atomic_json(state.control_dir(target) / "terminal_report.json", result)
    except OSError as exc:
        result = {
            "schema_version": 1, "official_result_available": False,
            "status": "unavailable", "reason": f"could not persist terminal report: {exc}",
            "counts": {"verification_results": 0, "candidates": 0, "confirmed": 0, "rejected": 0, "inconclusive": 0},
        }
        code = 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
