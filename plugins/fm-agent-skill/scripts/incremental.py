#!/usr/bin/env python3
import argparse, json
from _common import project, state

parser = argparse.ArgumentParser(description="Create auditable incremental inclusion decisions for the agent.")
parser.add_argument("action", choices=("init", "include", "exclude", "show"))
parser.add_argument("--project", required=True); parser.add_argument("--run-id", required=True)
parser.add_argument("--function"); parser.add_argument("--reason")
args = parser.parse_args(); path = state.control_dir(project(args)) / "incremental_decision.json"
data = state.read_json(path, {"run_id": args.run_id, "included": {}, "excluded": {}})
if args.action == "init": data = {"run_id": args.run_id, "included": {}, "excluded": {}}
elif args.action in ("include", "exclude"):
    if not args.function or not args.reason: parser.error("include/exclude require --function and --reason")
    data["included" if args.action == "include" else "excluded"][args.function] = args.reason
state.atomic_json(path, data)
print(json.dumps(data, ensure_ascii=False, indent=2))
