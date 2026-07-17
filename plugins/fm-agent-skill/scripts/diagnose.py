#!/usr/bin/env python3
import argparse, json
from _common import project, state

parser = argparse.ArgumentParser(description="Read existing FM-Agent diagnostics without starting analysis.")
parser.add_argument("--project", required=True); parser.add_argument("--bug-id")
args = parser.parse_args(); target = project(args)
summary = state.read_json(state.fm_dir(target) / "bug_validation" / "summary.json", {})
run = state.read_json(state.plugin_dir(target) / "active.json", {})
result = {"run": run, "summary": summary}
if args.bug_id:
    root = state.fm_dir(target) / "bug_validation"
    for candidate in (root / f"{args.bug_id}.md", root / f"{args.bug_id}.result.json"):
        if candidate.is_file(): result["report_path"] = str(candidate); result["report"] = candidate.read_text(errors="replace"); break
print(json.dumps(result, ensure_ascii=False, indent=2))
