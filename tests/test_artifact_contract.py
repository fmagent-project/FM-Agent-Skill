"""Focused checks for the native FM-Agent artifact layout."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "fm-agent-skill" / "src"))
sys.path.insert(0, str(ROOT / "plugins" / "fm-agent-skill" / "scripts"))

from fm_agent_core import state  # noqa: E402
from reset_full_artifacts import reset_incremental_artifacts  # noqa: E402


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fm-agent-artifacts-") as temp:
        project = Path(temp)
        artifact = project / "fm_agent" / "extracted_functions" / "src" / "demo-py" / "value.py"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("def value():\n    return 1\n", encoding="utf-8")
        write_json(Path(f"{artifact}.spec.json"), {
            "signature": "value() -> int", "pre_condition": "Always", "post_condition": "Returns 1",
        })
        write_json(Path(f"{artifact}.info.json"), {"callees": []})

        phases = {"project": "demo", "phases": [{"phase": 1, "name": "source", "modules": [{"name": "demo", "source_files": ["src/demo.py"]}], "depends_on_phases": []}]}
        write_json(project / "fm_agent" / "phases.json", phases)
        prompts = project / "fm_agent" / "spec_prompts"
        (prompts / "domain_context").mkdir(parents=True)
        (prompts / "system_prompt.md").write_text("sidecar schema", encoding="utf-8")
        (prompts / "domain_context" / "engine_overview.txt").write_text("demo", encoding="utf-8")
        (prompts / "domain_context" / "phase_01_types.txt").write_text("int", encoding="utf-8")

        rel = artifact.relative_to(project / "fm_agent" / "extracted_functions").as_posix()
        function_id, source_hash = "src::demo-py::value", state.stripped_source_hash(artifact)
        state.atomic_json(state.control_dir(project) / "analysis_index.json", {"functions": [{"id": function_id, "artifact": rel, "path": rel, "source_hash": source_hash}]})

        assert state.specification_context_ready(project)[0]
        assert state.specification_artifacts_ready(project, state.scoped_functions(project, []))[0]

        result = project / "fm_agent" / "logic_verification_results" / "src" / "demo-py" / "value.json"
        write_json(result, {"function": rel, "function_id": function_id, "source_hash": source_hash, "verdict": "MATCH", "gaps": None})
        assert state.function_artifacts_ready(project, state.scoped_functions(project, []))[0]

        # Sidecars must never be indexed or reported as stale function files.
        assert state.is_metadata_sidecar(Path(f"{artifact}.spec.json"))

        (project / "fm_agent" / "relevant_files_0.json").write_text("[]", encoding="utf-8")
        reset_incremental_artifacts(project)
        assert artifact.is_file() and Path(f"{artifact}.spec.json").is_file()
        assert not result.exists()
        assert not (project / "fm_agent" / "relevant_files_0.json").exists()

if __name__ == "__main__":
    main()
