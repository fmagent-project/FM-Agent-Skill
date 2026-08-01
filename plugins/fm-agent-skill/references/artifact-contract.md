# Artifact contract

All JSON is UTF-8 and atomically replaced. Paths are repository-relative with
forward slashes.

All mutable Skill state lives under `fm_agent_skill/`.

- `fm_agent/phases.json` uses FM-Agent's phase/module form, including source
  files and inter-phase dependencies. Test sources are excluded from this
  contract and from all analysis inputs.
- `fm_agent/fm_agent_file_list.json` is the sorted current set of extracted
  function-source paths, relative to `extracted_functions/`. Do not list
  metadata sidecars.
- `fm_agent/extracted_functions/` contains immutable function copies. Every
  `<function>.<ext>` has exactly two paired sidecars:
  `<function>.<ext>.spec.json` and `<function>.<ext>.info.json`. Include
  analyzable header-defined functions and constructors as well as implementation
  files.
- `fm_agent/spec_prompts/` contains required specification context:
  `system_prompt.md`, `domain_context/engine_overview.txt`, and one
  `domain_context/phase_XX_types.txt` for every phase. Materialize supplied
  domain knowledge through the deterministic knowledge command below.
  `knowledge.py materialize` owns that directory and binds every copied
  Markdown file to the active input hash and snapshot commit; semantic workers
  must not create or replace these files. Preserve the
  generated caller-first batch prompts and their manifest under
  `batch_prompts_<project>_phaseXX/` so work is resumable.
- `fm_agent/spec_prompts/phase_XX_topdown_layers.json` is one caller-first
  layer file per declared phase, using its zero-padded numeric phase number.
  This is the native call-graph artifact; do not add `fm_agent/call_graph.json`
  or merge phases unless `one_phase` is enabled.
- `fm_agent/logic_verification_results/` has one result per extracted function.
  A verdict is `MATCH`, direct `MISMATCH`, `DEPENDENCY_RISK`, `INCONCLUSIVE`,
  or `ERROR`. `MATCH` is valid only for a high-confidence specification with
  external evidence.
  Dependency risk records affected callers but is not a direct bug candidate.
- `fm_agent/bug_validation/` is generated only when a direct `MISMATCH` is
  dynamically probed. Every current candidate has a detail Markdown file,
  result JSON, and summary; a clean run need not create this directory.
- `fm_agent/version.log`, written after a successful baseline, records the
  commit held by `refs/fm-agent-skill/baseline` for FM-Agent-compatible
  provenance.
  `fm_agent/incremental_updated_specs.json` records the current incremental
  update.
- `fm_agent/select_relevant_modules.md`, `relevant_modules.json`,
  `select_relevant_files_<n>.md`, `relevant_files_<n>.json`,
  `spec_update_<n>.md`, and `spec_update_<n>.json` are latest-run incremental
  work records. Write only records used by the selected scope; remove them
  before a new full or incremental analysis.
- `fm_agent/trace/events.jsonl` records stage and model events. Write full
  prompt/response material below `trace/payloads/` only when full tracing is
  enabled.
- `fm_agent_skill/control/analysis_index.json` is the Skill-owned function
  identity inventory for one snapshot commit. Precision, incremental
  diff/selection,
  `graph_edges.json`, and validated `agent_static_edges.json` are Skill-owned
  control artifacts. Active analysis state, locks, scheduler jobs, build profiles, and probe results also belong in
  `fm_agent_skill/`, never in `fm_agent/`. Only the Coordinator and
  deterministic scripts may write Skill-owned control state.
- `fm_agent_skill/control/job_plans/<phase>.json` is the current-snapshot,
  deterministic full-scope queue manifest. Specification, verification, and
  Bug Validation gates reject a missing, stale, partial, or hand-divergent plan.
- `fm_agent_skill/failure.json` is a small failure receipt copied to the user's
  worktree when an isolated run fails. It never promotes private semantic
  artifacts; it records that no official result exists and where resume state
  was retained.

## Sidecar schemas

`<function>.<ext>.spec.json` is exactly:

```json
{"schema_version":2,"signature":"...","pre_condition":"...","post_condition":"...","normative_evidence":[{"kind":"user_requirement|public_api_contract|caller_contract","source":"repository-relative path or caller function id","quote":"exact source text","claims":["verbatim contract clause"]}],"observations":[{"kind":"implementation","source":"repository-relative production source","quote":"exact implementation text","claims":["observed behavior"]}],"confidence":"high|low"}
```

`high` requires at least one exact `user_requirement` or
`public_api_contract`; generated domain context and caller-only cycles are not
authority. `low` has no normative evidence and at least one implementation
observation. A normative claim must appear verbatim in the pre/postcondition.
User requirements quote the copied files under
`domain_context/user_knowledge/`; public API contracts quote non-generated
documentation or source comments. Oracle markers such as `BUG:`, `FIXME:`,
`TODO:`, seeded bugs, and known defects are rejected as normative evidence.

The seven-key object is a closed schema: extra keys are invalid. Encode
observable error, exception, rejection, and sentinel behavior inside
`post_condition`; do not add an `error_behavior` field.

`<function>.<ext>.info.json` is exactly `{"callees": [...]}`. Every callee is
an object with string fields `name`, `signature`, `pre_condition`, and
`post_condition`. Use `[]` when there are no in-scope callees.

An extracted function artifact is its unchanged source plus both valid
sidecars. It must be addressable by `source_index.functions[].artifact`.
Sidecars are never functions: exclude them from file lists, source indices, and
stale-artifact checks.

Layer artifact: `{phase, phase_id?, phase_name, source_files, total_layers,
layers}`. `source_files` must match its phase's declared source files. Each
layer function has at least `function_id`, `artifact`, and `source_file`; a
function occurs in one phase-layer artifact only.

Verification result is exactly
`{schema_version,function_id,snapshot_commit,verdict,reasoning,gaps,error}` with
`schema_version: 2`. `MATCH` and `MISMATCH` require `reasoning` exactly equal to
`{actual_postcondition,spec_postcondition,counterexample,offending_statements,reason}`.
The specification postcondition must equal the sidecar text. `MISMATCH` also
requires a high-confidence specification, non-empty concrete counterexample,
reason, and an exact contiguous source quote in `offending_statements`;
`MATCH` requires high confidence and null counterexample/offending statements.
`MISMATCH` means a local implementation/spec violation;
`DEPENDENCY_RISK` means a caller is affected by a callee mismatch but has no
independently established local violation and uses
`gaps:{affected_callee_ids,reason}`. `INCONCLUSIVE` uses
`gaps:{missing_evidence,reason}`. `ERROR` carries a non-empty `error`. Identity
and snapshot commit must match the current analysis worktree.

Finding/bug result records at least `{function_id, snapshot_commit,
confirmation_status, attempts}` plus the specification claim, implementation
evidence, trigger, probe, and output. `confirmation_status` is `confirmed`,
`rejected`, or `inconclusive`; it is never inferred from a build result. Each
attempt has `{ordinal, classification, trigger, probe, output,
dynamic_evidence, timestamp}` where classification is `confirmed`,
`not_reproduced`, or `inconclusive` and `dynamic_evidence` is exactly
`{"reproduction_result":"fm_agent_skill/probes/.../reproduction_result.json"}`.
The referred result belongs to the same snapshot and its completed
classification must match. A report can be confirmed only by `CONFIRMED` runtime
evidence; it can be rejected only by executed `NOT CONFIRMED` evidence after the
configured negative attempts. `summary.json` is Coordinator-authored and has
at least `{snapshot_commit, total_candidates, total_confirmed, total_rejected,
total_inconclusive}`; its counts must equal the current result files.

Every new Bug Validator job assigns exactly one
`fm_agent/bug_validation/*.result.json` output. Its current receipt
classification must equal the classification in the last appended attempt.

`fm_agent_skill/probes/<bug-id>/attempt_<n>/` is immutable and contains the
worker-authored `reproduction.json`, `probe.<ext>`, optional `build_result.json`,
and Coordinator-authored `reproduction_result.json`. The reproduction contract
has an approved language adapter, public-entrypoint explanation, fixed markers,
current snapshot commit, and no shell command. Only `reproduction_runner.py`
executes it using a fixed adapter command.

`fm_agent_skill/active.json` is the sole current-analysis record. It contains
mode, phase status, inputs, fingerprint, timestamps, an immutable
`snapshot_commit`, and resume count; it is overwritten by the next analysis.
The source baseline is the Git ref `refs/fm-agent-skill/baseline`, mirrored by
the latest commit in `fm_agent/version.log`. `baseline.json` stores only the
successful run's configuration and completion provenance; source and function
hashes are not persisted.

`fm_agent_skill/jobs/<job-id>.json` records a current host worker's phase,
type, dependencies, permitted/required outputs, attempt count, terminal status,
and a compact worker receipt. Workers never write these manifests. They are
deleted after a successful terminal analysis, and retained only while a failed
analysis remains resumable. A receipt is at most 4 KiB and includes matching
`job_id`, non-empty `status`, output paths, optional verdict, and short summary.
Detailed incremental plans are written only to their assigned
`fm_agent_skill/worker_reports/<job-id>.json`, not returned inline.

`fm_agent_skill/control/phase_receipts/<phase>.json` is Coordinator-generated
and contains only phase totals, gate readiness, and escalation job IDs. It is
the normal worker fan-in surface; inspect detailed outputs only for its listed
escalations.
