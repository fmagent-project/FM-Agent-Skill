#!/usr/bin/env python3
"""The sole deterministic dispatch entry for the public run skill."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

from _common import project, state
from config import load
from locking import acquire, release


def valid_settings(target, config):
    issues = []
    for item in config["submodules"]:
        path = (target / item).resolve()
        if not path.is_dir() or target not in path.parents and path != target: issues.append(f"invalid --submodule: {item}")
    for item in config["knowledge"]:
        path = Path(item)
        if not path.is_absolute(): path = target / path
        if not path.is_file() or path.suffix.lower() not in {".md", ".markdown"}: issues.append(f"knowledge must be readable Markdown: {item}")
    if config.get("extra_edge"):
        command = [sys.executable, str(Path(__file__).with_name("call_graph_edges.py")), config["extra_edge"]]
        if subprocess.run(command, text=True, capture_output=True).returncode: issues.append("extra-edge validation failed")
    return issues


def build_config(args, target, base=None):
    config = dict(base if isinstance(base, dict) else load(target))
    if args.submodules: config["submodules"] = args.submodules
    if args.knowledge: config["knowledge"] = args.knowledge
    if args.extra_edge is not None: config["extra_edge"] = args.extra_edge
    if args.codegraph: config["call_graph_backend"] = "codegraph"
    if args.one_phase: config["one_phase"] = True
    if args.isolate: config["isolate"] = True
    return config


def saved_config(target):
    value = state.read_json(state.plugin_dir(target) / "config.json", {})
    return value if isinstance(value, dict) else {}


def inspection_config(args, target, saved):
    """Reuse the last successful backend for a read-only no-op decision.

    Public run options and explicit repository config still override it. This
    prevents an unchanged CodeGraph baseline from looking incompatible merely
    because the caller has not yet authorized CodeGraph for this invocation.
    """
    baseline = state.read_json(state.plugin_dir(target) / "baseline.json", {})
    prior = baseline.get("inputs", {}).get("config") if isinstance(baseline, dict) else None
    base = prior if isinstance(prior, dict) else load(target)
    base = dict(base); base.update(saved)
    return build_config(args, target, base)


def inspect(target, args):
    preflight = state.preflight(target)
    if not preflight["ok"]: return {"ok": False, "preflight": preflight}
    config = inspection_config(args, target, saved_config(target))
    issues = valid_settings(target, config)
    if issues: return {"ok": False, "issues": issues}
    fingerprint, _ = state.fingerprint(target, config["one_phase"], config["submodules"], config.get("extra_edge"), config["knowledge"], config)
    baseline = state.inspect_baseline(target, fingerprint, config["submodules"])
    if baseline["valid"] and not baseline["snapshot_changed"]:
        current_commit = state.git(target, "rev-parse", "HEAD")
        return {"ok": True, "mode": "noop", "baseline": baseline, "config": config, "requires_codegraph": False, "refresh_observed_commit": baseline["saved"].get("observed_commit") != current_commit}
    return {"ok": True, "mode": "incremental" if baseline["valid"] else "full", "baseline": baseline, "config": config, "requires_codegraph": True}


def pipeline_prepare(target, mode, run_id, config):
    command = [sys.executable, str(Path(__file__).with_name("pipeline.py")), "prepare", "--project", str(target), "--mode", mode, "--run-id", run_id, "--config-json", json.dumps(config)]
    for item in config["submodules"]: command += ["--submodule", item]
    if config["one_phase"]: command.append("--one-phase")
    if config["isolate"]: command.append("--isolate")
    if config.get("extra_edge"): command += ["--extra-edge", config["extra_edge"]]
    for item in config["knowledge"]: command += ["--knowledge", item]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode: raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description="Preflight, lock, baseline selection, and plan creation for FM-Agent.")
    parser.add_argument("action", choices=("inspect", "dispatch")); parser.add_argument("--project", required=True); parser.add_argument("--note", default=""); parser.add_argument("--submodule", dest="submodules", action="append", default=[]); parser.add_argument("--one-phase", action="store_true"); parser.add_argument("--extra-edge"); parser.add_argument("--knowledge", action="append", default=[]); parser.add_argument("--isolate", action="store_true"); parser.add_argument("--codegraph", action="store_true"); parser.add_argument("--force-stale-lock", action="store_true")
    args = parser.parse_args(); target = project(args); preflight = state.preflight(target)
    if args.action == "inspect":
        result = inspect(target, args); print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)
    if not preflight["ok"]: print(json.dumps({"ok": False, "preflight": preflight}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    config = build_config(args, target); issues = valid_settings(target, config)
    if issues: print(json.dumps({"ok": False, "issues": issues}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    fingerprint, inputs = state.fingerprint(target, config["one_phase"], config["submodules"], config.get("extra_edge"), config["knowledge"], config)
    baseline = state.inspect_baseline(target, fingerprint, config["submodules"]); run_id = f"run-{uuid.uuid4().hex[:12]}"
    try: lock = acquire(target, run_id, args.force_stale_lock)
    except RuntimeError as exc: print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    try:
        if baseline["valid"] and not baseline["snapshot_changed"]:
            state.refresh_observed_commit(target, baseline["saved"])
            record = {"id": run_id, "mode": "noop", "status": "noop", "started_at": state.now(), "ended_at": state.now(), "fingerprint": fingerprint, "inputs": inputs, "baseline_commit": baseline["commit"]}
            state.atomic_json(state.plugin_dir(target) / "runs" / f"{run_id}.json", record); state.atomic_json(state.plugin_dir(target) / "active.json", record); release(target, run_id, "idle")
            result = {"ok": True, "mode": "noop", "run_id": run_id, "baseline": baseline, "config": config, "lock_released": True}
        else:
            mode = "incremental" if baseline["valid"] else "full"; record = pipeline_prepare(target, mode, run_id, config)
            if mode == "incremental": record["intent_path"] = str(state.build_intent(target, baseline["commit"], args.note, run_id)); state.atomic_json(state.plugin_dir(target) / "runs" / f"{run_id}.json", record); state.atomic_json(state.plugin_dir(target) / "active.json", record)
            result = {"ok": True, "mode": mode, "run_id": run_id, "baseline": baseline, "config": config, "lock": lock, "plan": record}
    except Exception:
        release(target, run_id, "failed"); raise
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
