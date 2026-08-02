#!/usr/bin/env python3
"""Preflight, Git-snapshot mode selection, and active-analysis preparation."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _common import project, state
from config import DEFAULTS, load
from isolation import clear_failure, create as create_snapshot, discard as discard_snapshot, marker as snapshot_marker
from locking import acquire, reclaim_for_resume, release
import checkpoint
import versioning


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
    candidate = dict(base if isinstance(base, dict) else load(target))
    config = {key: candidate.get(key, value) for key, value in DEFAULTS.items()}
    if args.submodules: config["submodules"] = args.submodules
    if args.knowledge: config["knowledge"] = args.knowledge
    if args.extra_edge is not None: config["extra_edge"] = args.extra_edge
    if args.codegraph: config["call_graph_backend"] = "codegraph"
    if args.one_phase: config["one_phase"] = True
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
    current = state.git(target, "rev-parse", "HEAD")
    mode = "noop" if baseline["valid"] and baseline["commit"] == current else "incremental" if baseline["valid"] else "full"
    return {"ok": True, "mode": mode, "baseline": baseline, "config": config, "requires_codegraph": mode != "noop"}


def resume_overrides(args):
    return bool(args.note.strip() or args.submodules or args.knowledge or args.extra_edge is not None or args.one_phase or args.codegraph)


def call_pipeline(target, action, mode=None, config=None):
    command = [sys.executable, str(Path(__file__).with_name("pipeline.py")), action, "--project", str(target)]
    if mode: command += ["--mode", mode]
    if config is not None:
        command += ["--config-json", json.dumps(config)]
        for item in config["submodules"]: command += ["--submodule", item]
        if config["one_phase"]: command.append("--one-phase")
        if config.get("extra_edge"): command += ["--extra-edge", config["extra_edge"]]
        for item in config["knowledge"]: command += ["--knowledge", item]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode: raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description="Prepare one FM-Agent analysis in a private Git snapshot worktree.")
    parser.add_argument("action", choices=("inspect", "dispatch", "resume-inspect", "resume")); parser.add_argument("--project", required=True); parser.add_argument("--note", default=""); parser.add_argument("--submodule", dest="submodules", action="append", default=[]); parser.add_argument("--one-phase", action="store_true"); parser.add_argument("--extra-edge"); parser.add_argument("--knowledge", action="append", default=[]); parser.add_argument("--codegraph", action="store_true"); parser.add_argument("--force-stale-lock", action="store_true"); parser.add_argument("--take-over", action="store_true")
    args = parser.parse_args(); source_target = project(args)
    if args.action == "inspect":
        result = inspect(source_target, args); print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)
    pending = snapshot_marker(source_target)
    # A normal run is also a continuation request when the immutable snapshot,
    # saved fingerprint/config, lease state, and checkpoint all still match.
    if args.action == "dispatch" and not resume_overrides(args):
        active_ref = state.git(source_target, "rev-parse", "--verify", "refs/fm-agent-skill/active", check=False)
        checkpoint_head = checkpoint.root(source_target) / "HEAD"
        if active_ref and checkpoint_head.is_file():
            automatic = checkpoint.inspect_auto_resume(source_target)
            if automatic.get("ok"):
                target = Path(automatic["snapshot"]).resolve()
                try:
                    versioning.require_compatible(source_target)
                    checked = state.inspect_resume(target)
                    if not checked.get("ok"): raise RuntimeError(checked.get("reason", "checkpoint is not resumable"))
                    lock = reclaim_for_resume(target, args.take_over)
                    record = call_pipeline(target, "resume")
                except Exception as exc:
                    print(json.dumps({"ok": False, "reason": str(exc), "auto_resume": True}, ensure_ascii=False, indent=2)); raise SystemExit(2)
                print(json.dumps({
                    "ok": True, "mode": "resume", "auto_resume": True,
                    "project": str(target), "source_project": str(source_target),
                    "resume_from_phase": record["current_phase"], "config": checked["config"],
                    "lock": lock, "analysis": record,
                }, ensure_ascii=False, indent=2)); return
            print(json.dumps(automatic, ensure_ascii=False, indent=2)); raise SystemExit(2)
    if args.action in {"resume-inspect", "resume"} and not (isinstance(pending.get("snapshot"), str) and Path(pending["snapshot"]).is_dir()):
        rebuilt = checkpoint.inspect_auto_resume(source_target)
        if rebuilt.get("ok"): pending = snapshot_marker(source_target)
    target = Path(pending["snapshot"]).resolve() if args.action in {"resume-inspect", "resume"} and isinstance(pending.get("snapshot"), str) and Path(pending["snapshot"]).is_dir() else source_target
    if args.action == "dispatch" and isinstance(pending.get("snapshot"), str) and Path(pending["snapshot"]).is_dir():
        print(json.dumps({"ok": False, "reason": "an FM-Agent snapshot worktree is pending; use --resume or clean it first"}, ensure_ascii=False, indent=2)); raise SystemExit(2)
    if args.action == "resume-inspect":
        result = {"ok": False, "preflight": state.preflight(source_target)} if not state.preflight(source_target)["ok"] else ({"ok": False, "reason": "resume cannot override current analysis settings"} if resume_overrides(args) else state.inspect_resume(target))
        print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(0 if result["ok"] else 2)
    if args.action == "resume":
        checked = {"ok": False, "reason": "resume cannot override current analysis settings"} if resume_overrides(args) else state.inspect_resume(target)
        if not checked["ok"]: print(json.dumps(checked, ensure_ascii=False, indent=2)); raise SystemExit(2)
        try: versioning.require_compatible(source_target); lock = reclaim_for_resume(target, args.take_over); record = call_pipeline(target, "resume")
        except Exception as exc:
            try: release(target, "failed")
            except RuntimeError: pass
            print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2)); raise SystemExit(2)
        print(json.dumps({"ok": True, "mode": "resume", "project": str(target), "resume_from_phase": record["current_phase"], "config": checked["config"], "lock": lock, "analysis": record}, ensure_ascii=False, indent=2)); return
    preview = inspect(source_target, args)
    if not preview["ok"]: print(json.dumps(preview, ensure_ascii=False, indent=2)); raise SystemExit(2)
    if preview["mode"] == "noop":
        clear_failure(source_target)
        record = {"schema_version": 2, "mode": "noop", "status": "noop", "started_at": state.now(), "ended_at": state.now(), "fingerprint": preview["baseline"]["saved"]["fingerprint"], "inputs": preview["baseline"]["saved"]["inputs"], "baseline_commit": preview["baseline"]["commit"]}
        state.atomic_json(state.skill_dir(source_target) / "active.json", record)
        print(json.dumps({"ok": True, "mode": "noop", "project": str(source_target), "baseline": preview["baseline"], "config": preview["config"], "analysis": record}, ensure_ascii=False, indent=2)); return
    try:
        snapshot = create_snapshot(source_target); target = Path(snapshot["snapshot"]).resolve()
        selected = inspect(target, args)
        if not selected["ok"]: raise RuntimeError(str(selected))
        lock = acquire(target, args.force_stale_lock)
        record = call_pipeline(target, "prepare", selected["mode"], selected["config"])
        if selected["mode"] == "incremental":
            record["intent_path"] = str(state.build_intent(target, selected["baseline"]["commit"], args.note)); state.atomic_json(state.skill_dir(target) / "active.json", record)
        print(json.dumps({"ok": True, "mode": selected["mode"], "project": str(target), "source_project": str(source_target), "snapshot": snapshot, "baseline": selected["baseline"], "config": selected["config"], "lock": lock, "analysis": record}, ensure_ascii=False, indent=2))
    except Exception as exc:
        if 'target' in locals() and target != source_target:
            try: discard_snapshot(target)
            except RuntimeError: pass
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False, indent=2)); raise SystemExit(2)


if __name__ == "__main__": main()
