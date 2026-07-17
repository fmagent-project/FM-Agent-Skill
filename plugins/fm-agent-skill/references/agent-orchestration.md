# Agent orchestration model

The host coding agent is the plugin's equivalent of FM-Agent's original
`main.py`. It does not call a remote FM-Agent API or launch the original
project. It coordinates repository tools, creates required artifacts, and uses
its own reasoning capability (and, when available, subagents) for project
understanding, specification generation, verification, and bug validation.

For every selected phase, call `pipeline.py phase-start`, perform the work,
write the contract-defined artifacts, and call `pipeline.py phase-complete`.
Finish with `pipeline.py complete`; it records the baseline and releases the
owned lock. On failure use `pipeline.py fail`, which also releases its owned
lock. The deterministic scripts own dispatch, locking, state, fingerprints,
and artifact gates; they do not replace semantic analysis.

## Optional CodeGraph backend

Before dispatch, run `codegraph.py status`. If CodeGraph is available, use it
automatically: every full or incremental run removes and regenerates the
generated `<project>/.codegraph/` index. Proceed directly. If it
is unavailable, do not install software; use the static fallback.

When available, dispatch with `--codegraph`, then rebuild the
index through `codegraph.py init --rebuild` while the analysis lock is held.
Read it through `codegraph.py export`; map its function/method nodes to the
current source index by repository path and line range, and map
`calls`/`instantiates` edges to those normalized function IDs. Record
`backend: "codegraph"` and the index path in
`fm_agent_plugin/control/call_graph_precision.json`. A
rebuild failure fails this CodeGraph-selected run; do not silently substitute a
different backend.

If CodeGraph is unavailable, continue with the agent's static analysis. Record
`backend: "agent-static"`, `precision: "best-effort"`, and the reason in
`fm_agent_plugin/control/call_graph_precision.json`. Never label a fallback graph as
exact. Erlang/ELP is outside the current scope.
