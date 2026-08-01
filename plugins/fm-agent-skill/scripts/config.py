#!/usr/bin/env python3
"""Persist non-secret FM-Agent Skill defaults in the target repository."""
from __future__ import annotations

import argparse
import json

from _common import project, state
from fm_agent_core.languages import probe_adapter_choices

DEFAULTS = {
    "submodules": [], "one_phase": False,
    "scheduler_executor": "host-subagent",
    "max_active_subagents": 10,
    "spec_concurrency": 4,
    "verify_concurrency": 8,
    "bug_validation_concurrency": 2,
    "read_only_plan_concurrency": 2,
    "spec_batch_size": 1, "bug_validation_max_attempts": 5, "bug_validation_negative_retries": 2,
    "bug_validation_execution": "agent-executed",
    "probe_adapter": "auto",
    "granularity": 40, "retries": 5, "lock_ttl_seconds": 7200, "resume_grace_seconds": 600,
    "codegraph_path": None, "call_graph_backend": "agent-static", "extra_edge": None, "knowledge": [],
}


def load(target):
    saved = state.read_json(state.skill_dir(target) / "config.json", {})
    result = dict(DEFAULTS)
    if isinstance(saved, dict):
        result.update({key: value for key, value in saved.items() if key in DEFAULTS})
    if result["probe_adapter"] not in probe_adapter_choices():
        raise ValueError(
            "saved probe_adapter is unsupported by the LanguageProfile registry: "
            f"{result['probe_adapter']}"
        )
    if result["bug_validation_execution"] not in {"agent-executed", "adapter"}:
        raise ValueError(
            "saved bug_validation_execution must be agent-executed or adapter"
        )
    return result


def save(target, config):
    state.atomic_json(state.skill_dir(target) / "config.json", config)


def main():
    parser = argparse.ArgumentParser(description="Read or persist FM-Agent Skill defaults; no secrets are copied.")
    parser.add_argument("action", choices=("show", "set", "reset"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--submodule", dest="submodules", action="append")
    parser.add_argument("--one-phase", choices=("true", "false"))
    parser.add_argument("--max-active-subagents", type=int)
    parser.add_argument("--spec-concurrency", type=int)
    parser.add_argument("--verify-concurrency", type=int)
    parser.add_argument("--bug-validation-concurrency", type=int)
    parser.add_argument("--read-only-plan-concurrency", type=int)
    parser.add_argument("--scheduler-executor", choices=("host-subagent",))
    parser.add_argument("--spec-batch-size", type=int)
    parser.add_argument("--bug-validation-max-attempts", type=int)
    parser.add_argument("--bug-validation-negative-retries", type=int)
    parser.add_argument("--bug-validation-execution", choices=("agent-executed", "adapter"))
    parser.add_argument("--probe-adapter", choices=probe_adapter_choices())
    parser.add_argument("--granularity", type=int)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--lock-ttl-seconds", type=int)
    parser.add_argument("--resume-grace-seconds", type=int)
    parser.add_argument("--codegraph-path")
    parser.add_argument("--call-graph-backend", choices=("agent-static", "codegraph"))
    parser.add_argument("--extra-edge")
    parser.add_argument("--knowledge", action="append")
    args = parser.parse_args(); target = project(args)
    if args.action == "reset":
        save(target, dict(DEFAULTS)); print(json.dumps(DEFAULTS, ensure_ascii=False, indent=2)); return
    try:
        config = load(target)
    except ValueError as exc:
        parser.error(str(exc))
    if args.action == "set":
        for key in ("max_active_subagents", "spec_concurrency", "verify_concurrency", "bug_validation_concurrency", "read_only_plan_concurrency", "scheduler_executor", "spec_batch_size", "bug_validation_max_attempts", "bug_validation_negative_retries", "bug_validation_execution", "probe_adapter", "granularity", "retries", "lock_ttl_seconds", "resume_grace_seconds", "codegraph_path", "call_graph_backend", "extra_edge"):
            value = getattr(args, key)
            if value is not None: config[key] = value
        if args.submodules is not None: config["submodules"] = args.submodules
        if args.knowledge is not None: config["knowledge"] = args.knowledge
        for key in ("one_phase",):
            value = getattr(args, key)
            if value is not None: config[key] = value == "true"
        for key in ("max_active_subagents", "spec_concurrency", "verify_concurrency", "bug_validation_concurrency", "read_only_plan_concurrency", "spec_batch_size", "bug_validation_max_attempts", "granularity", "retries", "lock_ttl_seconds", "resume_grace_seconds"):
            if config[key] < 1: parser.error(f"{key.replace('_', '-')} must be positive")
        if config["bug_validation_negative_retries"] < 0: parser.error("bug-validation-negative-retries must be non-negative")
        save(target, config)
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
