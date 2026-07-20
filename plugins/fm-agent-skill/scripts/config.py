#!/usr/bin/env python3
"""Persist non-secret FM-Agent plugin defaults in the target repository."""
from __future__ import annotations

import argparse
import json

from _common import project, state

DEFAULTS = {
    "submodules": [], "one_phase": False, "isolate": False, "concurrency": 10,
    "granularity": 40, "retries": 5, "lock_ttl_seconds": 7200, "resume_grace_seconds": 600,
    "codegraph_path": None, "call_graph_backend": "agent-static", "extra_edge": None, "knowledge": [],
}


def load(target):
    saved = state.read_json(state.plugin_dir(target) / "config.json", {})
    result = dict(DEFAULTS)
    if isinstance(saved, dict):
        result.update({key: value for key, value in saved.items() if key in DEFAULTS})
    return result


def save(target, config):
    state.atomic_json(state.plugin_dir(target) / "config.json", config)


def main():
    parser = argparse.ArgumentParser(description="Read or persist FM-Agent plugin defaults; no secrets are copied.")
    parser.add_argument("action", choices=("show", "set", "reset"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--submodule", dest="submodules", action="append")
    parser.add_argument("--one-phase", choices=("true", "false"))
    parser.add_argument("--isolate", choices=("true", "false"))
    parser.add_argument("--concurrency", type=int)
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
    config = load(target)
    if args.action == "set":
        for key in ("concurrency", "granularity", "retries", "lock_ttl_seconds", "resume_grace_seconds", "codegraph_path", "call_graph_backend", "extra_edge"):
            value = getattr(args, key)
            if value is not None: config[key] = value
        if args.submodules is not None: config["submodules"] = args.submodules
        if args.knowledge is not None: config["knowledge"] = args.knowledge
        for key in ("one_phase", "isolate"):
            value = getattr(args, key)
            if value is not None: config[key] = value == "true"
        for key in ("concurrency", "granularity", "retries", "lock_ttl_seconds", "resume_grace_seconds"):
            if config[key] < 1: parser.error(f"{key.replace('_', '-')} must be positive")
        save(target, config)
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
