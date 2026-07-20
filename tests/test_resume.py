"""Small integration test for the deterministic interrupted-run state machine."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "fm-agent-skill" / "scripts"


def run(*args: str, cwd: Path | None = None, expect: int = 0) -> dict:
    completed = subprocess.run([sys.executable, *args], cwd=cwd, text=True, capture_output=True)
    if completed.returncode != expect:
        raise AssertionError(f"expected {expect}, got {completed.returncode}: {completed.stdout}\n{completed.stderr}")
    return json.loads(completed.stdout) if completed.stdout.strip() else {}


def git(project: Path, *args: str) -> None:
    completed = subprocess.run(["git", "-C", str(project), *args], text=True, capture_output=True)
    if completed.returncode:
        raise AssertionError(completed.stderr)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fm-agent-resume-") as temp:
        project = Path(temp)
        (project / "demo.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        git(project, "init")
        git(project, "config", "user.email", "resume@example.test")
        git(project, "config", "user.name", "Resume Test")
        git(project, "add", "demo.py")
        git(project, "commit", "-m", "initial")

        config = {"submodules": [], "one_phase": False, "isolate": False, "concurrency": 10, "granularity": 40,
                  "retries": 5, "lock_ttl_seconds": 7200, "resume_grace_seconds": 600, "codegraph_path": None,
                  "call_graph_backend": "agent-static", "extra_edge": None, "knowledge": []}
        prepared = run(str(SCRIPTS / "pipeline.py"), "prepare", "--project", str(project), "--mode", "full", "--config-json", json.dumps(config))
        run_id = prepared["id"]
        run(str(SCRIPTS / "locking.py"), "acquire", "--project", str(project), "--run-id", run_id)
        run(str(SCRIPTS / "pipeline.py"), "phase-start", "--project", str(project), "--run-id", run_id, "--phase", "preflight")
        run(str(SCRIPTS / "pipeline.py"), "phase-complete", "--project", str(project), "--run-id", run_id, "--phase", "preflight")
        run(str(SCRIPTS / "pipeline.py"), "fail", "--project", str(project), "--run-id", run_id, "--message", "simulated interruption")

        inspected = run(str(SCRIPTS / "orchestrate.py"), "resume-inspect", "--project", str(project))
        assert inspected["run_id"] == run_id
        assert inspected["resume_from_phase"] == "project_understanding"
        run(str(SCRIPTS / "locking.py"), "acquire", "--project", str(project), "--run-id", run_id)
        fresh = subprocess.run([sys.executable, str(SCRIPTS / "orchestrate.py"), "resume", "--project", str(project)], text=True, capture_output=True)
        assert fresh.returncode == 2
        assert "fresh heartbeat" in fresh.stdout
        resumed = run(str(SCRIPTS / "orchestrate.py"), "resume", "--project", str(project), "--take-over")
        assert resumed["run_id"] == run_id
        assert resumed["resume_from_phase"] == "project_understanding"
        assert resumed["plan"]["resume"]["count"] == 1
        run(str(SCRIPTS / "pipeline.py"), "fail", "--project", str(project), "--run-id", run_id, "--message", "test cleanup")

        (project / "demo.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        rejected = subprocess.run([sys.executable, str(SCRIPTS / "orchestrate.py"), "resume-inspect", "--project", str(project)], text=True, capture_output=True)
        assert rejected.returncode == 2
        assert "source content changed" in rejected.stdout


if __name__ == "__main__":
    main()
