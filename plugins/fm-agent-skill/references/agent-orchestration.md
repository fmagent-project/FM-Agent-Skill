# Agent orchestration model

Semantic execution is currently **Claude Code only**.  It requires Claude's
`Agent` tool and this plugin's custom workers in `agents/`.  Codex may install
and inspect the shared deterministic artifacts, but must not claim that it ran
the semantic pipeline until a Codex executor is implemented.

The Coordinator is the plugin equivalent of FM-Agent's original `main.py`.
It owns `fm_agent_skill/` state, lock heartbeats, pipeline transitions, retry
decisions, and user-visible status.  It uses deterministic scripts for
extraction, index/graph construction, diffs, cleanup, gates, and finalization.
Every original FM-Agent LLM/OpenCode worker is instead dispatched as its named
Claude subagent according to [subagent-scheduler.md](subagent-scheduler.md).
No worker may spawn another worker or write control state.

For every selected phase, call `pipeline.py phase-start`, create/start/join
the required worker jobs, validate them through `scheduler.py complete`, and
only then call `pipeline.py phase-complete`. Finish with `pipeline.py complete`;
on failure use `pipeline.py fail`. The deterministic scripts own dispatch,
locking, fingerprints, job validation, and artifact gates; they do not replace
semantic analysis.

For an explicit resume, first use `orchestrate.py resume-inspect` and then
`orchestrate.py resume`; use its existing run id and first incomplete phase.
Do not dispatch a new run or repeat earlier successful phases. Re-open the
job manifests for that phase, retain only validated succeeded work, and use
`scheduler.py ready` to continue independent pending jobs. Read
[resume-contract.md](resume-contract.md) before re-entering an interrupted
phase.

## Optional CodeGraph backend

Before dispatch, run `codegraph.py status`. If CodeGraph is available, use it
automatically: every full or incremental run removes and regenerates the
generated `<project>/.codegraph/` index. If unavailable, do not install
software; use the static fallback.

When available, dispatch with `--codegraph`, then rebuild through
`codegraph.py init --rebuild` while the analysis lock is held. Read it with
`codegraph.py export`, map function/method nodes and `calls`/`instantiates`
edges to normalized IDs, and record `backend: "codegraph"` and the index path
in `fm_agent_skill/control/call_graph_precision.json`.

If unavailable, continue with Coordinator static analysis and record
`backend: "agent-static"`, `precision: "best-effort"`, and the reason. Never
label a fallback graph as exact. Erlang/ELP is outside the current scope.
