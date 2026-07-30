#!/usr/bin/env python3
"""Remove only regenerable full-run outputs, never project source or phases."""
from __future__ import annotations

import argparse
import shutil

from _common import project, state


def remove(path):
    if path.is_dir(): shutil.rmtree(path)
    elif path.exists(): path.unlink()


def reset(target):
    fm = state.fm_dir(target); control = state.control_dir(target)
    # Preserve phases.json: project_understanding/phase_cleanup owns it for this run.
    native = [fm / name for name in ("extracted_functions", "spec_prompts", "logic_verification_results", "bug_validation", "trace")]
    native += [fm / name for name in ("fm_agent_file_list.json", "version.log", "incremental_updated_specs.json")]
    # Latest-run incremental records must not survive a clean full analysis.
    for pattern in (
        "select_relevant_modules.md", "relevant_modules.json",
        "select_relevant_files_*.md", "relevant_files_*.json",
        "spec_update_*.md", "spec_update_*.json",
        "incremental_*.log", "workflow_*.md",
    ):
        native.extend(fm.glob(pattern))
    skill = [control / name for name in ("analysis_index.json", "call_graph_precision.json", "graph_edges.json", "agent_static_edges.json", "preserved_specs.json", "diff.json", "incremental_decision.json", "phase_receipts")]
    for path in native + skill + [state.skill_dir(target) / "jobs", state.skill_dir(target) / "worker_reports", state.skill_dir(target) / "probes", state.skill_dir(target) / "runs"]: remove(path)
    return {"ok": True, "preserved": str(fm / "phases.json")}


def reset_incremental_artifacts(target):
    """Discard transient incremental outputs while retaining hash-checked results.

    `executor.py diff` removes verification results for changed/removed functions.
    Unchanged results are required to keep the next baseline complete.
    """
    fm = state.fm_dir(target)
    paths = [fm / name for name in ("bug_validation", "incremental_updated_specs.json", "trace")]
    for pattern in (
        "select_relevant_modules.md", "relevant_modules.json",
        "select_relevant_files_*.md", "relevant_files_*.json",
        "spec_update_*.md", "spec_update_*.json", "incremental_*.log",
    ):
        paths.extend(fm.glob(pattern))
    paths += [state.control_dir(target) / "graph_edges.json", state.control_dir(target) / "agent_static_edges.json", state.control_dir(target) / "phase_receipts", state.skill_dir(target) / "jobs", state.skill_dir(target) / "worker_reports", state.skill_dir(target) / "probes", state.skill_dir(target) / "runs"]
    for path in paths:
        remove(path)
    return {"ok": True, "preserved": str(fm / "extracted_functions")}


def clear_transient(target):
    """Discard current scheduler/probe work after a terminal successful analysis."""
    for path in (state.skill_dir(target) / "jobs", state.skill_dir(target) / "worker_reports", state.skill_dir(target) / "probes"):
        remove(path)
    return {"ok": True}


def main():
    parser = argparse.ArgumentParser(description="Clear derived FM-Agent artifacts before a full rerun.")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    print(json.dumps(reset(project(args)), ensure_ascii=False))


if __name__ == "__main__": main()
