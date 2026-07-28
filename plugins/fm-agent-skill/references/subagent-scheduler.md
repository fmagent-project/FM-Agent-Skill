# Host subagent scheduler

This scheduler maps each original FM-Agent semantic LLM/OpenCode worker to one
named worker invoked by the active host (Claude Code or Codex). It never starts
or imports the original FM-Agent. The Coordinator alone owns `fm_agent_skill/`
control state, phase transitions, lock heartbeats, and user-visible status.

## Job lifecycle

For each semantic job, the Coordinator calls `scheduler.py create`, then
`scheduler.py start`, invokes the mapped host subagent, validates its concise
report, and calls `scheduler.py complete`. Workers may write only their assigned
sidecars, result JSON, or probe output; they never write manifests or control
state.

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
Concurrent jobs cannot share an output path.

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

Use configured `concurrency` as the maximum active host subagents (default 10).
Run phases serially; run independent batches in a caller-first layer in
parallel. Verification can start as soon as its own sidecars validate. Join all
jobs required by a phase and pass its gate before advancing. Each host-specific
adapter supplies the actual subagent call; the manifest, boundaries, retries,
and artifact contract are identical in Claude Code and Codex.
