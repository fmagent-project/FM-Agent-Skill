#!/usr/bin/env python3
import argparse, json
from _common import project, state

parser = argparse.ArgumentParser(description="Validate a target before agent-led FM-Agent analysis.")
parser.add_argument("--project", required=True)
args = parser.parse_args()
result = state.preflight(project(args))
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["ok"] else 2)
