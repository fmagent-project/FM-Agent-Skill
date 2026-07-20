# Artifact contract

All JSON is UTF-8 and atomically replaced. Paths are repository-relative with
forward slashes.

- `fm_agent/phases.json`: use FM-Agent's phase/module form, including source files and inter-phase dependencies. Do not require a separate overview or phase-type file.
- `fm_agent/extracted_functions/`: extracted function copies with generated `[SPEC]` and `[INFO]` headers. Include analyzable header-defined functions and constructors as well as implementation files.
- `fm_agent/spec_prompts/phase_XX_topdown_layers.json`: one caller-first layer file for every phase in `phases.json`, using the zero-padded numeric phase number (`phase_01_…`, `phase_02_…`). This is the native call-graph artifact; do not require an additional `fm_agent/call_graph.json`. Do not merge phases unless `one_phase` is enabled.
- `fm_agent/logic_verification_results/`: one result per extracted function. A verdict is `MATCH`, direct `MISMATCH`, `DEPENDENCY_RISK`, or `ERROR`. Dependency risk records affected callers but is not a direct bug candidate.
- `fm_agent/bug_validation/`: generated only when at least one direct `MISMATCH` is probed. Confirmed candidates have a detail Markdown file, result JSON, and summary; a clean run need not create this directory.
- `fm_agent/version.log`, when a successful baseline is recorded, and `fm_agent/incremental_updated_specs.json` for an incremental update are native compatibility artifacts.
- `fm_agent_plugin/control/analysis_index.json` is the plugin-owned function identity/hash inventory. `call_graph_precision.json`, incremental snapshots/diff/selection, and probe build results also belong under `fm_agent_plugin/`, not under `fm_agent/`.
- Extracted function artifact: generated `[SPEC]` pair, generated `[INFO]` pair, then unchanged source. It must be addressable by `source_index.functions[].artifact`.
- Layer artifact: `{phase, phase_id?, phase_name, source_files, total_layers, layers}`. `source_files` must match that phase's declared source files. Each layer function is an object containing at least `function_id`, `artifact`, and `source_file`; `source_file` must belong to `source_files`. A function may occur in only one phase-layer artifact. Preserve the repository-relative original source path here even if the extracted artifact has a generated path.
- Verification result: `{function, function_id, source_hash, verdict, gaps?, error?}`. `MISMATCH` means a local implementation/spec violation; `DEPENDENCY_RISK` means a caller is affected by a callee mismatch but has no independently established local violation. `function_id` and `source_hash` must exactly match the control analysis-index item.
- Finding/bug result: records function identity, spec claim, implementation evidence, trigger/probe, output, and status `candidate`, `confirmed`, `rejected`, or `error`. `summary.json` counts each status.
- Run: `fm_agent_plugin/runs/<run-id>.json` records `mode`, `phase_status`, inputs, fingerprint, timestamps, and terminal state. Resumable runs additionally retain their starting source snapshot, effective configuration, phase history, and resume count. `baseline.json` is written only by a successfully completed run.

There must be exactly one required extracted artifact and one required result
path for every current indexed function. Removed functions must not remain in
the required mappings.
