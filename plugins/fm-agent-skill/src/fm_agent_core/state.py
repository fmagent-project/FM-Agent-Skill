"""Small, deterministic helpers for FM-Agent plugin state and artifacts.

These helpers deliberately do not invoke ``main.py`` or an LLM.  They make the
agent-led workflow resumable and make a baseline reusable only when its
artifacts still describe the selected source scope.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid


SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".cu", ".go", ".h", ".hpp", ".java", ".js", ".jsx", ".py", ".rs", ".ts", ".tsx", ".ets", ".erl"}
PHASES = {
    "full": ["preflight", "project_understanding", "phase_cleanup", "extraction", "call_graph", "specification", "verification", "bug_validation", "finalize"],
    "incremental": ["validate_baseline", "refresh_plan", "preserve_specs", "diff", "rebuild_graph", "select_scope", "update_specs", "verify_affected", "bug_validation", "finalize"],
}
RESUMABLE_STATUSES = {"running", "failed", "interrupted"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(project: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(["git", "-C", str(project), *args], text=True, capture_output=True, check=False)
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed.stdout.strip()


def plugin_dir(project: Path) -> Path:
    return project / "fm_agent_plugin"


def control_dir(project: Path) -> Path:
    """Return plugin-owned state, separate from FM-Agent's workspace."""
    return plugin_dir(project) / "control"


def fm_dir(project: Path) -> Path:
    return project / "fm_agent"


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_hash(path: Path) -> str | None:
    """Hash a file, or a directory's sorted JSON-relative-path/content hashes."""
    if path.is_file():
        return file_hash(path)
    if not path.is_dir():
        return None
    entries = []
    for item in sorted(path.rglob("*.json")):
        if item.is_file():
            entries.append({"path": item.relative_to(path).as_posix(), "sha256": file_hash(item)})
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def resolve(project: Path, value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value)
    return str((project / path).resolve() if not path.is_absolute() else path.resolve())


def fingerprint(project: Path, one_phase: bool, submodules: list[str], extra_edge: str | None, knowledge: list[str], config: dict | None = None) -> tuple[str, dict]:
    edge = resolve(project, extra_edge)
    # A resume grace period controls lock recovery, not the meaning or scope of
    # an analysis.  It must not invalidate baselines or interrupted runs.
    fingerprint_config = dict(config or {})
    fingerprint_config.pop("resume_grace_seconds", None)
    inputs = {
        "one_phase": bool(one_phase),
        "submodules": sorted(dict.fromkeys(submodules)),
        "extra_edge": {"path": edge, "sha256": content_hash(Path(edge)) if edge else None},
        "knowledge": [{"path": resolve(project, item), "sha256": file_hash(Path(resolve(project, item)))} for item in knowledge],
        "config": fingerprint_config,
    }
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), inputs


def source_files(project: Path) -> list[Path]:
    ignored = {".git", ".venv", "node_modules", "fm_agent", "fm_agent_plugin", ".codegraph", "build", "dist", "out", "target", "CMakeFiles"}
    found = []
    for root, directories, files in os.walk(project):
        directories[:] = [name for name in directories if name not in ignored]
        found.extend(Path(root) / name for name in files if Path(name).suffix.lower() in SOURCE_EXTENSIONS)
    return found


def source_snapshot(project: Path, submodules: list[str] | None = None) -> dict[str, str]:
    """Return the complete, scoped source-content snapshot for baseline reuse."""
    scopes = submodules or []
    snapshot = {}
    for path in source_files(project):
        rel = path.relative_to(project).as_posix()
        if scopes and not any(rel == item.rstrip("/") or rel.startswith(item.rstrip("/") + "/") for item in scopes):
            continue
        digest = file_hash(path)
        if digest: snapshot[rel] = digest
    return dict(sorted(snapshot.items()))


def snapshot_changed(project: Path, saved: dict, submodules: list[str] | None = None) -> bool:
    snapshot = saved.get("source_snapshot") if isinstance(saved, dict) else None
    return not isinstance(snapshot, dict) or snapshot != source_snapshot(project, submodules)


def refresh_observed_commit(project: Path, saved: dict) -> dict:
    """Advance Git provenance after a no-op without changing the analyzed snapshot."""
    current = git(project, "rev-parse", "HEAD")
    if saved.get("observed_commit") != current:
        saved["observed_commit"] = current; saved["observed_at"] = now()
        atomic_json(plugin_dir(project) / "baseline.json", saved)
    return saved


def run_path(project: Path, run_id: str) -> Path:
    """Return a run-record path without permitting path traversal."""
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        raise ValueError("invalid run id")
    return plugin_dir(project) / "runs" / f"{run_id}.json"


def run_record(project: Path, run_id: str) -> dict:
    try:
        value = read_json(run_path(project, run_id), {})
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def resumable_runs(project: Path) -> list[dict]:
    """Return incomplete full/incremental runs, newest first."""
    root = plugin_dir(project) / "runs"
    records = []
    if not root.is_dir():
        return records
    for path in root.glob("run-*.json"):
        record = read_json(path, {})
        if (
            isinstance(record, dict)
            and record.get("id") == path.stem
            and record.get("mode") in PHASES
            and record.get("status") in RESUMABLE_STATUSES
        ):
            records.append(record)
    return sorted(records, key=lambda item: str(item.get("updated_at") or item.get("ended_at") or item.get("started_at") or ""), reverse=True)


def first_incomplete_phase(record: dict) -> str | None:
    phases = record.get("phases")
    statuses = record.get("phase_status")
    if not isinstance(phases, list) or not isinstance(statuses, dict):
        return None
    for phase in phases:
        if statuses.get(phase, {}).get("status") != "succeeded":
            return phase
    # A run can fail after every gate passed but before pipeline completion.
    return "finalize" if "finalize" in phases else None


def inspect_resume(project: Path, run_id: str | None = None) -> dict:
    """Check a prior run can continue without changing its analysis inputs.

    Resume deliberately uses the run's saved configuration rather than current
    defaults.  It is valid only when the selected source and auxiliary inputs
    still have the exact content from the interrupted run.
    """
    record = run_record(project, run_id) if run_id else next(iter(resumable_runs(project)), {})
    if not record:
        return {"ok": False, "reason": "no interrupted FM-Agent full or incremental run was found"}
    if record.get("status") not in RESUMABLE_STATUSES or record.get("mode") not in PHASES:
        return {"ok": False, "reason": "selected run is not resumable", "run_id": record.get("id")}
    inputs = record.get("inputs")
    config = inputs.get("config") if isinstance(inputs, dict) else None
    snapshot = record.get("source_snapshot")
    if not isinstance(config, dict) or not isinstance(snapshot, dict):
        return {"ok": False, "reason": "run predates resumable state snapshots", "run_id": record.get("id")}
    submodules = inputs.get("submodules", [])
    if not isinstance(submodules, list):
        return {"ok": False, "reason": "run has invalid saved scope", "run_id": record.get("id")}
    try:
        fingerprint, _ = fingerprint_for_config(project, config)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": f"cannot validate saved analysis inputs: {exc}", "run_id": record.get("id")}
    if fingerprint != record.get("fingerprint"):
        return {"ok": False, "reason": "knowledge, supplemental edges, or saved analysis configuration changed", "run_id": record.get("id")}
    if source_snapshot(project, submodules) != snapshot:
        return {"ok": False, "reason": "supported source content changed since the interrupted run", "run_id": record.get("id")}
    phase = first_incomplete_phase(record)
    if not phase:
        return {"ok": False, "reason": "run has no resumable phase", "run_id": record.get("id")}
    return {"ok": True, "run": record, "run_id": record["id"], "mode": record["mode"], "resume_from_phase": phase, "config": config}


def fingerprint_for_config(project: Path, config: dict) -> tuple[str, dict]:
    """Rebuild a fingerprint from a run's immutable effective configuration."""
    return fingerprint(
        project,
        bool(config.get("one_phase", False)),
        list(config.get("submodules", [])),
        config.get("extra_edge"),
        list(config.get("knowledge", [])),
        config,
    )


def preflight(project: Path) -> dict:
    issues = []
    try:
        git(project, "rev-parse", "--verify", "HEAD")
    except RuntimeError:
        issues.append("target must be a Git repository with a resolvable HEAD")
    files = source_files(project)
    if not files:
        issues.append("no supported source files found")
    return {"ok": not issues, "project": str(project), "source_file_count": len(files), "issues": issues}


def source_index(project: Path) -> dict | None:
    data = read_json(control_dir(project) / "analysis_index.json", None)
    return data if isinstance(data, dict) and isinstance(data.get("functions"), list) else None


def _in_scope(item: dict, submodules: list[str]) -> bool:
    if not submodules:
        return True
    path = str(item.get("path", "")).replace("\\", "/").lstrip("./")
    return any(path == scope.rstrip("/") or path.startswith(scope.rstrip("/") + "/") for scope in submodules)


def scoped_functions(project: Path, submodules: list[str]) -> list[dict]:
    index = source_index(project)
    if not index:
        return []
    return [item for item in index["functions"] if isinstance(item, dict) and isinstance(item.get("id"), str) and _in_scope(item, submodules)]


def stripped_source_hash(path: Path) -> str | None:
    """Hash source after the leading generated SPEC/INFO headers, if present."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = content.splitlines(keepends=True)
    markers = [i for i, line in enumerate(lines) if "[INFO]" in line]
    if len(markers) >= 2:
        content = "".join(lines[markers[1] + 1:]).lstrip("\r\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def specification_artifacts_ready(project: Path, functions: list[dict], submodules: list[str] | None = None) -> tuple[bool, str]:
    """Validate current extracted copies and contracts without requiring verification.

    This deliberately stops before checking logic-verification results: an
    incremental run must be able to finish updating specifications before its
    subsequent verification phase produces new, hash-aligned results.
    """
    extracted = fm_dir(project) / "extracted_functions"
    expected_artifacts = set()
    for item in functions:
        function_id = item.get("id")
        rel = item.get("artifact") or item.get("extracted_path")
        source_hash = item.get("source_hash")
        if not isinstance(function_id, str) or not isinstance(rel, str) or not isinstance(source_hash, str):
            return False, "source_index contains a function without id, artifact, or source_hash"
        artifact = extracted / rel
        normalized_rel = Path(rel).as_posix()
        expected_artifacts.add(normalized_rel)
        if not artifact.is_file():
            return False, f"missing extracted artifact for {function_id}"
        text = artifact.read_text(encoding="utf-8", errors="replace")
        if text.count("[SPEC]") < 2 or text.count("[INFO]") < 2:
            return False, f"incomplete specification for {function_id}"
        if stripped_source_hash(artifact) != source_hash:
            return False, f"source hash mismatch for {function_id}"

    def in_selected_scope(rel: str) -> bool:
        return not submodules or any(rel == item.rstrip("/") or rel.startswith(item.rstrip("/") + "/") for item in submodules)

    actual_artifacts = {path.relative_to(extracted).as_posix() for path in extracted.rglob("*") if path.is_file() and in_selected_scope(path.relative_to(extracted).as_posix())} if extracted.is_dir() else set()
    stale_artifacts = actual_artifacts - expected_artifacts
    if stale_artifacts:
        return False, f"stale extracted artifact: {sorted(stale_artifacts)[0]}"
    return True, ""


def function_artifacts_ready(project: Path, functions: list[dict], submodules: list[str] | None = None) -> tuple[bool, str]:
    extracted = fm_dir(project) / "extracted_functions"
    results = fm_dir(project) / "logic_verification_results"
    expected_artifacts = set()
    expected_results = set()
    for item in functions:
        function_id = item.get("id")
        rel = item.get("artifact") or item.get("extracted_path")
        source_hash = item.get("source_hash")
        if not isinstance(function_id, str) or not isinstance(rel, str) or not isinstance(source_hash, str):
            return False, "source_index contains a function without id, artifact, or source_hash"
        artifact = extracted / rel
        expected_artifacts.add(Path(rel).as_posix())
        expected_results.add(Path(rel).with_suffix(".json").as_posix())
        if not artifact.is_file():
            return False, f"missing extracted artifact for {function_id}"
        text = artifact.read_text(encoding="utf-8", errors="replace")
        if text.count("[SPEC]") < 2 or text.count("[INFO]") < 2:
            return False, f"incomplete specification for {function_id}"
        if stripped_source_hash(artifact) != source_hash:
            return False, f"source hash mismatch for {function_id}"
        result_path = results / (str(Path(rel).with_suffix(".json")))
        result = read_json(result_path, None)
        if not isinstance(result, dict) or result.get("verdict") not in {"MATCH", "MISMATCH", "DEPENDENCY_RISK", "ERROR"}:
            return False, f"missing or invalid verification result for {function_id}"
        if result.get("function_id") != function_id:
            return False, f"verification function identity mismatch for {function_id}"
        result_hash = result.get("source_hash")
        if result_hash != source_hash:
            return False, f"verification hash mismatch for {function_id}"
    def in_selected_scope(rel: str) -> bool:
        return not submodules or any(rel == item.rstrip("/") or rel.startswith(item.rstrip("/") + "/") for item in submodules)
    actual_artifacts = {path.relative_to(extracted).as_posix() for path in extracted.rglob("*") if path.is_file() and in_selected_scope(path.relative_to(extracted).as_posix())} if extracted.is_dir() else set()
    actual_results = {path.relative_to(results).as_posix() for path in results.rglob("*.json") if in_selected_scope(path.relative_to(results).with_suffix("").as_posix())} if results.is_dir() else set()
    stale_artifacts = actual_artifacts - expected_artifacts
    stale_results = actual_results - expected_results
    if stale_artifacts:
        return False, f"stale extracted artifact: {sorted(stale_artifacts)[0]}"
    if stale_results:
        return False, f"stale verification result: {sorted(stale_results)[0]}"
    return True, ""


def _phase_number(phase, index: int) -> int:
    value = phase.get("phase", index) if isinstance(phase, dict) else index
    return value if isinstance(value, int) and value > 0 else index


def _phase_sources(phase) -> list[str]:
    if not isinstance(phase, dict): return []
    sources = phase.get("sources")
    if isinstance(sources, list): return sorted(str(item) for item in sources)
    result = []
    for module in phase.get("modules", []):
        if isinstance(module, dict): result.extend(str(item) for item in module.get("source_files", []))
    return sorted(set(result))


def phase_layers_ready(project: Path) -> tuple[bool, str]:
    """Check that native layer artifacts preserve every declared phase boundary."""
    phases_data = read_json(fm_dir(project) / "phases.json", {})
    phases = phases_data.get("phases") if isinstance(phases_data, dict) else None
    root = fm_dir(project) / "spec_prompts"
    if not isinstance(phases, list) or not phases or not root.is_dir():
        return False, "missing phases or top-down layer artifacts"
    expected, seen_functions = [], set()
    for index, phase in enumerate(phases, start=1):
        number = _phase_number(phase, index)
        path = root / f"phase_{number:02d}_topdown_layers.json"
        expected.append(path.name)
        data = read_json(path, None)
        if not isinstance(data, dict) or data.get("phase") != number:
            return False, f"missing or invalid layer artifact for phase {number}"
        sources = _phase_sources(phase)
        if sorted(data.get("source_files", [])) != sources:
            return False, f"layer sources do not match phase {number}"
        layers = data.get("layers")
        if not isinstance(layers, list): return False, f"invalid layers for phase {number}"
        for layer in layers:
            if not isinstance(layer, dict) or not isinstance(layer.get("functions"), list):
                return False, f"invalid layer entry for phase {number}"
            for function in layer["functions"]:
                if not isinstance(function, dict): return False, f"invalid function entry for phase {number}"
                function_id, source_file = function.get("function_id"), function.get("source_file")
                if not isinstance(function_id, str) or not isinstance(source_file, str):
                    return False, f"missing function identity or source in phase {number}"
                if source_file not in sources or function_id in seen_functions:
                    return False, f"cross-phase or duplicate function in phase {number}"
                seen_functions.add(function_id)
    actual = sorted(path.name for path in root.glob("phase_*_topdown_layers.json"))
    if actual != sorted(expected): return False, "unexpected phase layer artifact set"
    return True, ""


def specs_ready(project: Path, submodules: list[str] | None = None) -> tuple[bool, str, int]:
    functions = scoped_functions(project, submodules or [])
    if not functions:
        return False, "missing or invalid plugin control analysis_index.json", 0
    ready, reason = function_artifacts_ready(project, functions, submodules)
    return ready, reason, len(functions)


def inspect_baseline(project: Path, config_fingerprint: str, submodules: list[str] | None = None) -> dict:
    phases = read_json(fm_dir(project) / "phases.json", None)
    if not isinstance(phases, dict) or not isinstance(phases.get("phases"), list):
        return {"valid": False, "reason": "missing or invalid fm_agent/phases.json"}
    saved = read_json(plugin_dir(project) / "baseline.json", {})
    if not isinstance(saved, dict) or saved.get("schema_version") != 3 or saved.get("fingerprint") != config_fingerprint:
        return {"valid": False, "reason": "analysis range or configuration is incompatible"}
    commit = saved.get("analysis_commit")
    if not isinstance(commit, str):
        return {"valid": False, "reason": "missing successful baseline commit"}
    try:
        git(project, "cat-file", "-e", f"{commit}^{{commit}}")
    except RuntimeError:
        return {"valid": False, "reason": f"baseline commit is unavailable: {commit}"}
    functions = scoped_functions(project, submodules or [])
    if not functions:
        return {"valid": False, "reason": "no indexed functions in the selected scope"}
    ready, reason = function_artifacts_ready(project, functions)
    if not ready:
        return {"valid": False, "reason": reason}
    layers_ready, layers_reason = phase_layers_ready(project)
    if not layers_ready:
        return {"valid": False, "reason": layers_reason}
    # ``active.json`` describes the most recent attempt.  A failed later
    # attempt must not invalidate the successful run named by the baseline.
    # The baseline's own run is the evidence whose completion matters here.
    run_id = saved.get("run_id")
    if not isinstance(run_id, str) or Path(run_id).name != run_id:
        return {"valid": False, "reason": "missing successful baseline run"}
    run = read_json(plugin_dir(project) / "runs" / f"{run_id}.json", {})
    if run.get("status") != "succeeded" or run.get("mode") not in {"full", "incremental"}:
        return {"valid": False, "reason": "baseline run did not complete"}
    if not isinstance(saved.get("source_snapshot"), dict):
        return {"valid": False, "reason": "missing source snapshot"}
    return {"valid": True, "commit": commit, "function_count": len(functions), "snapshot_changed": snapshot_changed(project, saved, submodules), "saved": saved}


def untracked_sources(project: Path) -> list[str]:
    return [item for item in git(project, "ls-files", "--others", "--exclude-standard").splitlines() if is_supported_source_path(item)]


def is_supported_source_path(value: str) -> bool:
    path = value.replace("\\", "/")
    return Path(path).suffix.lower() in SOURCE_EXTENSIONS and not path.startswith(("fm_agent/", "fm_agent_plugin/"))


def changed_since(project: Path, commit: str) -> bool:
    tracked = git(project, "diff", "--name-only", commit, "--", check=False).splitlines()
    return any(is_supported_source_path(path) for path in tracked) or bool(untracked_sources(project))


def build_intent(project: Path, base_commit: str, note: str, run_id: str | None = None) -> Path:
    git(project, "cat-file", "-e", f"{base_commit}^{{commit}}")
    run_id = run_id or f"run-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    path = plugin_dir(project) / "intents" / f"{run_id}.md"
    commits = git(project, "log", "--format=%h %s", f"{base_commit}..HEAD", check=False).splitlines()
    files = git(project, "diff", "--name-status", base_commit, "--", check=False).splitlines()
    stat = git(project, "diff", "--stat", base_commit, "--", check=False)
    lines = ["# FM-Agent automatic incremental intent", "", "## Developer note", note.strip() or "(none)", "", "## Commit summary"]
    lines += [f"- {item}" for item in commits] or ["- No committed changes."]
    lines += ["", "## Changed files"] + ([f"- {item}" for item in files] or ["- No tracked changes."])
    if untracked_sources(project):
        lines += ["", "## Untracked source files"] + [f"- A\t{item}" for item in untracked_sources(project)]
    lines += ["", "## Restricted diff summary", "```text", stat or "No statistic available.", "```", "", "## Scope instruction", "Identify affected invariants, callers, and callees. Do not assume unrelated invariants are unchanged."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
