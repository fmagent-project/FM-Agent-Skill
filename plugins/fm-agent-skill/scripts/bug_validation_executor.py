#!/usr/bin/env python3
"""Deterministic Bug Validator state machine for a Codex/Claude Coordinator.

This script never starts an LLM, imports FM-Agent, or accepts an Agent command.
It exposes exactly the next host-worker request, while it owns every local
transition: admission, build evidence, sandbox execution, receipt validation,
retry, and terminal summary.  A Codex/Claude Coordinator remains responsible
only for the two semantic passes of ``fm-bug-validate-worker``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import project, state
import scheduler
from bug_summary import write_summary
from probe_runner import configured_adapter, run_probe
from reproduction_runner import attempt_dir, read_contract, run as run_reproduction


def load_job(target: Path, job_id: str) -> dict:
    job = scheduler._load(target, job_id)
    if job.get("type") != "bug_validate" or job.get("legacy_contract"):
        raise ValueError("bug-validation executor requires a current bug_validate job")
    return job


def context(job: dict) -> tuple[str, str]:
    payload = job.get("input") if isinstance(job.get("input"), dict) else {}
    bug_id, mode = payload.get("bug_id"), payload.get("mode")
    if not isinstance(bug_id, str) or not bug_id:
        raise ValueError("bug_validate job input requires a non-empty bug_id")
    if mode not in {"full", "incremental"}:
        raise ValueError("bug_validate job input requires mode full or incremental")
    return bug_id, mode


def worker_request(target: Path, job: dict, pass_name: str) -> dict:
    bug_id, mode = context(job)
    attempt = int(job.get("attempts", 0))
    if attempt < 1:
        raise ValueError("running Bug Validator job has no attempt")
    root = attempt_dir(target, bug_id, attempt).relative_to(target).as_posix()
    return {
        "action": "host_worker",
        "worker": "fm-bug-validate-worker",
        "pass": pass_name,
        "job_id": job["id"],
        "mode": mode,
        "bug_id": bug_id,
        "attempt": attempt,
        "attempt_dir": root,
        "required_outputs": (
            [f"{root}/reproduction.json", f"{root}/probe.<language-extension>"]
            if pass_name == "preparation" else [job["bug_result_path"]]
        ),
        "rule": "Invoke this only through the active Codex/Claude host worker mechanism; do not run FM-Agent or pass a shell command.",
    }


def finalization_matches_current_attempt(target: Path, job: dict) -> bool:
    """A prior negative report never satisfies a retried dynamic attempt."""
    bug_id, _ = context(job); attempt = int(job.get("attempts", 0))
    report = state.read_json(target / job["bug_result_path"], {})
    attempts = report.get("attempts") if isinstance(report, dict) else None
    if not isinstance(attempts, list) or not attempts:
        return False
    latest = attempts[-1]
    if not isinstance(latest, dict) or latest.get("ordinal") != attempt:
        return False
    evidence = latest.get("dynamic_evidence")
    expected = attempt_dir(target, bug_id, attempt) / "reproduction_result.json"
    return isinstance(evidence, dict) and evidence.get("reproduction_result") == expected.relative_to(target).as_posix()


def next_action(target: Path, job_id: str) -> dict:
    job = load_job(target, job_id)
    if job["status"] == "queued":
        return {"action": "scheduler_start", "job_id": job_id, "rule": "Call start only after this job appears in scheduler admissible output."}
    if job["status"] == "retryable":
        return {"action": "scheduler_retry", "job_id": job_id}
    if job["status"] != "running":
        return {"action": "terminal", "job_id": job_id, "status": job["status"]}
    bug_id, _ = context(job)
    root = attempt_dir(target, bug_id, int(job["attempts"]))
    if not (root / "reproduction.json").is_file():
        return worker_request(target, job, "preparation")
    # Validate the preparation contract before a host may ask for finalization.
    read_contract(target, bug_id, int(job["attempts"]))
    if not (root / "reproduction_result.json").is_file():
        return {"action": "run_dynamic", "job_id": job_id, "bug_id": bug_id, "attempt": int(job["attempts"])}
    if not finalization_matches_current_attempt(target, job):
        return worker_request(target, job, "finalization")
    return {"action": "submit_finalization_receipt", "job_id": job_id, "result_path": job["bug_result_path"]}


def start(target: Path, job_id: str) -> dict:
    job = load_job(target, job_id)
    if job["status"] != "queued":
        raise ValueError("only a queued Bug Validator job can start")
    admitted = scheduler.admissible(target).get("jobs", [])
    if not any(item.get("id") == job_id for item in admitted):
        raise ValueError("job is not scheduler-admissible")
    scheduler.transition(target, job_id, "start", None, None, "execution")
    return next_action(target, job_id)


def retry(target: Path, job_id: str) -> dict:
    job = load_job(target, job_id)
    if job["status"] != "retryable":
        raise ValueError("only a retryable Bug Validator job can requeue")
    scheduler.transition(target, job_id, "retry", None, None, "execution")
    return next_action(target, job_id)


def run_dynamic(target: Path, job_id: str) -> dict:
    job = load_job(target, job_id)
    if job["status"] != "running":
        raise ValueError("dynamic reproduction requires a running Bug Validator job")
    bug_id, _ = context(job); attempt = int(job["attempts"])
    root, _ = read_contract(target, bug_id, attempt)
    if not (root / "build_result.json").exists():
        run_probe(target, bug_id, attempt, configured_adapter(target, None), 120, None)
    result = run_reproduction(target, bug_id, attempt)
    if result.get("state") == "execution_error":
        failed = scheduler.transition(target, job_id, "fail", None, str(result.get("reason", "dynamic runner failed")), "execution")
        if failed.get("status") == "retryable":
            scheduler.transition(target, job_id, "retry", None, None, "execution")
            return next_action(target, job_id)
        return {"action": "terminal", "job_id": job_id, "status": failed.get("status")}
    return next_action(target, job_id)


def all_phase_jobs_succeeded(target: Path, phase: str) -> bool:
    root = state.skill_dir(target) / "jobs"
    jobs = [state.read_json(path, {}) for path in root.glob("*.json")] if root.is_dir() else []
    relevant = [job for job in jobs if isinstance(job, dict) and job.get("phase") == phase and job.get("type") == "bug_validate"]
    return bool(relevant) and all(job.get("status") == "succeeded" for job in relevant)


def submit_finalization(target: Path, job_id: str, receipt: dict) -> dict:
    job = load_job(target, job_id); _, mode = context(job)
    if not finalization_matches_current_attempt(target, job):
        raise ValueError("finalization report does not append evidence for the current Bug Validator attempt")
    completed = scheduler.transition(target, job_id, "complete", receipt, None, "execution")
    if completed.get("status") == "retryable":
        scheduler.transition(target, job_id, "retry", None, None, "execution")
        return next_action(target, job_id)
    response = next_action(target, job_id)
    if completed.get("status") == "succeeded" and all_phase_jobs_succeeded(target, completed["phase"]):
        response["summary"] = write_summary(target, mode)
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Advance one host-coordinated FM-Agent Bug Validator job without invoking FM-Agent.")
    parser.add_argument("action", choices=("next", "start", "retry", "run-dynamic", "submit-finalization"))
    parser.add_argument("--project", required=True); parser.add_argument("--job-id", required=True); parser.add_argument("--receipt-json")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "next": result = next_action(target, args.job_id)
        elif args.action == "start": result = start(target, args.job_id)
        elif args.action == "retry": result = retry(target, args.job_id)
        elif args.action == "run-dynamic": result = run_dynamic(target, args.job_id)
        else:
            if not args.receipt_json: raise ValueError("submit-finalization requires --receipt-json")
            result = submit_finalization(target, args.job_id, json.loads(args.receipt_json))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); raise SystemExit(2)


if __name__ == "__main__": main()
