#!/usr/bin/env python3
import argparse, json
from _common import project, state
import checkpoint
import versioning

parser = argparse.ArgumentParser(description="Validate a target before agent-led FM-Agent analysis.")
parser.add_argument("--project", required=True)
args = parser.parse_args()
target = project(args)
result = state.preflight(target)
active_ref = state.git(target, "rev-parse", "--verify", "refs/fm-agent-skill/active", check=False)
if result["ok"] and active_ref and (checkpoint.root(target) / "HEAD").is_file():
    compatibility = versioning.checkpoint_compatibility(checkpoint.source_project(target))
    result["checkpoint_version"] = compatibility
    if not compatibility.get("compatible"):
        result["ok"] = False
        result["issues"].append("active checkpoint is incompatible with the installed runtime")
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["ok"] else 2)
