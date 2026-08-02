# Incremental pipeline

Dispatch enters this pipeline only with a valid compatible full baseline and an
automatically written intent. Every selection must be recorded in
`fm_agent_skill/control/incremental_decision.json` with a reason: `diff`, `intent`,
`caller-propagation`, `callee-propagation`, or `spec-change`.

1. `validate_baseline`: confirm the baseline Git ref, index, specs, and verification artifacts.
2. `refresh_plan`: dispatch `fm-agent-skill:fm-phase-plan-worker` (and `fm-agent-skill:fm-domain-context-worker` when context changes) for the chosen scope, then run `executor.py normalize-phases --project "$PROJECT"` before its gate.
3. `preserve_specs`: run `executor.py preserve-specs --project "$PROJECT"` before re-extraction to snapshot compatible paired sidecars in Skill control state.
4. `diff`: when CodeGraph is selected, export first and run `executor.py extract --project "$PROJECT" --codegraph-export "$PROJECT/fm_agent_skill/control/codegraph_export.json"`; otherwise omit that option. Then run `executor.py diff --project "$PROJECT"`. It compares the baseline commit with the current snapshot commit through Git: any changed file includes every current function from that file, even when a function body is unchanged. It removes verification results for that whole conservative range and for removed functions; only unaffected files retain their sidecars and results.
5. `rebuild_graph`: when CodeGraph is selected, reuse `codegraph.py export --output "$PROJECT/fm_agent_skill/control/codegraph_export.json"` from extraction (or create it immediately before extraction) and run `executor.py graph --project "$PROJECT" --codegraph-export "$PROJECT/fm_agent_skill/control/codegraph_export.json"`. Otherwise dispatch `fm-agent-skill:fm-agent-static-edge-worker`, promote its candidate with `executor.py record-agent-edges`, then run `executor.py graph`; its validated edges seed automatic caller/callee propagation.
6. `select_scope`: run `executor.py select --project "$PROJECT"` for the deterministic changed-function seed. Then use `fm-agent-skill:fm-select-relevant-modules-worker` and `fm-agent-skill:fm-select-relevant-files-worker` to add caller/callee propagation. After validating each file-selector record, merge it with `incremental.py merge-selection --record <record> --reason caller-propagation` (or its actual propagation reason); the control decision records why every indexed function is included or excluded.
7. `update_specs`: run `executor.py restore-specs --project "$PROJECT"` to recover only unchanged paired sidecars. Dispatch read-only `fm-agent-skill:fm-incremental-spec-plan-worker` jobs for selected independent functions; persist each accepted response and call `incremental.py apply-plan --plan <plan>` serially. Then dispatch one `fm-agent-skill:fm-reconcile-caller-info-worker` per caller frontier. The apply tool writes native `fm_agent/incremental_updated_specs.json`.
8. `verify_affected`: dispatch `fm-agent-skill:fm-verify-function-worker` jobs for selected functions. Each job repeats the structured actual-postcondition A versus specification B check for the current snapshot; use `DEPENDENCY_RISK` rather than converting a callee's direct mismatch into a caller mismatch. Retained schema-v1 or otherwise malformed verification results invalidate the baseline instead of becoming Bug Validator candidates.
9. `bug_validation`: dispatch `fm-agent-skill:fm-bug-validate-worker` only for selected direct `MISMATCH` candidates. The worker first designs a public-entrypoint probe; the Coordinator records optional build evidence with `probe_runner.py`, executes the reviewed probe with `reproduction_runner.py`, then asks the worker to finalize its result. Only the latter dynamic result can confirm or reject a candidate. Overwrite the current `fm_agent/bug_validation/summary.json`; incremental cleanup removes older reports and probes before this phase.
10. `finalize`: gate all retained and selected artifacts, then save the new baseline.

Deleted functions must be absent from the Skill control analysis index, extracted artifacts,
required result mapping, and retained-spec mapping. A stale or malformed
artifact makes the gate fail rather than being treated as valid reuse.

Read [agent-orchestration.md](agent-orchestration.md) for the host-agent and
CodeGraph backend model.

## Resume

Read [resume-contract.md](resume-contract.md) for an explicit resume. Re-enter
only the first incomplete incremental phase. Do not recalculate a completed
diff, selection decision, or preserved-spec snapshot unless its normal gate no
longer validates, in which case resume must fail rather than silently widening
scope. Reuse only function artifacts whose ids belong to the interrupted
snapshot commit.
