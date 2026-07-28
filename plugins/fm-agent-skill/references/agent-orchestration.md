# Agent orchestration model

The Skill runs directly in Claude Code or Codex. The host Coordinator uses that
host's subagent capability and the shared job contract; it never invokes,
imports, or shells out to the original FM-Agent.

The Coordinator is the Skill equivalent of `main.py`: it owns the current
analysis state, lock heartbeat, deterministic scripts, phase gates, retries,
and user-visible status. Named semantic workers do only their assigned
analysis. They cannot spawn workers or write `fm_agent_skill/` control state.

For every phase, call `pipeline.py phase-start`, create/start/join required
jobs, validate them through `scheduler.py complete`, then call
`pipeline.py phase-complete`. On success use `pipeline.py complete`; on a
terminal failure use `pipeline.py fail`. An explicit resume continues the
single `active.json` analysis and its first incomplete phase; it does not make a
new analysis identity or repeat valid work.

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
