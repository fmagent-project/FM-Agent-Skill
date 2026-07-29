# Full pipeline

Follow the listed order. Start and complete every phase through `pipeline.py`;
the corresponding gate in [stage-gates.md](stage-gates.md) is the acceptance
authority.

1. `preflight`: validate Git, source files, merged settings, and lock ownership.
2. `project_understanding`: dispatch `fm-phase-plan-worker`, then run `executor.py normalize-phases --project "$PROJECT"` before gating native-style `phases.json`. It writes `modules[].source_files` and `depends_on_phases`, rejects missing sources, and excludes test paths from all analysis. Separate headers/types, implementation, and entry points when their dependencies differ: implementation depends on headers/types and CLI/entry points depend on implementation. Name each phase after functions assigned to that phase's source files, not functions that merely call into it; a phase must not claim ownership of a REPL, parser, or other function assigned to another phase.
3. `phase_cleanup`: remove empty/duplicate phases without expanding the selected scope. Its `pipeline.py phase-start` transition automatically preserves the current `phases.json` and removes old derived extraction, specification, verification, Bug Validator, trace, and Skill-control artifacts. A full run must not reuse semantic outputs from an earlier run.
4. `extraction`: run `executor.py extract --project "$PROJECT"` (plus every selected `--submodule`). It writes immutable function copies and the `fm_agent_skill` control inventory without calling FM-Agent. Never analyze test paths, emit a pseudo-function for declaration-only headers, hash-name artifacts, or write inline `[SPEC]`/`[INFO]` into copied source.
5. `call_graph`: when CodeGraph is selected, run `codegraph.py export --output "$PROJECT/fm_agent_skill/control/codegraph_export.json"`, then run `executor.py graph --project "$PROJECT" --codegraph-export "$PROJECT/fm_agent_skill/control/codegraph_export.json"`. It maps exported nodes/edges to current extracted artifacts and records `exact` only then. Without CodeGraph, dispatch `fm-agent-static-edge-worker`, validate its `fm_agent/agent_static_edges_candidate.json` through `executor.py record-agent-edges`, then rerun `executor.py graph`; only validated edges are used and the backend remains `agent-static/best-effort`.
6. `specification`: dispatch `fm-domain-context-worker` to materialize context, then `fm-spec-batch-worker` jobs phase-by-phase and caller-first. Same-layer batches may run concurrently; each writes schema-valid `.spec.json`/`.info.json` sidecars without changing source copies.
7. `verification`: dispatch one `fm-verify-function-worker` per ready function. It may begin when its batch validates, while unrelated specification batches still run. Mark direct local violations `MISMATCH`; mark callers affected only through an invalid callee `DEPENDENCY_RISK`, not `MISMATCH`. A low-confidence, implementation-derived specification yields `INCONCLUSIVE`, never `MATCH`. An individual tool error is `ERROR` and does not stop other functions.
8. `bug_validation`: dispatch one `fm-bug-validate-worker` for each direct `MISMATCH`. For a CMake project, first use `probe_build.py` to configure and build in a bug-specific directory under `fm_agent_skill/probes/`; never reuse the project's `build/` cache. Write reports and a summary only when there are direct candidates.
9. `finalize`: write summaries and a successful baseline only after every previous gate passed.

If a phase fails, record `phase-fail`, retain its outputs, then run
`pipeline.py fail`, which releases the owned lock. Retrying is bounded by the configured retry count; do not
silently bypass a missing artifact.

## Resume

For an explicit resume, read [resume-contract.md](resume-contract.md). Start
at the active analysis's first incomplete phase, not at `preflight`. Keep all
earlier gate-validated work. Within the resumed phase, retain only current
hash-compatible function artifacts and create missing work. Do not repeat
`phase_cleanup` after it has succeeded. Reuse a readable same-snapshot
CodeGraph index only when resuming the incomplete `call_graph` phase.

The host Coordinator dispatches the semantic work to the named workers
instead of calling FM-Agent's original remote-LLM pipeline. Read
[agent-orchestration.md](agent-orchestration.md) and
[subagent-scheduler.md](subagent-scheduler.md) before beginning.
