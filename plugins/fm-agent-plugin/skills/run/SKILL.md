---
name: run
description: Run an FM-Agent full, incremental, or no-op correctness analysis for a Git project. Use when the user asks to analyze code, generate behavioral specifications, verify implementation against specifications, or assess changes for formal-methods bugs.
---

# FM-Agent analysis

This is the sole public analysis entry point. The host coding agent is the
plugin's equivalent of FM-Agent's original `main.py`: it coordinates local
tools, reads the repository, writes specifications, and performs reasoning.
Deterministic scripts own dispatch, locking, state transitions, and artifact
validation. Never launch the original FM-Agent remote-LLM pipeline as a
substitute for this workflow.

Before invoking a script, read [runtime-path.md](../../references/runtime-path.md)
and resolve `FM_AGENT_PLUGIN_ROOT`. This shared skill must work in both Codex
and Claude Code; never use `CLAUDE_SKILL_DIR`.

Read [agent-orchestration.md](../../references/agent-orchestration.md) before
starting the selected pipeline.

## Public parameters

```text
/fm-agent:run [natural-language change note]
  [--submodule path ...] [--one-phase]
  [--extra-edge file-or-dir] [--knowledge file ...] [--isolate]
```

Treat all text not matching an option as the change note. Resolve paths relative
to the target repository. Do not invent `$PROJECT`, `$RUN_ID`, or a scope: the
caller supplies the project and `orchestrate.py dispatch` returns the run id,
merged configuration, and mode as JSON.

## Dispatch and cleanup

Parse the supplied argument list before dispatching.  Do **not** pass
`$ARGUMENTS` verbatim as `--note`: split these public options from the
natural-language change note:

- `--submodule PATH` (repeatable)
- `--knowledge FILE` (repeatable)
- `--extra-edge FILE_OR_DIR`
- `--one-phase`
- `--isolate`

All remaining text is the change note. Preserve every option value and pass
each option separately. First perform this read-only inspection. It never
acquires a lock, writes state, rebuilds CodeGraph, or needs authorization:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/orchestrate.py" inspect \
  --project "$PROJECT" \
  [--submodule "$PATH"]... [--knowledge "$FILE"]... \
  [--extra-edge "$FILE_OR_DIR"] [--one-phase] [--isolate]
```

If inspection returns `noop` and `refresh_observed_commit` is false, report its
baseline commit and finish. Do not run `codegraph.py status` and do not ask for
CodeGraph permission. If `refresh_observed_commit` is true, run the stateful
`dispatch` command below **without** `--codegraph`; it writes the no-op record
and refreshes only Git provenance, then finish.

Only when inspection returns `full` or `incremental`, inspect CodeGraph:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/codegraph.py" status --project "$PROJECT"
```

If it is available, ask permission to use it, explaining that this required
analysis run removes and regenerates `$PROJECT/.codegraph/`. If it is not
available, ask whether installation is authorized. Do not install software
without explicit approval and do not consider Erlang/ELP. On approval, include
the internal `--codegraph` option below. On decline, dispatch without it, use
`agent-static`, and record the fallback reason. A selected CodeGraph rebuild
failure fails the run; do not silently change backend.

After that decision, run exactly one stateful dispatch command:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/orchestrate.py" dispatch \
  --project "$PROJECT" \
  --note "$CHANGE_NOTE" \
  [--submodule "$PATH"]... \
  [--knowledge "$FILE"]... \
  [--extra-edge "$FILE_OR_DIR"] [--one-phase] [--isolate] [--codegraph]
```

The bracketed terms are placeholders, not literal shell text: omit an option
when absent.  For example, `review checkout changes --submodule backend
--knowledge payments.md` must dispatch with note `review checkout changes`,
`--submodule backend`, and `--knowledge payments.md` as distinct arguments.

`dispatch` should return the inspected non-noop mode. Retain its `run_id` and
execute only the selected
pipeline. If `--codegraph` was selected, rebuild its generated index while the
run lock is held before extraction or graph construction:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/codegraph.py" init --rebuild --project "$PROJECT"
```

During graph construction, read its normalized function and edge data with:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/codegraph.py" export --project "$PROJECT"
```

Before each phase call `pipeline.py phase-start`; after producing its artifacts
call `pipeline.py phase-complete`. A failed gate means do not enter the next
phase. On every exception, tool failure, or user-requested stop, run
`pipeline.py fail`; it releases its owned lock while preserving artifacts and
the final run record. `pipeline.py complete` likewise releases its owned lock.
Report the last completed phase.

When `pipeline.py phase-start` begins `phase_cleanup` in a `full` run, it
automatically clears only old derived artifacts; it never removes business
source or the current `fm_agent/phases.json`. Do not bypass this phase.

## Full and incremental execution

Read [stage-gates.md](../../references/stage-gates.md) before starting. Then use
[full-pipeline.md](../../references/full-pipeline.md) or
[incremental-pipeline.md](../../references/incremental-pipeline.md) according
to dispatch. Use [artifact-contract.md](../../references/artifact-contract.md)
whenever writing an artifact.

For specification work, read [specification-rules.md](../../references/specification-rules.md).
For verification, read [hoare-reasoning.md](../../references/hoare-reasoning.md).
For every `MISMATCH`, read [bug-validation.md](../../references/bug-validation.md).

During `call_graph` or `rebuild_graph`, write one native top-down layer artifact
for each phase in `fm_agent/phases.json`: `phase_01_topdown_layers.json`, then
`phase_02_topdown_layers.json`, and so on. A layer entry must retain its
original repository-relative `source_file`, so it can be checked against its
phase. Do not merge phases unless `--one-phase` was selected. Then write the
plugin-control precision record. For CodeGraph use:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/call_graph.py" record-precision \
  --project "$PROJECT" --backend codegraph --precision exact \
  --codegraph-index "$PROJECT/.codegraph/codegraph.db"
```

For agent-static analysis, use `--backend agent-static --precision best-effort`
and state the fallback reason with `--reason`.

Build the plugin control index after extraction:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/artifact_index.py" build --project "$PROJECT"
```

Write `MISMATCH` only for a function's own specification violation. If the
function is affected solely by a mismatching callee, write `DEPENDENCY_RISK`
with the affected callee IDs and do not send it to Bug Validator. For each
direct CMake candidate, first run:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/probe_build.py" \
  --project "$PROJECT" --run-id "$RUN_ID" --bug-id "$BUG_ID"
```

Use the resulting isolated build directory for the probe. Do not reuse a
project `build/` directory or its `CMakeCache.txt`.

Only after every phase gate succeeds may the agent call `pipeline.py complete`
and release the lock as `idle`. Never modify business source with `[SPEC]` or
`[INFO]` or expose raw full diffs in chat. Execute and describe only
capabilities documented by this plugin's shared skills and references; do not
infer features from the original FM-Agent project.
