#!/usr/bin/env python3
"""Deterministic Bug Validator state machine for a Codex/Claude Coordinator.

This script never starts an LLM, imports FM-Agent, or executes an Agent-provided
command.  It exposes exactly the next host-worker request and owns local
admission, evidence identity checks, retries, and the terminal summary.  In
the default ``agent-executed`` mode, the Coordinator invokes the Worker for
preparation, project-scoped execution, and finalization; ``adapter`` retains
the controlled local-runner path.
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


EXECUTION_MODES = {"agent-executed", "adapter"}


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


def execution_mode(target: Path) -> str:
    config = state.read_json(state.skill_dir(target) / "config.json", {})
    value = config.get("bug_validation_execution", "agent-executed") if isinstance(config, dict) else "agent-executed"
    if value not in EXECUTION_MODES:
        raise ValueError(f"unsupported bug_validation_execution mode: {value}")
    return value


def worker_request(target: Path, job: dict, pass_name: str) -> dict:
    bug_id, mode = context(job)
    attempt = int(job.get("attempts", 0))
    if attempt < 1:
        raise ValueError("running Bug Validator job has no attempt")
    root = attempt_dir(target, bug_id, attempt).relative_to(target).as_posix()
    return {
        "action": "host_worker",
        "worker": "fm-agent-skill:fm-bug-validate-worker",
        "pass": pass_name,
        "job_id": job["id"],
        "mode": mode,
        "bug_id": bug_id,
        "attempt": attempt,
        "attempt_dir": root,
        "required_outputs": {
            "preparation": [f"{root}/reproduction.json", f"{root}/probe.<language-extension>"],
            "execution": [f"{root}/reproduction_result.json"],
            "finalization": [job["bug_result_path"]],
        }[pass_name],
        "rule": (
            "Invoke this only through the active Codex/Claude host worker mechanism; do not run FM-Agent."
            if pass_name != "execution" else
            "Invoke this through the active host worker mechanism. The Worker may run project-scoped reproduction commands and must persist their exact evidence."
        ),
    }


def agent_execution_result_valid(target: Path, job: dict) -> bool:
    """Validate identity and outcome before a fast-mode Worker may finalize."""
    bug_id, _ = context(job); attempt = int(job["attempts"])
    _, contract = read_contract(target, bug_id, attempt, "agent-executed")
    result = state.read_json(attempt_dir(target, bug_id, attempt) / "reproduction_result.json", None)
    if not isinstance(result, dict):
        return False
    if any(result.get(key) != value for key, value in {
        "schema_version": 1, "execution_mode": "agent-executed", "bug_id": bug_id,
        "attempt": attempt, "snapshot_commit": state.current_snapshot_commit(target),
        "language": contract["language"], "public_entrypoint": contract["public_entrypoint"],
    }.items()):
        return False
    if result.get("state") not in {"completed", "execution_error", "unsupported"}:
        return False
    classification = result.get("classification")
    if classification not in {"confirmed", "not_reproduced", "inconclusive", "runtime_error"}:
        return False
    if result["state"] == "completed" and classification not in {"confirmed", "not_reproduced", "inconclusive"}:
        return False
    if result["state"] == "execution_error" and classification != "runtime_error":
        return False
    if result["state"] == "unsupported" and classification != "inconclusive":
        return False
    if contract["public_entrypoint"]["ecosystem"] == "unavailable" and (
        result["state"] != "unsupported" or classification != "inconclusive"
    ):
        return False
    return agent_execution_commands_valid(target, attempt_dir(target, bug_id, attempt), result) and isinstance(result.get("reason"), str)


def agent_execution_commands_valid(target: Path, root: Path, result: dict) -> bool:
    """Require recorded Agent commands to use only their attempt-local cwd.

    The Worker can read the immutable project snapshot, but any command that
    could build, package, or cache must operate from a copied workspace under
    its own attempt. This makes simultaneous Bug Validator jobs independent
    even in the broad ``agent-executed`` compatibility mode.
    """
    commands = result.get("commands")
    if not isinstance(commands, list):
        return False
    if result.get("state") in {"completed", "execution_error"} and not commands:
        return False
    root = root.resolve()
    for item in commands:
        if not isinstance(item, dict) or set(item) != {"command", "cwd", "returncode", "stdout", "stderr"}:
            return False
        if not isinstance(item.get("command"), str) or not item["command"].strip() or len(item["command"]) > 4096:
            return False
        cwd = item.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            return False
        candidate = Path(cwd)
        if candidate.is_absolute() or ".." in candidate.parts:
            return False
        resolved = (target / candidate).resolve()
        if not (resolved == root or root in resolved.parents):
            return False
        if not isinstance(item.get("returncode"), int):
            return False
        if not all(isinstance(item.get(field), str) and len(item[field]) <= 8000 for field in ("stdout", "stderr")):
            return False
    return True


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
    # Validate the preparation contract against the selected execution mechanism.
    mode = execution_mode(target)
    read_contract(target, bug_id, int(job["attempts"]), mode)
    if not (root / "reproduction_result.json").is_file():
        if mode == "agent-executed":
            return worker_request(target, job, "execution")
        return {"action": "run_dynamic", "job_id": job_id, "bug_id": bug_id, "attempt": int(job["attempts"])}
    if execution_mode(target) == "agent-executed":
        if not agent_execution_result_valid(target, job):
            raise ValueError("agent-executed reproduction result is missing required identity or outcome evidence")
        return {"action": "submit_agent_execution", "job_id": job_id}
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
    if execution_mode(target) == "agent-executed":
        raise ValueError("agent-executed Bug Validator evidence must be produced by the execution Worker pass")
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


def submit_agent_execution(target: Path, job_id: str) -> dict:
    """Accept immutable Worker evidence and preserve normal retry semantics."""
    job = load_job(target, job_id)
    if job["status"] != "running" or execution_mode(target) != "agent-executed":
        raise ValueError("submit-agent-execution requires a running agent-executed Bug Validator job")
    if not agent_execution_result_valid(target, job):
        raise ValueError("agent-executed reproduction result is missing required identity or outcome evidence")
    bug_id, _ = context(job); attempt = int(job["attempts"])
    result = state.read_json(attempt_dir(target, bug_id, attempt) / "reproduction_result.json", {})
    if result["state"] == "execution_error":
        failed = scheduler.transition(target, job_id, "fail", None, result["reason"], "execution")
        if failed.get("status") == "retryable":
            scheduler.transition(target, job_id, "retry", None, None, "execution")
            return next_action(target, job_id)
        return {"action": "terminal", "job_id": job_id, "status": failed.get("status")}
    if not finalization_matches_current_attempt(target, job):
        return worker_request(target, job, "finalization")
    return {"action": "submit_finalization_receipt", "job_id": job_id, "result_path": job["bug_result_path"]}


def all_phase_jobs_succeeded(target: Path, phase: str) -> bool:
    root = state.skill_dir(target) / "jobs"
    jobs = [state.read_json(path, {}) for path in root.glob("*.json")] if root.is_dir() else []
    relevant = [job for job in jobs if isinstance(job, dict) and job.get("phase") == phase and job.get("type") == "bug_validate"]
    return bool(relevant) and all(job.get("status") == "succeeded" for job in relevant)


def submit_finalization(target: Path, job_id: str, receipt: dict) -> dict:
    job = load_job(target, job_id); _, mode = context(job)
    if not finalization_matches_current_attempt(target, job):
        # Do not leave a leased job running when a Worker wrote a static
        # report without the required immutable dynamic evidence.  Treat this
        # as an execution/protocol failure and requeue through the normal
        # bounded retry path; a report path alone is never a completion.
        failed = scheduler.transition(
            target, job_id, "fail", None,
            "no dynamic reproduction available",
            "execution",
        )
        if failed.get("status") == "retryable":
            scheduler.transition(target, job_id, "retry", None, None, "execution")
            return next_action(target, job_id)
        return {"action": "terminal", "job_id": job_id, "status": failed.get("status"), "reason": failed.get("message")}
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
    parser.add_argument("action", choices=("next", "start", "retry", "run-dynamic", "submit-agent-execution", "submit-finalization"))
    parser.add_argument("--project", required=True); parser.add_argument("--job-id", required=True); parser.add_argument("--receipt-json")
    args = parser.parse_args(); target = project(args)
    try:
        if args.action == "next": result = next_action(target, args.job_id)
        elif args.action == "start": result = start(target, args.job_id)
        elif args.action == "retry": result = retry(target, args.job_id)
        elif args.action == "run-dynamic": result = run_dynamic(target, args.job_id)
        elif args.action == "submit-agent-execution": result = submit_agent_execution(target, args.job_id)
        else:
            if not args.receipt_json: raise ValueError("submit-finalization requires --receipt-json")
            result = submit_finalization(target, args.job_id, json.loads(args.receipt_json))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); raise SystemExit(2)


if __name__ == "__main__": main()
