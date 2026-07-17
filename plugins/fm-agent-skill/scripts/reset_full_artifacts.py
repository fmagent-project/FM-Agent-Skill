#!/usr/bin/env python3
"""Remove only regenerable full-run outputs, never project source or phases."""
from __future__ import annotations

import argparse
import json
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
    plugin = [control / name for name in ("analysis_index.json", "call_graph_precision.json", "preserved_specs.json", "diff.json", "incremental_decision.json")]
    for path in native + plugin: remove(path)
    return {"ok": True, "preserved": str(fm / "phases.json")}


def main():
    parser = argparse.ArgumentParser(description="Clear derived FM-Agent artifacts before a full rerun.")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    print(json.dumps(reset(project(args)), ensure_ascii=False))


if __name__ == "__main__": main()
