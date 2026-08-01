# Agent orchestration model

The Skill runs directly in Claude Code or Codex. The host Coordinator uses that
host's subagent capability and the shared job contract; it never invokes,
imports, or shells out to the original FM-Agent.

The Coordinator is the Skill equivalent of `main.py`: it owns the current
analysis state, lock heartbeat, deterministic scripts, phase gates, retries,
and user-visible status. Named semantic workers do only their assigned
analysis. They cannot spawn workers or write `fm_agent_skill/` control state;
the sole exception is an assigned immutable worker report under
`fm_agent_skill/worker_reports/<job-id>.json`.

This is a host-native Coordinator, not the original FM-Agent process: it must
use Codex or Claude Code's internal subagent mechanism and must never invoke
`FM-Agent/main.py`, import its pipeline, or call its remote-LLM API. For Bug
Validator jobs, `bug_validation_executor.py` supplies the deterministic next
action and invokes local safe runners in adapter mode; the host supplies its
preparation, default agent-executed execution, and finalization Worker calls.

Use only two target-project output roots: `fm_agent/` for FM-Agent-compatible
analysis artifacts and `fm_agent_skill/` for mutable Skill state. Derive no
target-project path from the Skill's installation or marketplace packaging.
Give each worker only the exact allowed output paths from its job manifest.

For every phase, call `pipeline.py phase-start`, create phase-labelled jobs,
then acquire a bounded worker slot before launching it. Specification and
verification must use `semantic_executor.py`; other jobs use
`scheduler.py admissible/start/complete`. Join required jobs and validate their
small receipts through the same owner that issued the lease. Next create
`scheduler.py phase-receipt --phase <phase>` and inspect only its counts and
escalations before calling `pipeline.py phase-complete`. On success use
`pipeline.py complete`; on a terminal failure use `pipeline.py fail`. An
explicit resume continues the single `active.json` analysis and its first
incomplete phase; it does not make a new analysis identity or repeat valid work.

Keep the Coordinator as a control plane, not a second reasoning worker. Give a
semantic worker only its immutable dispatch ticket, registered Worker
definition, assigned artifacts, and direct evidence; never give it
the whole repository or a prior worker transcript. Workers cannot spawn other
workers. Detailed worker output belongs in the assigned artifact; their final
response is a short structured receipt. Read detailed artifacts only for a
receipt escalation or a required deterministic validation.

Do not use a host Dynamic Workflow or generated JavaScript to implement a
semantic phase. It is not a registered Worker, cannot rewrite output paths, and
must not paraphrase Worker schemas. Parallelism comes only from concurrently
invoking the exact tickets returned by `semantic_executor.py dispatch`.

## Deterministic executor

Use `executor.py` for source extraction, native function inventory, layer
artifacts, preserved-sidecar snapshots, restoration, diff, and initial
selection. Without CodeGraph, dispatch `fm-agent-static-edge-worker` to write
one candidate under `fm_agent/`, then call `executor.py record-agent-edges`.
It validates artifact identities and promotes only valid edges into
`fm_agent_skill/control/agent_static_edges.json`; rerun `executor.py graph`
before specification or selection. `select` uses the resulting validated graph
for deterministic caller/callee propagation. `agent-static` is always
`best-effort`; only validated CodeGraph output may be recorded as `exact`.

## Optional CodeGraph backend

Run `codegraph.py status` before dispatch. When available, dispatch with
`--codegraph`, rebuild using `codegraph.py init --rebuild`, export normalized
functions/edges, and record `backend: codegraph` with its index path. If
unavailable, do not install it: use host semantic analysis plus the deterministic
inventory and record `backend: agent-static`, `precision: best-effort`, and a
reason. Erlang/ELP is outside the current scope.
