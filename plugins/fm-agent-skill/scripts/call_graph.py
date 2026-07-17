#!/usr/bin/env python3
"""Record call-graph precision or summarize top-down layer artifacts."""
import argparse, json
from _common import project, state

parser = argparse.ArgumentParser(description="Record FM-Agent call-graph precision or summarize layer artifacts.")
parser.add_argument("action", choices=("show", "record-precision"), nargs="?", default="show")
parser.add_argument("--project", required=True)
parser.add_argument("--backend", choices=("codegraph", "agent-static"))
parser.add_argument("--precision", choices=("exact", "best-effort"))
parser.add_argument("--reason", default="")
parser.add_argument("--codegraph-index")
args = parser.parse_args(); target = project(args); root = state.fm_dir(target) / "spec_prompts"
if args.action == "record-precision":
    if not args.backend or not args.precision: parser.error("record-precision requires --backend and --precision")
    result = {"backend": args.backend, "precision": args.precision, "reason": args.reason, "generated_at": state.now()}
    if args.codegraph_index: result["codegraph_index"] = args.codegraph_index
    state.atomic_json(state.control_dir(target) / "call_graph_precision.json", result)
else:
    layers = []
    for path in sorted(root.glob("phase_*_topdown_layers.json")) if root.is_dir() else []:
        data = state.read_json(path, {}); layers.append({"file": path.name, "total_layers": data.get("total_layers"), "layers": len(data.get("layers", []))})
    result = {"phases": layers, "precision": state.read_json(state.control_dir(target) / "call_graph_precision.json", {})}
print(json.dumps(result, ensure_ascii=False, indent=2))
