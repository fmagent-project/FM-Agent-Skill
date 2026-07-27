# Claude subagent scheduler

This is the Claude-first executor for semantic FM-Agent work.  It maps each
original FM-Agent OpenCode/LLM worker to one Claude custom subagent.  The
Coordinator remains the sole owner of `fm_agent_skill/` control state, locks,
run records, and phase transitions.

## Job lifecycle

For every semantic job, the Coordinator creates a manifest with
`scheduler.py create`, then calls `scheduler.py start` immediately before
launching its named worker through Claude's `Agent` tool. On return, it checks
the worker's concise report, then calls `scheduler.py complete`. `complete`
checks required outputs and the type-specific sidecar/context contract before
recording success; invalid or missing outputs become `retryable` automatically.

Use the **same job id** for every attempt. A timeout, rate limit, or tool crash
uses `scheduler.py fail --failure-class execution`; while attempts remain below
configured `retries`, call `scheduler.py retry` after the retry delay. Phase
planning and domain context use 10 seconds. A spec layer with newly valid
sidecars retries its remaining batches immediately; a layer with no new valid
sidecar waits 10 seconds, matching FM-Agent. `output`
and `interrupted` failures follow the same path. `input`, `semantic`, and
`cancelled` failures are terminal and fail the phase. A retried spec worker
keeps valid sidecar pairs and repairs only missing or invalid pairs. This
matches FM-Agent's repeated OpenCode calls and partial-progress preservation,
without creating a new logical worker or rewiring dependencies.

For verification, an analysis-level problem must be written as a valid `ERROR`
result and completed; it is not a scheduler retry. Only loss of the Agent call
or failure to write/validate its assigned result is retryable. Bug Validator
jobs default to one total attempt, matching FM-Agent's
`bug_validation_max_retries = 1`.

The current job files live at `fm_agent_skill/jobs/<job-id>.json`. Workers must
never write there. On resume call `scheduler.py recover` before `scheduler.py ready`. A
stale `running` job with valid current outputs becomes `succeeded`; otherwise
it becomes `retryable` when attempts remain. Requeue it in place. A final
`failed` job blocks dependents and makes the Coordinator fail the phase, while
valid completed independent work remains reusable.

`required_outputs` are project-relative and may be only under `fm_agent/` or
`fm_agent_skill/probes/`. A worker receives only its assigned output
paths.  Concurrent jobs may not share one output path; the Coordinator first
partitions work by phase/layer/function/caller.

## One-to-one worker mapping

| Original FM-Agent semantic worker | Claude worker | Dispatch rule |
| --- | --- | --- |
| phase generation | `fm-phase-plan-worker` | one job, then phase gate |
| phase post-processing | `fm-phase-refine-worker` | only when cleanup needs semantic refinement |
| domain context generation | `fm-domain-context-worker` | one job after phases are valid |
| specification batch generation | `fm-spec-batch-worker` | same phase/layer jobs can run concurrently; batch size defaults to 2 |
| function Hoare reasoner | `fm-verify-function-worker` | one independent function per job; dispatch when that function's sidecars are ready |
| Bug Validator | `fm-bug-validate-worker` | one direct `MISMATCH` per job |
| incremental relevant-module selector | `fm-select-relevant-modules-worker` | one selection job |
| incremental relevant-file selector | `fm-select-relevant-files-worker` | one selection job, after module selection |
| incremental specification update planner | `fm-incremental-spec-plan-worker` | concurrent read-only plans; Coordinator serially applies them |
| caller reconciliation | `fm-reconcile-caller-info-worker` | one caller per job; caller-before-callee frontier rounds |

## Concurrency and joins

Use the saved `concurrency` value as the maximum number of active background
Agent calls.  Process phases serially.  Within a top-down layer, start up to
that limit of independent specification batches.  As soon as a batch completes
and its sidecars validate, verification jobs for its functions may start; do
not wait for unrelated batches.  Join all required jobs, then run the phase
gate before advancing.  Bug Validator jobs may run concurrently after their
individual direct mismatches appear.

Claude subagents have independent context and cannot spawn another subagent.
Therefore every prompt must name the project, run id, assigned inputs, exact
allowed outputs, schema reference, and the instruction to return a short
machine-readable summary.  The Coordinator, not a worker, owns retries,
dependency checks, state transitions, and user-visible progress.
