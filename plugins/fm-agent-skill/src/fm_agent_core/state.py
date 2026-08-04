"""Small, deterministic helpers for FM-Agent Skill state and artifacts.

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
import re
import subprocess

from .languages import source_extensions


# Language recognition is centralised in languages.py.  External-plugin
# profiles (currently Erlang/ELP) are intentionally not silently accepted by
# the core executor: they require their dedicated capability to be installed.
SOURCE_EXTENSIONS = source_extensions()
METADATA_SIDECAR_SUFFIXES = (".spec.json", ".info.json")
VERDICTS = {"MATCH", "MISMATCH", "DEPENDENCY_RISK", "INCONCLUSIVE", "ERROR"}
VERIFICATION_FIELDS = {
    "schema_version", "function_id", "snapshot_commit", "verdict",
    "reasoning", "gaps", "error",
}
POSTCONDITION_REASONING_FIELDS = {
    "actual_postcondition", "spec_postcondition", "counterexample",
    "offending_statements", "reason",
}
BUG_ATTEMPT_CLASSIFICATIONS = {"confirmed", "not_reproduced", "inconclusive"}
BUG_FINAL_STATUSES = {"confirmed", "rejected", "inconclusive"}
SPEC_FIELD_ORDER = ("signature", "pre_condition", "post_condition")
SPEC_FIELDS = set(SPEC_FIELD_ORDER)
CALLEE_FIELD_ORDER = ("name", "signature", "pre_condition", "post_condition")
CALLEE_FIELDS = set(CALLEE_FIELD_ORDER)
SPEC_CONTRACT_BASES = {"normative", "inferred", "unavailable"}
OBSERVATIONAL_CONTEXT_MARKER = "FM_AGENT_OBSERVATIONAL_CONTEXT_V1"
_ORACLE_MARKER = re.compile(
    r"(?:\bBUG\s*:|\bFIXME\s*:|\bTODO\s*:|\bseeded[ -]bug\b|"
    r"\bknown defect\b|\bintentionally (?:broken|defective)\b)",
    re.IGNORECASE,
)
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


def skill_dir(project: Path) -> Path:
    return project / "fm_agent_skill"


def control_dir(project: Path) -> Path:
    """Return Skill-owned state, separate from FM-Agent's workspace."""
    return skill_dir(project) / "control"


def fm_dir(project: Path) -> Path:
    return project / "fm_agent"


BASELINE_REF = "refs/fm-agent-skill/baseline"


def baseline_commit(project: Path) -> str | None:
    value = git(project, "rev-parse", "--verify", BASELINE_REF, check=False)
    return value if value else None


def current_snapshot_commit(project: Path) -> str:
    """Return the worktree commit; unit-level tools may use an unversioned sentinel."""
    return git(project, "rev-parse", "HEAD", check=False) or "unversioned"


def version_log(project: Path, commit: str) -> None:
    path = fm_dir(project) / "version.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(commit + "\n")


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
    # Scheduling changes alter resource use, not the meaning of an analysis.
    for key in ("resume_grace_seconds", "scheduler_executor", "max_active_subagents", "spec_concurrency", "verify_concurrency", "bug_validation_concurrency", "read_only_plan_concurrency", "worker_target_tokens", "worker_max_functions", "worker_max_source_bytes", "worker_lease_seconds", "spec_batch_size", "retries", "lock_ttl_seconds", "bug_validation_max_attempts", "bug_validation_negative_retries", "bug_validation_execution", "probe_adapter"):
        fingerprint_config.pop(key, None)
    inputs = {
        "one_phase": bool(one_phase),
        "submodules": sorted(dict.fromkeys(submodules)),
        "extra_edge": {"path": edge, "sha256": content_hash(Path(edge)) if edge else None},
        "knowledge": [{"path": resolve(project, item), "sha256": file_hash(Path(resolve(project, item)))} for item in knowledge],
        "config": fingerprint_config,
    }
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), inputs


def source_files(project: Path) -> list[Path]:
    ignored = {".git", ".venv", "node_modules", "oh_modules", "fm_agent", "fm_agent_skill", "fm_agent_plugin", ".codegraph", "build", "dist", "out", "target", "CMakeFiles"}
    found = []
    for root, directories, files in os.walk(project):
        directories[:] = [name for name in directories if name not in ignored]
        found.extend(Path(root) / name for name in files if Path(name).suffix.lower() in SOURCE_EXTENSIONS and not is_test_source_path((Path(root) / name).relative_to(project).as_posix()))
    return found


def is_test_source_path(value: str) -> bool:
    parts = value.replace("\\", "/").lower().split("/")
    name = parts[-1]
    return any(part in {"test", "tests", "testing", "fixtures"} for part in parts[:-1]) or name.startswith(("test_", "test-")) or "_test." in name or "_tests." in name


def changed_source_paths(project: Path, base_commit: str, current_commit: str | None = None) -> set[str]:
    """Return production source paths changed between two committed snapshots."""
    current = current_commit or git(project, "rev-parse", "HEAD")
    lines = git(project, "diff", "--name-only", base_commit, current, "--", check=False).splitlines()
    return {item.replace("\\", "/") for item in lines if is_supported_source_path(item)}


def snapshot_sources_clean(project: Path) -> bool:
    """Reject workers that modify business source inside the analysis worktree."""
    changed = git(project, "diff", "--name-only", "HEAD", "--", check=False).splitlines()
    if any(is_supported_source_path(path) for path in changed):
        return False
    return not any(is_supported_source_path(path) for path in git(project, "ls-files", "--others", "--exclude-standard").splitlines())


def active_record(project: Path) -> dict:
    """Return the sole current-analysis record, never a run history."""
    value = read_json(skill_dir(project) / "active.json", {})
    return value if isinstance(value, dict) else {}


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


def inspect_resume(project: Path) -> dict:
    """Check the current interrupted analysis can continue unchanged.

    Resume deliberately uses the run's saved configuration rather than current
    defaults.  It is valid only when the selected source and auxiliary inputs
    still have the exact content from the interrupted run.
    """
    record = active_record(project)
    if not record:
        return {"ok": False, "reason": "no interrupted FM-Agent analysis was found"}
    if record.get("status") not in RESUMABLE_STATUSES or record.get("mode") not in PHASES:
        return {"ok": False, "reason": "current analysis is not resumable"}
    inputs = record.get("inputs")
    config = inputs.get("config") if isinstance(inputs, dict) else None
    snapshot_commit = record.get("snapshot_commit")
    if not isinstance(config, dict) or not isinstance(snapshot_commit, str):
        return {"ok": False, "reason": "analysis predates Git snapshot resume state"}
    submodules = inputs.get("submodules", [])
    if not isinstance(submodules, list):
        return {"ok": False, "reason": "analysis has invalid saved scope"}
    try:
        fingerprint, _ = fingerprint_for_config(project, config)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": f"cannot validate saved analysis inputs: {exc}"}
    if fingerprint != record.get("fingerprint"):
        return {"ok": False, "reason": "knowledge, supplemental edges, or saved analysis configuration changed"}
    if current_snapshot_commit(project) != snapshot_commit:
        return {"ok": False, "reason": "active worktree no longer points at the saved snapshot commit"}
    phase = first_incomplete_phase(record)
    if not phase:
        return {"ok": False, "reason": "analysis has no resumable phase"}
    return {"ok": True, "analysis": record, "mode": record["mode"], "resume_from_phase": phase, "config": config}


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


def phases_schema_ready(project: Path) -> tuple[bool, str]:
    data = read_json(fm_dir(project) / "phases.json", {})
    phases = data.get("phases") if isinstance(data, dict) else None
    if not isinstance(phases, list) or not phases: return False, "missing phases"
    for position, phase in enumerate(phases, 1):
        if not isinstance(phase, dict) or phase.get("phase") != position or not isinstance(phase.get("modules"), list):
            return False, f"phase {position} is not normalized"
        for module in phase["modules"]:
            if not isinstance(module, dict) or not isinstance(module.get("source_files"), list) or not module["source_files"]:
                return False, f"phase {position} has invalid module sources"
            for source in module["source_files"]:
                if not isinstance(source, str) or not (project / source).is_file() or is_test_source_path(source): return False, f"phase {position} has invalid production source"
        if not isinstance(phase.get("depends_on_phases"), list) or not all(isinstance(value, int) and 0 < value < position for value in phase["depends_on_phases"]):
            return False, f"phase {position} has invalid dependencies"
    return True, ""


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


def is_metadata_sidecar(path: Path | str) -> bool:
    return str(path).replace("\\", "/").endswith(METADATA_SIDECAR_SUFFIXES)


def _spec_schema_ready(data) -> tuple[bool, str]:
    """Validate FM-Agent's native three-field specification schema."""
    if not isinstance(data, dict):
        return False, "spec sidecar must be a JSON object"
    actual = set(data)
    if actual != SPEC_FIELDS:
        missing = sorted(SPEC_FIELDS - actual)
        extra = sorted(actual - SPEC_FIELDS)
        details = []
        if missing:
            details.append("missing fields: " + ", ".join(missing))
        if extra:
            details.append("unsupported fields: " + ", ".join(extra))
        return False, "spec sidecar fields must match schema exactly (" + "; ".join(details) + ")"
    if not all(isinstance(data[field], str) and data[field].strip() for field in ("signature", "pre_condition", "post_condition")):
        return False, "signature, pre_condition, and post_condition must be non-empty strings"
    if _ORACLE_MARKER.search("\n".join((data["pre_condition"], data["post_condition"]))):
        return False, "specification contract contains benchmark/debug oracle markers"
    return True, ""


def _valid_spec(data) -> bool:
    return _spec_schema_ready(data)[0]


def canonical_spec(data) -> tuple[dict | None, list[str]]:
    """Convert a model response to FM-Agent's native specification boundary.

    Older Skill workers and permissive hosts sometimes add identity, evidence,
    phase, or error metadata.  Those fields do not belong to the FM-Agent
    contract consumed by the reasoner.  Strip them deterministically instead
    of spending another semantic Worker attempt on a mechanical rewrite.
    """
    if not isinstance(data, dict):
        return None, []
    candidate = {field: data.get(field) for field in SPEC_FIELD_ORDER}
    error_behavior = data.get("error_behavior")
    if (
        isinstance(error_behavior, str) and error_behavior.strip()
        and isinstance(candidate.get("post_condition"), str)
        and error_behavior.strip() not in candidate["post_condition"]
    ):
        candidate["post_condition"] = candidate["post_condition"].rstrip() + "\nError behavior: " + error_behavior.strip()
    if not _spec_schema_ready(candidate)[0]:
        return None, []
    return candidate, sorted(set(data) - SPEC_FIELDS)


def canonical_info(data) -> tuple[dict | None, list[str]]:
    """Convert callee metadata to FM-Agent's native closed schema."""
    if not isinstance(data, dict) or not isinstance(data.get("callees"), list):
        return None, []
    callees, removed = [], set(data) - {"callees"}
    for raw in data["callees"]:
        if not isinstance(raw, dict):
            return None, []
        item = dict(raw)
        if not isinstance(item.get("name"), str) and isinstance(item.get("id"), str):
            item["name"] = item["id"]
        candidate = {field: item.get(field) for field in CALLEE_FIELD_ORDER}
        if not all(isinstance(candidate[field], str) for field in CALLEE_FIELDS):
            return None, []
        removed.update(set(item) - CALLEE_FIELDS)
        callees.append(candidate)
    return {"callees": callees}, sorted(removed)


def _relative_file(project: Path, value: str) -> Path | None:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (project / candidate).resolve()
    if resolved != project.resolve() and project.resolve() not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


def _knowledge_manifest_ready(project: Path) -> tuple[bool, str, list[dict]]:
    root = fm_dir(project) / "spec_prompts" / "domain_context" / "user_knowledge"
    manifest = read_json(root / "manifest.json", None)
    record = active_record(project)
    inputs = record.get("inputs") if isinstance(record, dict) else None
    expected = inputs.get("knowledge") if isinstance(inputs, dict) else None
    entries = manifest.get("entries") if isinstance(manifest, dict) and set(manifest) == {"schema_version", "snapshot_commit", "entries"} and manifest.get("schema_version") == 1 else None
    if not isinstance(expected, list):
        return False, "active analysis has no immutable knowledge input list", []
    if not isinstance(entries, list) or manifest.get("snapshot_commit") != current_snapshot_commit(project) or len(entries) != len(expected):
        return False, "missing or stale immutable user-knowledge manifest", []
    for item, source in zip(entries, expected):
        if not isinstance(item, dict) or set(item) != {"original_path", "copied_path", "sha256"} or not isinstance(source, dict):
            return False, "invalid immutable user-knowledge manifest entry", []
        original = source.get("path")
        digest = source.get("sha256")
        if not isinstance(original, str) or not isinstance(digest, str):
            return False, "active analysis has an invalid knowledge input", []
        try:
            same_original = str(Path(item.get("original_path", "")).resolve()) == str(Path(original).resolve())
        except (OSError, RuntimeError):
            same_original = False
        copied = _relative_file(project, item.get("copied_path", ""))
        if not same_original or item.get("sha256") != digest or copied is None:
            return False, "user-knowledge manifest does not match active analysis inputs", []
        try:
            inside_root = copied.parent == root or root in copied.parents
        except (OSError, RuntimeError):
            inside_root = False
        if not inside_root or copied.name == "manifest.json" or file_hash(copied) != digest:
            return False, "copied user knowledge does not match its manifest", []
    return True, "", entries


def spec_confidence(artifact: Path) -> str | None:
    spec = read_json(Path(f"{artifact}.spec.json"), None)
    return "medium" if _valid_spec(spec) else None


def spec_contract_basis(artifact: Path) -> str | None:
    spec = read_json(Path(f"{artifact}.spec.json"), None)
    # Original FM-Agent treats every generated behavioral specification as its
    # model-inferred condition B.  External provenance is useful context, but
    # it is not part of the persisted spec schema or a prerequisite for A→B.
    return "inferred" if _valid_spec(spec) else None


def _valid_info(data) -> bool:
    if not isinstance(data, dict) or set(data) != {"callees"} or not isinstance(data["callees"], list):
        return False
    return all(
        isinstance(callee, dict)
        and set(callee) == CALLEE_FIELDS
        and all(isinstance(callee[field], str) for field in CALLEE_FIELDS)
        for callee in data["callees"]
    )


def sidecars_ready(artifact: Path) -> tuple[bool, str]:
    """Validate FM-Agent's paired specification and call-information files."""
    source = artifact.read_text(encoding="utf-8", errors="replace") if artifact.is_file() else ""
    if "[SPEC]" in source or "[INFO]" in source:
        return False, "extracted source contains inline specification metadata"
    spec_path, info_path = Path(f"{artifact}.spec.json"), Path(f"{artifact}.info.json")
    spec, info = read_json(spec_path, None), read_json(info_path, None)
    canonical, _ = canonical_spec(spec)
    if canonical is None and isinstance(spec, dict) and all(isinstance(spec.get(field), str) for field in SPEC_FIELDS):
        # Still strip legacy metadata when the native contract itself is
        # invalid, so the validation message names the real contract defect
        # (for example an oracle marker) rather than irrelevant extra keys.
        canonical = {field: spec[field] for field in SPEC_FIELD_ORDER}
    if canonical is not None and canonical != spec:
        atomic_json(spec_path, canonical)
        spec = canonical
    canonical_callees, _ = canonical_info(info)
    if canonical_callees is not None and canonical_callees != info:
        atomic_json(info_path, canonical_callees)
        info = canonical_callees
    spec_ok, spec_reason = _spec_schema_ready(spec)
    if not spec_ok:
        return False, f"invalid spec sidecar {spec_path.name}: {spec_reason}"
    if not _valid_info(info):
        return False, f"missing or invalid info sidecar: {info_path.name}"
    return True, ""


def _in_selected_scope(rel: str, submodules: list[str] | None) -> bool:
    return not submodules or any(rel == item.rstrip("/") or rel.startswith(item.rstrip("/") + "/") for item in submodules)


def _current_function_artifacts(extracted: Path, submodules: list[str] | None) -> set[str]:
    if not extracted.is_dir():
        return set()
    return {
        path.relative_to(extracted).as_posix()
        for path in extracted.rglob("*")
        if path.is_file()
        and not is_metadata_sidecar(path)
        and _in_selected_scope(path.relative_to(extracted).as_posix(), submodules)
    }


def specification_artifacts_ready(project: Path, functions: list[dict], submodules: list[str] | None = None) -> tuple[bool, str]:
    """Validate current extracted copies and contracts without requiring verification.

    This deliberately stops before checking logic-verification results: an
    incremental run must be able to finish updating specifications before its
    subsequent verification phase produces new snapshot-aligned results.
    """
    extracted = fm_dir(project) / "extracted_functions"
    expected_artifacts = set()
    for item in functions:
        function_id = item.get("id")
        rel = item.get("artifact") or item.get("extracted_path")
        if not isinstance(function_id, str) or not isinstance(rel, str):
            return False, "source_index contains a function without id or artifact"
        artifact = extracted / rel
        normalized_rel = Path(rel).as_posix()
        expected_artifacts.add(normalized_rel)
        if not artifact.is_file():
            return False, f"missing extracted artifact for {function_id}"
        sidecars_ok, sidecars_reason = sidecars_ready(artifact)
        if not sidecars_ok:
            return False, f"incomplete specification for {function_id}: {sidecars_reason}"
    actual_artifacts = _current_function_artifacts(extracted, submodules)
    stale_artifacts = actual_artifacts - expected_artifacts
    if stale_artifacts:
        return False, f"stale extracted artifact: {sorted(stale_artifacts)[0]}"
    return True, ""


def semantic_job_plan_ready(project: Path, phase: str, functions: list[dict] | None = None, candidate_ids: set[str] | None = None) -> tuple[bool, str]:
    """Require deterministic queue coverage before a semantic phase can pass."""
    plan = read_json(control_dir(project) / "job_plans" / f"{phase}.json", None)
    if not isinstance(plan, dict) or set(plan) != {"schema_version", "phase", "snapshot_commit", "entries", "created_jobs", "total_entries"}:
        return False, f"missing or invalid deterministic job plan for {phase}"
    if plan.get("schema_version") != 1 or plan.get("phase") != phase or plan.get("snapshot_commit") != current_snapshot_commit(project):
        return False, f"stale deterministic job plan for {phase}"
    entries = plan.get("entries")
    if not isinstance(entries, list) or plan.get("total_entries") != len(entries) or not isinstance(plan.get("created_jobs"), int):
        return False, f"invalid deterministic job plan counts for {phase}"
    artifact_phases = {"specification", "verification", "verify_affected"}
    identity_key = "artifact" if phase in artifact_phases else "function_id"
    expected = ({item.get("artifact") for item in functions or []} if identity_key == "artifact" else set(candidate_ids or set()))
    actual = set()
    expected_types = {"spec_batch"} if phase == "specification" else {"verify_function", "verify_batch"} if phase in {"verification", "verify_affected"} else {"bug_validate"}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {identity_key, "job_id"} or not isinstance(entry.get(identity_key), str):
            return False, f"invalid entry in deterministic job plan for {phase}"
        identity, job_id = entry[identity_key], entry["job_id"]
        if identity in actual or job_id is not None and not isinstance(job_id, str):
            return False, f"duplicate or invalid entry in deterministic job plan for {phase}"
        actual.add(identity)
        if job_id is None:
            continue
        job = read_json(skill_dir(project) / "jobs" / f"{job_id}.json", None)
        if not isinstance(job, dict) or job.get("id") != job_id or job.get("phase") != phase or job.get("type") not in expected_types:
            return False, f"job plan references an invalid job: {job_id}"
        if identity_key == "artifact" and identity not in job.get("artifacts", []) and identity not in job.get("completed_artifacts", []):
            return False, f"job {job_id} does not own planned artifact {identity}"
        if identity_key == "function_id" and job.get("input", {}).get("function_id") != identity:
            return False, f"job {job_id} does not own planned function {identity}"
    if actual != expected:
        return False, f"deterministic job plan for {phase} does not cover the current scope"
    return True, ""


def _nonempty_text(value, limit: int = 8000) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _postcondition_reasoning_ready(artifact: Path, spec: dict, reasoning, mismatch: bool) -> tuple[bool, str]:
    if not isinstance(reasoning, dict) or set(reasoning) != POSTCONDITION_REASONING_FIELDS:
        return False, "MATCH/MISMATCH requires exact structured postcondition reasoning"
    actual = reasoning.get("actual_postcondition")
    required = reasoning.get("spec_postcondition")
    counterexample = reasoning.get("counterexample")
    statements = reasoning.get("offending_statements")
    reason = reasoning.get("reason")
    if not _nonempty_text(actual) or required != spec.get("post_condition"):
        return False, "reasoning must contain an actual postcondition and the exact specification postcondition"
    if not isinstance(reason, str) or len(reason) > 8000:
        return False, "reasoning reason must be a bounded string"
    if mismatch:
        if not all(_nonempty_text(value) for value in (counterexample, statements, reason)):
            return False, "MISMATCH requires a concrete counterexample, exact offending statements, and reason"
        source = artifact.read_text(encoding="utf-8", errors="replace")
        if statements not in source:
            return False, "MISMATCH offending statements are not an exact quote from the extracted function"
    elif counterexample is not None or statements is not None:
        return False, "MATCH cannot contain a counterexample or offending statements"
    return True, ""


def verification_result_ready(project: Path, artifact: Path, function_id: str, result, snapshot_commit: str | None = None) -> tuple[bool, str]:
    """Validate one auditable A→B reasoner result before it can affect state."""
    if not artifact.is_file():
        return False, "verification input artifact is missing"
    sidecars_ok, sidecars_reason = sidecars_ready(artifact)
    if not sidecars_ok:
        return False, f"verification input is invalid: {sidecars_reason}"
    if not isinstance(result, dict) or set(result) != VERIFICATION_FIELDS or result.get("schema_version") != 2:
        return False, "verification result does not match schema version 2"
    if result.get("function_id") != function_id:
        return False, "verification function identity mismatch"
    if result.get("snapshot_commit") != (snapshot_commit or current_snapshot_commit(project)):
        return False, "verification snapshot mismatch"
    verdict = result.get("verdict")
    if verdict not in VERDICTS:
        return False, "verification verdict is invalid"
    spec = read_json(Path(f"{artifact}.spec.json"), None)
    basis = spec_contract_basis(artifact)
    if verdict in {"MATCH", "MISMATCH"}:
        if basis not in {"normative", "inferred"}:
            return False, f"unavailable specification contract cannot establish {verdict}"
        if result.get("gaps") is not None or result.get("error") is not None:
            return False, f"{verdict} must use structured reasoning rather than gaps/error"
        return _postcondition_reasoning_ready(artifact, spec, result.get("reasoning"), verdict == "MISMATCH")
    if result.get("reasoning") is not None:
        return False, f"{verdict} cannot claim a completed postcondition proof"
    if verdict == "INCONCLUSIVE":
        gaps = result.get("gaps")
        if result.get("error") is not None or not isinstance(gaps, dict) or set(gaps) != {"missing_evidence", "reason"}:
            return False, "INCONCLUSIVE requires missing_evidence and reason"
        missing = gaps.get("missing_evidence")
        if not isinstance(missing, list) or not missing or len(missing) > 50 or not all(_nonempty_text(item, 2000) for item in missing) or not _nonempty_text(gaps.get("reason")):
            return False, "INCONCLUSIVE evidence gaps must be non-empty"
        return True, ""
    if verdict == "DEPENDENCY_RISK":
        gaps = result.get("gaps")
        if result.get("error") is not None or not isinstance(gaps, dict) or set(gaps) != {"affected_callee_ids", "reason"}:
            return False, "DEPENDENCY_RISK requires affected_callee_ids and reason"
        callees = gaps.get("affected_callee_ids")
        known = {item.get("id") for item in (source_index(project) or {}).get("functions", []) if isinstance(item, dict)}
        if not isinstance(callees, list) or not callees or len(callees) > 100 or function_id in callees or not all(isinstance(item, str) and item in known for item in callees) or not _nonempty_text(gaps.get("reason")):
            return False, "DEPENDENCY_RISK must name known, distinct callees"
        return True, ""
    if result.get("gaps") is not None or not _nonempty_text(result.get("error")):
        return False, "ERROR requires a non-empty error and no reasoning/gaps"
    return True, ""


def function_artifacts_ready(project: Path, functions: list[dict], submodules: list[str] | None = None, snapshot_commit: str | None = None) -> tuple[bool, str]:
    extracted = fm_dir(project) / "extracted_functions"
    results = fm_dir(project) / "logic_verification_results"
    expected_artifacts = set()
    expected_results = set()
    for item in functions:
        function_id = item.get("id")
        rel = item.get("artifact") or item.get("extracted_path")
        if not isinstance(function_id, str) or not isinstance(rel, str):
            return False, "source_index contains a function without id or artifact"
        artifact = extracted / rel
        expected_artifacts.add(Path(rel).as_posix())
        expected_results.add(Path(rel).with_suffix(".json").as_posix())
        if not artifact.is_file():
            return False, f"missing extracted artifact for {function_id}"
        sidecars_ok, sidecars_reason = sidecars_ready(artifact)
        if not sidecars_ok:
            return False, f"incomplete specification for {function_id}: {sidecars_reason}"
        result_path = results / (str(Path(rel).with_suffix(".json")))
        result = read_json(result_path, None)
        result_ok, result_reason = verification_result_ready(project, artifact, function_id, result, snapshot_commit)
        if not result_ok:
            return False, f"invalid verification result for {function_id}: {result_reason}"
    actual_artifacts = _current_function_artifacts(extracted, submodules)
    actual_results = {path.relative_to(results).as_posix() for path in results.rglob("*.json") if _in_selected_scope(path.relative_to(results).with_suffix("").as_posix(), submodules)} if results.is_dir() else set()
    stale_artifacts = actual_artifacts - expected_artifacts
    stale_results = actual_results - expected_results
    if stale_artifacts:
        return False, f"stale extracted artifact: {sorted(stale_artifacts)[0]}"
    if stale_results:
        return False, f"stale verification result: {sorted(stale_results)[0]}"
    return True, ""


def selected_verification_ready(project: Path, functions: list[dict]) -> tuple[bool, str]:
    """Validate only the current incremental selection without requiring a full rerun."""
    results = fm_dir(project) / "logic_verification_results"
    for item in functions:
        rel, function_id = item.get("artifact"), item.get("id")
        if not all(isinstance(value, str) for value in (rel, function_id)):
            return False, "selected function lacks artifact identity"
        artifact = fm_dir(project) / "extracted_functions" / rel
        result = read_json(results / Path(rel).with_suffix(".json"), {})
        result_ok, result_reason = verification_result_ready(project, artifact, function_id, result)
        if not result_ok:
            return False, f"invalid selected verification result for {function_id}: {result_reason}"
    return True, ""


def verification_coverage(project: Path, functions: list[dict]) -> dict:
    """Summarize semantic coverage after every result passed schema validation."""
    counts = {verdict: 0 for verdict in sorted(VERDICTS)}
    bases = {basis: 0 for basis in sorted(SPEC_CONTRACT_BASES)}
    results = fm_dir(project) / "logic_verification_results"
    for item in functions:
        rel = item.get("artifact") if isinstance(item, dict) else None
        if not isinstance(rel, str):
            continue
        artifact = fm_dir(project) / "extracted_functions" / rel
        basis = spec_contract_basis(artifact)
        if basis in bases:
            bases[basis] += 1
        result = read_json(results / Path(rel).with_suffix(".json"), {})
        verdict = result.get("verdict") if isinstance(result, dict) else None
        if verdict in counts:
            counts[verdict] += 1
    conclusive = counts["MATCH"] + counts["MISMATCH"]
    return {
        "total": len(functions),
        "contract_bases": bases,
        "verdicts": counts,
        "conclusive": conclusive,
        "conclusive_ratio": (conclusive / len(functions)) if functions else 0.0,
    }


def verification_coverage_ready(project: Path, functions: list[dict]) -> tuple[bool, str]:
    """Require majority semantic coverage before creating an official baseline."""
    coverage = verification_coverage(project, functions)
    if not functions:
        return False, "insufficient_specification: verification scope is empty"
    independent = coverage["contract_bases"]["normative"] + coverage["contract_bases"]["inferred"]
    required = (len(functions) + 1) // 2
    if independent < required:
        return False, f"insufficient_specification: only {independent}/{len(functions)} functions have an independent normative or inferred contract; at least {required} are required"
    if coverage["verdicts"]["ERROR"]:
        return False, f"verification_incomplete: {coverage['verdicts']['ERROR']} semantic worker result(s) ended in ERROR"
    if coverage["conclusive"] < required:
        return False, f"insufficient_specification: only {coverage['conclusive']}/{len(functions)} functions reached MATCH or MISMATCH; at least {required} are required"
    return True, ""


def direct_mismatch_ids(project: Path, selected_ids: set[str] | None = None) -> set[str]:
    """Return current, schema-valid normative or inferred direct candidates."""
    results = fm_dir(project) / "logic_verification_results"
    found = set()
    for item in (source_index(project) or {}).get("functions", []):
        function_id = item.get("id") if isinstance(item, dict) else None
        rel = item.get("artifact") if isinstance(item, dict) else None
        if not isinstance(function_id, str) or not isinstance(rel, str) or (selected_ids is not None and function_id not in selected_ids):
            continue
        artifact = fm_dir(project) / "extracted_functions" / rel
        result = read_json(results / Path(rel).with_suffix(".json"), {})
        valid, _ = verification_result_ready(project, artifact, function_id, result)
        if valid and result.get("verdict") == "MISMATCH":
            found.add(function_id)
    return found


def _project_relative(project: Path, value) -> Path | None:
    if not isinstance(value, str):
        return None
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (project / candidate).resolve()
    return resolved if project.resolve() in resolved.parents else None


def _dynamic_attempt_ready(project: Path, attempt: dict, snapshot_commit: str) -> tuple[bool, str]:
    if not isinstance(attempt, dict) or attempt.get("classification") not in BUG_ATTEMPT_CLASSIFICATIONS:
        return False, "bug attempt has an invalid classification"
    evidence = attempt.get("dynamic_evidence")
    if not isinstance(evidence, dict) or set(evidence) != {"reproduction_result"}:
        return False, "bug attempt lacks its reproduction result evidence"
    path = _project_relative(project, evidence.get("reproduction_result"))
    if path is None or not path.is_file() or "fm_agent_skill/probes" not in path.as_posix().replace("\\", "/"):
        return False, "bug attempt references an invalid reproduction result path"
    result = read_json(path, None)
    if not isinstance(result, dict) or result.get("snapshot_commit") != snapshot_commit:
        return False, "reproduction result is missing or belongs to another snapshot"
    classification = attempt["classification"]
    if classification == "inconclusive":
        if result.get("classification") != "inconclusive" or result.get("state") not in {"completed", "unsupported"}:
            return False, "inconclusive attempt lacks matching completed reproduction evidence"
    elif result.get("state") != "completed" or result.get("classification") != classification:
        return False, "bug attempt classification does not match its dynamic reproduction result"
    return True, ""


def bug_validation_ready(project: Path, candidate_ids: set[str]) -> tuple[bool, str]:
    """Require dynamic evidence for every direct MISMATCH before phase success."""
    root = fm_dir(project) / "bug_validation"
    summary = read_json(root / "summary.json", None)
    if not isinstance(summary, dict):
        return False, "missing or invalid bug validation summary"
    reports: dict[str, dict] = {}
    for path in root.glob("*.result.json") if root.is_dir() else []:
        report = read_json(path, None)
        function_id = report.get("function_id") if isinstance(report, dict) else None
        if not isinstance(function_id, str) or function_id in reports:
            return False, "bug validation report has missing or duplicate function identity"
        reports[function_id] = report
    missing = candidate_ids - set(reports)
    extra = set(reports) - candidate_ids
    if missing or extra:
        return False, "bug validation reports do not match current direct MISMATCH candidates"
    snapshot = current_snapshot_commit(project)
    if summary.get("snapshot_commit") != snapshot or summary.get("total_candidates") != len(candidate_ids):
        return False, "bug validation summary does not match the current snapshot or candidate count"
    counts = {"confirmed": 0, "rejected": 0, "inconclusive": 0}
    for function_id, report in reports.items():
        if report.get("snapshot_commit") != snapshot or report.get("confirmation_status") not in BUG_FINAL_STATUSES:
            return False, f"bug validation report is invalid for {function_id}"
        attempts = report.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            return False, f"bug validation report has no attempts for {function_id}"
        for attempt in attempts:
            ok, reason = _dynamic_attempt_ready(project, attempt, snapshot)
            if not ok:
                return False, f"{function_id}: {reason}"
        latest = attempts[-1].get("classification")
        status = report["confirmation_status"]
        if status == "confirmed" and latest != "confirmed":
            return False, f"confirmed bug lacks confirmed dynamic evidence for {function_id}"
        if status == "rejected" and latest != "not_reproduced":
            return False, f"rejected bug lacks executed non-reproduction evidence for {function_id}"
        if status == "inconclusive" and latest != "inconclusive":
            return False, f"inconclusive bug has an incompatible final attempt for {function_id}"
        counts[status] += 1
    for status, count in counts.items():
        if summary.get(f"total_{status}") != count:
            return False, f"bug validation summary has an invalid {status} count"
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
    indexed = source_index(project).get("functions", [])
    expected, seen_functions, expected_functions = [], set(), set()
    for index, phase in enumerate(phases, start=1):
        number = _phase_number(phase, index)
        path = root / f"phase_{number:02d}_topdown_layers.json"
        sources = _phase_sources(phase)
        phase_functions = {item.get("id") for item in indexed if isinstance(item, dict) and item.get("path") in sources and isinstance(item.get("id"), str)}
        if not phase_functions:
            continue
        expected.append(path.name); expected_functions.update(phase_functions)
        data = read_json(path, None)
        if not isinstance(data, dict) or data.get("phase") != number:
            return False, f"missing or invalid layer artifact for phase {number}"
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
    if seen_functions != expected_functions: return False, "layer artifacts do not cover every indexed phase function"
    return True, ""


def specification_context_ready(project: Path) -> tuple[bool, str]:
    """Validate the non-optional FM-Agent context used to write sidecars."""
    phases_data = read_json(fm_dir(project) / "phases.json", {})
    phases = phases_data.get("phases") if isinstance(phases_data, dict) else None
    root = fm_dir(project) / "spec_prompts"
    if not isinstance(phases, list) or not phases:
        return False, "missing phases for specification context"
    system_prompt = root / "system_prompt.md"
    if not system_prompt.is_file():
        return False, "missing specification system prompt"
    if _ORACLE_MARKER.search(system_prompt.read_text(encoding="utf-8", errors="replace")):
        return False, "specification system prompt contains benchmark/debug oracle markers"
    domain = root / "domain_context"
    overview = domain / "engine_overview.txt"
    if not overview.is_file():
        return False, "missing engine overview"
    knowledge_ready, knowledge_reason, _ = _knowledge_manifest_ready(project)
    if not knowledge_ready:
        return False, knowledge_reason
    context_files = [overview]
    for index, phase in enumerate(phases, start=1):
        number = _phase_number(phase, index)
        phase_context = domain / f"phase_{number:02d}_types.txt"
        if not phase_context.is_file():
            return False, f"missing phase type context for phase {number}"
        context_files.append(phase_context)
    for path in context_files:
        content = path.read_text(encoding="utf-8", errors="replace")
        if not content.startswith(OBSERVATIONAL_CONTEXT_MARKER + "\n"):
            return False, f"generated domain context lacks its observational marker: {path.name}"
        if _ORACLE_MARKER.search(content):
            return False, f"generated domain context contains benchmark/debug oracle markers: {path.name}"
    return True, ""


def specs_ready(project: Path, submodules: list[str] | None = None) -> tuple[bool, str, int]:
    functions = scoped_functions(project, submodules or [])
    if not functions:
        return False, "missing or invalid Skill control analysis_index.json", 0
    ready, reason = function_artifacts_ready(project, functions, submodules)
    return ready, reason, len(functions)


def inspect_baseline(project: Path, config_fingerprint: str, submodules: list[str] | None = None) -> dict:
    phases = read_json(fm_dir(project) / "phases.json", None)
    if not isinstance(phases, dict) or not isinstance(phases.get("phases"), list):
        return {"valid": False, "reason": "missing or invalid fm_agent/phases.json"}
    saved = read_json(skill_dir(project) / "baseline.json", {})
    if not isinstance(saved, dict) or saved.get("schema_version") != 4 or saved.get("fingerprint") != config_fingerprint:
        return {"valid": False, "reason": "analysis range or configuration is incompatible"}
    commit = saved.get("baseline_commit")
    if not isinstance(commit, str):
        return {"valid": False, "reason": "missing successful baseline commit"}
    try:
        git(project, "cat-file", "-e", f"{commit}^{{commit}}")
    except RuntimeError:
        return {"valid": False, "reason": f"baseline commit is unavailable: {commit}"}
    if baseline_commit(project) != commit:
        return {"valid": False, "reason": "baseline Git ref does not match baseline record"}
    functions = scoped_functions(project, submodules or [])
    if not functions:
        return {"valid": False, "reason": "no indexed functions in the selected scope"}
    ready, reason = function_artifacts_ready(project, functions, snapshot_commit=commit)
    if not ready:
        return {"valid": False, "reason": reason}
    layers_ready, layers_reason = phase_layers_ready(project)
    if not layers_ready:
        return {"valid": False, "reason": layers_reason}
    if not isinstance(saved.get("completed_at"), str):
        return {"valid": False, "reason": "baseline lacks completion provenance"}
    return {"valid": True, "commit": commit, "function_count": len(functions), "saved": saved}


def untracked_sources(project: Path) -> list[str]:
    return [item for item in git(project, "ls-files", "--others", "--exclude-standard").splitlines() if is_supported_source_path(item)]


def is_supported_source_path(value: str) -> bool:
    path = value.replace("\\", "/")
    return Path(path).suffix.lower() in SOURCE_EXTENSIONS and not path.startswith(("fm_agent/", "fm_agent_skill/")) and not is_test_source_path(path)


def changed_since(project: Path, commit: str) -> bool:
    return bool(changed_source_paths(project, commit))


def build_intent(project: Path, base_commit: str, note: str) -> Path:
    git(project, "cat-file", "-e", f"{base_commit}^{{commit}}")
    path = skill_dir(project) / "control" / "incremental_intent.md"
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
