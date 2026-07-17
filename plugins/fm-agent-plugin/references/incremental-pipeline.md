# Incremental pipeline

Dispatch enters this pipeline only with a valid compatible full baseline and an
automatically written intent. Every selection must be recorded in
`fm_agent_plugin/control/incremental_decision.json` with a reason: `diff`, `intent`,
`caller-propagation`, `callee-propagation`, or `spec-change`.

1. `validate_baseline`: confirm baseline index, hashes, specs, and verification artifacts.
2. `refresh_plan`: refresh project phases and context for the chosen scope.
3. `preserve_specs`: snapshot compatible headers in plugin control state before re-extraction.
4. `diff`: write added/modified/removed function identities and source hashes in plugin control state.
5. `rebuild_graph`: recreate graph, layers, precision, and supplemental edges with the backend selected for this run. Reuse or refresh an authorized CodeGraph index when available; otherwise record the agent-static fallback and its reason.
6. `select_scope`: include changed and propagated functions; exclude every other indexed function with a reason.
7. `update_specs`: restore only hash-compatible headers, regenerate affected contracts, reconcile caller `[INFO]` expectations, and write native `fm_agent/incremental_updated_specs.json`.
8. `verify_affected`: write a result for each selected function; use `DEPENDENCY_RISK` rather than converting a callee's direct mismatch into a caller mismatch.
9. `bug_validation`: validate only new direct `MISMATCH` candidates in an isolated probe build and update the summary only when candidates exist.
10. `finalize`: gate all retained and selected artifacts, then save the new baseline.

Deleted functions must be absent from the plugin control analysis index, extracted artifacts,
required result mapping, and retained-spec mapping. A stale or malformed
artifact makes the gate fail rather than being treated as valid reuse.

Read [agent-orchestration.md](agent-orchestration.md) for the host-agent and
CodeGraph authorization model.
