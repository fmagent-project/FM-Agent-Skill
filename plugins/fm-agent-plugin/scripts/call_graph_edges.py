#!/usr/bin/env python3
"""Strict, recursive supplemental call-edge loader used by the plugin."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath


def clean(value):
    if not isinstance(value, str): raise ValueError("must be a string")
    return value.strip().rstrip(";").strip().strip("\"'")

def normalize(label):
    label = clean(label)
    if "::" not in label: return label
    path, func = label.rsplit("::", 1)
    name = PurePosixPath(path).name
    if "/" not in path or "." not in name: return label
    stem, extension = name.rsplit(".", 1)
    return "::".join([*PurePosixPath(path).parent.parts, f"{stem}-{extension}", func])

def strings(value, field, source):
    if not isinstance(value, list): raise ValueError(f"{source}: {field} must be a string array")
    out = []
    for item in value:
        text = clean(item)
        if text and text not in out: out.append(text)
    return out

def parse(item, source):
    if not isinstance(item, dict): raise ValueError(f"{source}: expected edge object")
    caller, callee = item.get("caller"), item.get("callee")
    if not isinstance(caller, dict) or not isinstance(callee, dict): raise ValueError(f"{source}: caller and callee must be objects")
    caller_fqn = normalize(caller.get("fqn", "")) if caller.get("fqn", "") is not None else ""
    names = strings(caller.get("callsite_names", []), "caller.callsite_names", source)
    if not caller_fqn and not names: raise ValueError(f"{source}: caller.fqn or caller.callsite_names is required")
    callee_fqn = normalize(callee.get("fqn"))
    if not callee_fqn: raise ValueError(f"{source}: callee.fqn is required")
    info = strings(callee.get("info_names", []), "callee.info_names", source)
    evidence = item.get("evidence", item.get("source", source))
    if isinstance(evidence, list): evidence = [clean(value) for value in evidence if isinstance(value, str) and clean(value)]
    elif isinstance(evidence, str): evidence = [evidence.strip()] if evidence.strip() else []
    else: raise ValueError(f"{source}: evidence must be a string or string array")
    return {"caller": {"fqn": caller_fqn, "callsite_names": names}, "callee": {"fqn": callee_fqn, "info_names": info}, "evidence": evidence, "source": source}

def load(path):
    path = Path(path); files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    merged = {}
    for file in files:
        try: data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise ValueError(f"{file}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("edges"), list): raise ValueError(f"{file}: expected object with an edges list")
        for index, item in enumerate(data["edges"], 1):
            edge = parse(item, f"{file}:edges[{index}]"); key = (edge["caller"]["fqn"], tuple(edge["caller"]["callsite_names"]), edge["callee"]["fqn"])
            prior = merged.setdefault(key, edge)
            for name in edge["callee"]["info_names"]:
                if name not in prior["callee"]["info_names"]: prior["callee"]["info_names"].append(name)
            for evidence in edge["evidence"]:
                if evidence not in prior["evidence"]: prior["evidence"].append(evidence)
    return {"ok": True, "files": [str(item) for item in files], "edges": list(merged.values()), "edge_count": len(merged)}

def main():
    parser = argparse.ArgumentParser(description="Validate and normalize extra caller-to-callee edges."); parser.add_argument("path"); args = parser.parse_args()
    try: result = load(args.path); code = 0
    except ValueError as exc: result = {"ok": False, "issues": [str(exc)]}; code = 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(code)

if __name__ == "__main__": main()
