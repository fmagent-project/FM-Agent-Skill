#!/usr/bin/env python3
"""Preflight, mode selection, and single-current-analysis preparation."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _common import project, state
from config import load
from locking import acquire, reclaim_for_resume, release
from isolation import create as create_isolation, marker as isolation_marker, sync as sync_isolation


def valid_settings(target, config):
    issues = []
    for item in config["submodules"]:
        path = (target / item).resolve()
        if not path.is_dir() or target not in path.parents and path != target: issues.append(f"invalid --submodule: {item}")
    for item in config["knowledge"]:
        path = Path(item); path = path if path.is_absolute() else target / path
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


def inspection_config(args, target):
    saved = state.read_json(state.skill_dir(target) / "config.json", {})
    baseline = state.read_json(state.skill_dir(target) / "baseline.json", {})
    prior = baseline.get("inputs", {}).get("config") if isinstance(baseline, dict) else None
    base = dict(prior if isinstance(prior, dict) else load(target)); base.update(saved if isinstance(saved, dict) else {})
    return build_config(args, target, base)


def inspect(target, args):
    preflight = state.preflight(target)
    if not preflight["ok"]: return {"ok": False, "preflight": preflight}
    config = inspection_config(args, target); issues = valid_settings(target, config)
    if issues: return {"ok": False, "issues": issues}
    fingerprint, _ = state.fingerprint(target, config["one_phase"], config["submodules"], config.get("extra_edge"), config["knowledge"], config)
    baseline = state.inspect_baseline(target, fingerprint, config["submodules"])
    if baseline["valid"] and not baseline["snapshot_changed"]:
        current = state.git(target, "rev-parse", "HEAD")
        return {"ok": True, "mode": "noop", "baseline": baseline, "config": config, "requires_codegraph": False, "refresh_observed_commit": baseline["saved"].get("observed_commit") != current}
    return {"ok": True, "mode": "incremental" if baseline["valid"] else "full", "baseline": baseline, "config": config, "requires_codegraph": True}


def resume_overrides(args): return bool(args.note.strip() or args.submodules or args.knowledge or args.extra_edge is not None or args.one_phase or args.isolate or args.codegraph)


def call_pipeline(target, action, mode=None, config=None):
    command = [sys.executable, str(Path(__file__).with_name("pipeline.py")), action, "--project", str(target)]
    if mode: command += ["--mode", mode]
    if config is not None:
        command += ["--config-json", json.dumps(config)]
        for item in config["submodules"]: command += ["--submodule", item]
        if config["one_phase"]: command.append("--one-phase")
        if config["isolate"]: command.append("--isolate")
        if config.get("extra_edge"): command += ["--extra-edge", config["extra_edge"]]
        for item in config["knowledge"]: command += ["--knowledge", item]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode: raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description="Preflight and prepare FM-Agent's single current analysis.")
    parser.add_argument("action", choices=("inspect", "dispatch", "resume-inspect", "resume")); parser.add_argument("--project", required=True); parser.add_argument("--note", default=""); parser.add_argument("--submodule", dest="submodules", action="append", default=[]); parser.add_argument("--one-phase", action="store_true"); parser.add_argument("--extra-edge"); parser.add_argument("--knowledge", action="append", default=[]); parser.add_argument("--isolate", action="store_true"); parser.add_argument("--codegraph", action="store_true"); parser.add_argument("--force-stale-lock", action="store_true"); parser.add_argument("--take-over", action="store_true")
    args = parser.parse_args(); target = project(args); source_target = target; preflight = state.preflight(target)
    if args.action == "inspect":
        result = inspect(target, args); print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)
    pending_isolation = isolation_marker(source_target)
    if args.action in {"resume-inspect", "resume"} and isinstance(pending_isolation.get("snapshot"), str) and Path(pending_isolation["snapshot"]).is_dir():
        target = Path(pending_isolation["snapshot"]).resolve()
    if args.action == "dispatch" and isinstance(pending_isolation.get("snapshot"), str) and Path(pending_isolation["snapshot"]).is_dir():
        print(json.dumps({"ok": False, "reason": "an isolated FM-Agent analysis is pending; use --resume or finish it first"}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    if args.action == "resume-inspect":
        result = {"ok": False, "preflight": preflight} if not preflight["ok"] else ( {"ok": False, "reason": "resume cannot override current analysis settings"} if resume_overrides(args) else state.inspect_resume(target) )
        print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)
    if not preflight["ok"]: print(json.dumps({"ok": False, "preflight": preflight}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    if args.action == "resume":
        checked = {"ok": False, "reason": "resume cannot override current analysis settings"} if resume_overrides(args) else state.inspect_resume(target)
        if not checked["ok"]: print(json.dumps(checked, ensure_ascii=False, indent=2)); raise SystemExit(2)
        try: lock = reclaim_for_resume(target, args.take_over); record = call_pipeline(target, "resume")
        except Exception as exc:
            try: release(target, "failed")
            except RuntimeError: pass
            print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2)); raise SystemExit(2)
        print(json.dumps({"ok": True, "mode": "resume", "project": str(target), "resume_from_phase": record["current_phase"], "config": checked["config"], "lock": lock, "analysis": record}, ensure_ascii=False, indent=2)); return
    selected = inspect(target, args)
    if not selected["ok"]: print(json.dumps(selected, ensure_ascii=False, indent=2)); raise SystemExit(2)
    isolated = None
    if selected["mode"] != "noop" and selected["config"].get("isolate"):
        try:
            isolated = create_isolation(source_target); target = Path(isolated["snapshot"]).resolve()
        except RuntimeError as exc:
            print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    try: lock = acquire(target, args.force_stale_lock)
    except RuntimeError as exc: print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    try:
        if selected["mode"] == "noop":
            state.refresh_observed_commit(target, selected["baseline"]["saved"])
            record = {"schema_version": 1, "mode": "noop", "status": "noop", "started_at": state.now(), "ended_at": state.now(), "fingerprint": selected["baseline"]["saved"]["fingerprint"], "inputs": selected["baseline"]["saved"]["inputs"], "baseline_commit": selected["baseline"]["commit"]}
            state.atomic_json(state.skill_dir(target) / "active.json", record); release(target, "idle")
        else:
            record = call_pipeline(target, "prepare", selected["mode"], selected["config"])
            if selected["mode"] == "incremental":
                record["intent_path"] = str(state.build_intent(target, selected["baseline"]["commit"], args.note)); state.atomic_json(state.skill_dir(target) / "active.json", record)
        print(json.dumps({"ok": True, "mode": selected["mode"], "project": str(target), "isolated": isolated, "baseline": selected["baseline"], "config": selected["config"], "lock": lock, "analysis": record}, ensure_ascii=False, indent=2))
    except Exception:
        release(target, "failed")
        if isolated: sync_isolation(target)
        raise


if __name__ == "__main__": main()
