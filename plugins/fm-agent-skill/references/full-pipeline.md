# Full pipeline

Follow the listed order. Start and complete every phase through `pipeline.py`;
the corresponding gate in [stage-gates.md](stage-gates.md) is the acceptance
authority.

1. `preflight`: validate Git, source files, merged settings, and lock ownership.
2. `project_understanding`: dispatch `fm-phase-plan-worker`, then write/gate native-style `phases.json`, separating modules and entry points when their dependencies differ. Name each phase after functions assigned to that phase's source files, not functions that merely call into it; a phase must not claim ownership of a REPL, parser, or other function assigned to another phase.
3. `phase_cleanup`: remove empty/duplicate phases without expanding the selected scope. Its `pipeline.py phase-start` transition automatically preserves the current `phases.json` and removes old derived extraction, specification, verification, Bug Validator, trace, and plugin-control artifacts. A full run must not reuse semantic outputs from an earlier run.
4. `extraction`: use language extractors and test filtering; write immutable function copies, including analyzable header-defined functions. Build the plugin control analysis index through `artifact_index.py build`.
5. `call_graph`: use the selected CodeGraph index for function boundaries and resolved edges when available; otherwise build an agent-static graph. Validate supplemental edges, then generate one native caller-first layer file per `phases.json` phase. Name them `phase_01_topdown_layers.json`, `phase_02_topdown_layers.json`, and so on; keep every function within its declared phase source files. Only `one_phase=true` permits a single merged phase. Record backend precision through `call_graph.py record-precision` in plugin control state.
6. `specification`: dispatch `fm-domain-context-worker` to materialize context, then `fm-spec-batch-worker` jobs phase-by-phase and caller-first. Same-layer batches may run concurrently; each writes schema-valid `.spec.json`/`.info.json` sidecars without changing source copies.
7. `verification`: dispatch one `fm-verify-function-worker` per ready function. It may begin when its batch validates, while unrelated specification batches still run. Mark direct local violations `MISMATCH`; mark callers affected only through an invalid callee `DEPENDENCY_RISK`, not `MISMATCH`. An individual tool error is `ERROR` and does not stop other functions.
8. `bug_validation`: dispatch one `fm-bug-validate-worker` for each direct `MISMATCH`. For a CMake project, first use `probe_build.py` to configure and build in a run- and bug-specific directory under `fm_agent_skill/probes/`; never reuse the project's `build/` cache. Write reports and a summary only when there are direct candidates.
9. `finalize`: write summaries and a successful baseline only after every previous gate passed.

If a phase fails, record `phase-fail`, retain its outputs, then run
`pipeline.py fail`, which releases the owned lock. Retrying is bounded by the configured retry count; do not
silently bypass a missing artifact.

## Resume

For an explicit resume, read [resume-contract.md](resume-contract.md). Start
at the run record's first incomplete phase, not at `preflight`. Keep all
earlier gate-validated work. Within the resumed phase, retain only current
hash-compatible function artifacts and create missing work. Do not repeat
`phase_cleanup` after it has succeeded. Reuse a readable same-snapshot
CodeGraph index only when resuming the incomplete `call_graph` phase.

The Claude Coordinator dispatches the semantic work to the named workers
instead of calling FM-Agent's original remote-LLM pipeline. Read
[agent-orchestration.md](agent-orchestration.md) and
[subagent-scheduler.md](subagent-scheduler.md) before beginning.
