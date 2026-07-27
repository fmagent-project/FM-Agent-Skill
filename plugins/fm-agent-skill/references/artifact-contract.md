# Artifact contract

All JSON is UTF-8 and atomically replaced. Paths are repository-relative with
forward slashes.

- `fm_agent/phases.json` uses FM-Agent's phase/module form, including source
  files and inter-phase dependencies.
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
  `domain_context/phase_XX_types.txt` for every phase. Copy supplied domain
  knowledge to `domain_context/user_knowledge/` with a manifest. Preserve the
  generated caller-first batch prompts and their manifest under
  `batch_prompts_<project>_phaseXX/` so work is resumable.
- `fm_agent/spec_prompts/phase_XX_topdown_layers.json` is one caller-first
  layer file per declared phase, using its zero-padded numeric phase number.
  This is the native call-graph artifact; do not add `fm_agent/call_graph.json`
  or merge phases unless `one_phase` is enabled.
- `fm_agent/logic_verification_results/` has one result per extracted function.
  A verdict is `MATCH`, direct `MISMATCH`, `DEPENDENCY_RISK`, or `ERROR`.
  Dependency risk records affected callers but is not a direct bug candidate.
- `fm_agent/bug_validation/` is generated only when a direct `MISMATCH` is
  probed. Confirmed candidates have a detail Markdown file, result JSON, and
  summary; a clean run need not create this directory.
- `fm_agent/version.log`, written after a successful baseline, is source-version
  provenance only. It is never a resume or baseline authority.
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
- `fm_agent_skill/control/analysis_index.json` is the plugin-owned function
  identity/hash inventory. Precision, incremental snapshots/diff/selection,
  run records, locks, and probe-build results also belong in `fm_agent_skill/`,
  never in `fm_agent/`.

## Sidecar schemas

`<function>.<ext>.spec.json` is exactly:

```json
{"signature":"...","pre_condition":"...","post_condition":"..."}
```

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

Verification result: `{function, function_id, source_hash, verdict, gaps?,
error?}`. `MISMATCH` means a local implementation/spec violation;
`DEPENDENCY_RISK` means a caller is affected by a callee mismatch but has no
independently established local violation. Identity and hash must match the
control analysis index.

Finding/bug result records function identity, spec claim, implementation
evidence, trigger/probe, output, and status `candidate`, `confirmed`,
`rejected`, or `error`. `summary.json` counts each status.

`fm_agent_skill/runs/<run-id>.json` records mode, phase status, inputs,
fingerprint, timestamps, and terminal state. Resumable runs additionally retain
their starting source snapshot, effective configuration, phase history, and
resume count. `baseline.json` is written only by a successfully completed run.
