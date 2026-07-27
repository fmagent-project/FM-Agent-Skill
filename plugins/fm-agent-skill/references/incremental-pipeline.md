# Incremental pipeline

Dispatch enters this pipeline only with a valid compatible full baseline and an
automatically written intent. Every selection must be recorded in
`fm_agent_skill/control/incremental_decision.json` with a reason: `diff`, `intent`,
`caller-propagation`, `callee-propagation`, or `spec-change`.

1. `validate_baseline`: confirm baseline index, hashes, specs, and verification artifacts.
2. `refresh_plan`: dispatch `fm-phase-plan-worker` (and `fm-domain-context-worker` when context changes) for the chosen scope.
3. `preserve_specs`: snapshot compatible specification and call-information sidecars in plugin control state before re-extraction.
4. `diff`: write added/modified/removed function identities and source hashes in plugin control state.
5. `rebuild_graph`: recreate graph, layers, precision, and supplemental edges with the backend selected for this run. Reuse or refresh a CodeGraph index when available; otherwise record the agent-static fallback and its reason.
6. `select_scope`: use `fm-select-relevant-modules-worker`, then `fm-select-relevant-files-worker`; include changed and propagated functions and exclude every other indexed function with a reason.
7. `update_specs`: dispatch read-only `fm-incremental-spec-plan-worker` jobs for independent functions, serially apply accepted plans, then dispatch one `fm-reconcile-caller-info-worker` per caller frontier. Restore only hash-compatible sidecars and write native `fm_agent/incremental_updated_specs.json`.
8. `verify_affected`: dispatch `fm-verify-function-worker` jobs for selected functions; use `DEPENDENCY_RISK` rather than converting a callee's direct mismatch into a caller mismatch.
9. `bug_validation`: dispatch `fm-bug-validate-worker` only for selected direct `MISMATCH` candidates in an isolated probe build. When one exists, rebuild its probe under `fm_agent_skill/probes/<current-run-id>/`, then overwrite `fm_agent/bug_validation/summary.json` with `run_id` equal to the current incremental run. A summary from an earlier full or incremental run never satisfies this phase.
10. `finalize`: gate all retained and selected artifacts, then save the new baseline.

Deleted functions must be absent from the plugin control analysis index, extracted artifacts,
required result mapping, and retained-spec mapping. A stale or malformed
artifact makes the gate fail rather than being treated as valid reuse.

Read [agent-orchestration.md](agent-orchestration.md) for the host-agent and
CodeGraph backend model.

## Resume

Read [resume-contract.md](resume-contract.md) for an explicit resume. Re-enter
only the first incomplete incremental phase. Do not recalculate a completed
diff, selection decision, or preserved-spec snapshot unless its normal gate no
longer validates, in which case resume must fail rather than silently widening
scope. Reuse only function artifacts whose ids and source hashes still match
the interrupted run.
