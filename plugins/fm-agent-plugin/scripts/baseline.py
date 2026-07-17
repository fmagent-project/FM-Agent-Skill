#!/usr/bin/env python3
import argparse, json
from _common import common_scope, project, scope, state
from config import load

parser = argparse.ArgumentParser(description="Inspect whether a full FM-Agent baseline is reusable.")
common_scope(parser)
args = parser.parse_args()
target = project(args); config = load(target)
args.one_phase = config["one_phase"] if args.one_phase is None else args.one_phase
if not args.submodules: args.submodules = config["submodules"]
if not args.extra_edge: args.extra_edge = config.get("extra_edge")
if not args.knowledge: args.knowledge = config["knowledge"]
fingerprint, inputs = scope(args, config)
result = state.inspect_baseline(target, fingerprint, args.submodules)
result.update({"fingerprint": fingerprint, "inputs": inputs})
print(json.dumps(result, ensure_ascii=False, indent=2))
