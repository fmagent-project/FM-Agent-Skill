# Host subagent scheduler

This scheduler maps each original FM-Agent semantic LLM/OpenCode worker to one
named worker invoked by the active host (Claude Code or Codex). It never starts
or imports the original FM-Agent. The Coordinator alone owns `fm_agent_skill/`
control state, phase transitions, lock heartbeats, and user-visible status.

## Job lifecycle

For each semantic job, the Coordinator calls `scheduler.py create` with its
pipeline `phase`, then obtains `scheduler.py admissible`. For each returned
job, call `scheduler.py start` **before** invoking the host subagent. `start`
is the admission point: it atomically rejects a job when its global or
type-specific capacity is full. Never launch a worker merely because it was
previously listed as ready.

The worker writes detailed evidence only to its assigned artifact or, for an
incremental plan, to `fm_agent_skill/worker_reports/<job-id>.json`. Its final
response is a JSON receipt of at most 4 KiB with `job_id`, `status`, output
paths, counts, an optional verdict, and at most a one-sentence summary. The
Coordinator passes that receipt to `scheduler.py complete`; it does not paste
worker reasoning or source excerpts into its own context. Workers never write
job manifests, phase receipts, locks, or other control state.

Retain the same job id for retries. `execution`, `output`, and `interrupted`
failures use `scheduler.py fail`; if retryable, call `scheduler.py retry` after
10 seconds, up to configured `retries`. For a specification layer, retry
remaining batches immediately when any paired sidecar became valid; otherwise
wait 10 seconds. `input`, `semantic`, and `cancelled` are terminal. A semantic
verification error is a valid `ERROR` result, not a retry. Bug Validator has
one total attempt by default. On resume, call `scheduler.py recover` before
`scheduler.py ready`; valid stale outputs finish their job, invalid stale jobs
requeue in place when attempts remain.

Job files live temporarily at `fm_agent_skill/jobs/<job-id>.json`. They are
removed after a successful analysis and retained only to resume a failed one.
Concurrent jobs cannot share an output path. After joining a phase, the
Coordinator runs `scheduler.py phase-receipt --phase <phase>`; it writes the
small aggregate record at `fm_agent_skill/control/phase_receipts/<phase>.json`.
The Coordinator reads that receipt, then reads detailed artifacts only for an
escalation (`MISMATCH`, `DEPENDENCY_RISK`, `INCONCLUSIVE`, `ERROR`, or worker
failure) before its normal gate.

## Worker mapping

| Original FM-Agent semantic worker | Skill worker | Dispatch rule |
| --- | --- | --- |
| phase generation | `fm-phase-plan-worker` | one job, then gate |
| phase post-processing | `fm-phase-refine-worker` | only when needed |
| domain context generation | `fm-domain-context-worker` | after valid phases |
| static edge resolution without CodeGraph | `fm-agent-static-edge-worker` | one bounded candidate, then deterministic validation |
| specification batch generation | `fm-spec-batch-worker` | same layer batches in parallel |
| function Hoare reasoner | `fm-verify-function-worker` | one ready function per job |
| Bug Validator | `fm-bug-validate-worker` | one direct `MISMATCH` per job |
| incremental module selection | `fm-select-relevant-modules-worker` | one job |
| incremental file selection | `fm-select-relevant-files-worker` | after module selection |
| incremental spec update planning | `fm-incremental-spec-plan-worker` | parallel read-only plans |
| caller reconciliation | `fm-reconcile-caller-info-worker` | one caller per job |

## Concurrency

The default bounded profile is deliberately conservative for a context-limited
Coordinator:

| Work type | Maximum active jobs |
| --- | ---: |
| all host subagents | 10 |
| `spec_batch` | 4 |
| `verify_function` | 8 |
| `bug_validate` | 1 |
| `incremental_spec_plan` | 2 |
| phase/refine/domain/edge/select/reconcile workers | 1 each |

The corresponding configuration keys are `max_active_subagents`,
`spec_concurrency`, `verify_concurrency`, `bug_validation_concurrency`, and
`read_only_plan_concurrency`. They are operational limits, not semantic inputs:
changing them does not invalidate an otherwise valid Git baseline. Use
`scheduler.py capacity` to inspect current leases. Phases remain serial;
independent caller-first-layer work may be parallel, verification may start as
soon as its sidecars validate, and every phase must join and emit its receipt
before its gate. The active host supplies the actual subagent call; the Skill
enforces admission and artifact contracts on both Claude Code and Codex.
