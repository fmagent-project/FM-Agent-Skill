#!/usr/bin/env python3
"""Build a CMake project in a plugin-owned directory for one bug probe."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from _common import project, state


def safe_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return result or "probe"


def run(command: list[str], cwd: Path) -> dict:
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    except FileNotFoundError as exc:
        return {
            "command": command,
            "returncode": 127,
            "stdout": "",
            "stderr": f"required build command is unavailable: {exc}",
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure and build a CMake probe without reusing the project's build cache."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--bug-id", required=True)
    parser.add_argument("--target", help="optional CMake build target")
    args = parser.parse_args()
    target = project(args)
    if not (target / "CMakeLists.txt").is_file():
        parser.error("isolated CMake probes require CMakeLists.txt in --project")

    build_dir = state.plugin_dir(target) / "probes" / safe_component(args.run_id) / safe_component(args.bug_id) / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.parent.mkdir(parents=True, exist_ok=True)

    configure = run(["cmake", "-S", str(target), "-B", str(build_dir)], target)
    result = {"project": str(target), "build_dir": str(build_dir), "configure": configure}
    if configure["returncode"] == 0:
        command = ["cmake", "--build", str(build_dir)]
        if args.target:
            command += ["--target", args.target]
        result["build"] = run(command, target)
    result["ok"] = configure["returncode"] == 0 and result.get("build", {}).get("returncode") == 0
    state.atomic_json(build_dir.parent / "build_result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
