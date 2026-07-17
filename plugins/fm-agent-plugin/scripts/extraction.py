#!/usr/bin/env python3
import argparse, json
from _common import project, state

parser = argparse.ArgumentParser(description="Inspect FM-Agent extracted-function artifact readiness.")
parser.add_argument("--project", required=True)
args = parser.parse_args(); ready, reason, count = state.specs_ready(project(args))
print(json.dumps({"ready": ready, "reason": reason, "function_count": count}, ensure_ascii=False, indent=2))
