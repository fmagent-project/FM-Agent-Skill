---
name: run
description: Run or safely resume an FM-Agent full, incremental, or no-op correctness analysis for a Git project. Use when the user asks to analyze code, continue an interrupted FM-Agent run, generate behavioral specifications, verify implementation against specifications, or assess changes for formal-methods bugs.
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

Codex selects this skill from a natural-language request; this skill directory
does not create a Codex slash command. When a client exposes a command entry,
it may pass the following arguments to the same workflow:

```text
[natural-language change note]
  [--submodule path ...] [--one-phase]
  [--extra-edge file-or-dir] [--knowledge file ...] [--isolate] [--resume]
```

Treat all text not matching an option as the change note. Resolve paths relative
to the target repository. Do not invent `$PROJECT`, `$RUN_ID`, or a scope: the
caller supplies the project and `orchestrate.py dispatch` returns the run id,
merged configuration, and mode as JSON.

## Resume, dispatch, and cleanup

Parse the supplied argument list before dispatching.  Do **not** pass
`$ARGUMENTS` verbatim as `--note`: split these public options from the
natural-language change note:

- `--submodule PATH` (repeatable)
- `--knowledge FILE` (repeatable)
- `--extra-edge FILE_OR_DIR`
- `--one-phase`
- `--isolate`
- `--resume`

All remaining text is the change note. Preserve every option value and pass
each option separately. First perform this read-only inspection. It never
acquires a lock, writes state, or rebuilds CodeGraph:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/orchestrate.py" inspect \
  --project "$PROJECT" \
  [--submodule "$PATH"]... [--knowledge "$FILE"]... \
  [--extra-edge "$FILE_OR_DIR"] [--one-phase] [--isolate]
```

### Explicit resume

`--resume` means continue the newest interrupted `full` or `incremental` run;
it never creates a new run id and never changes its saved scope or
configuration. It is mutually exclusive with `--submodule`, `--knowledge`,
`--extra-edge`, `--one-phase`, `--isolate`, and a new change note. Read
[resume-contract.md](../../references/resume-contract.md), then inspect it
before any ordinary mode selection or CodeGraph action:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/orchestrate.py" resume-inspect \
  --project "$PROJECT"
```

If this returns an error, report its reason and do not start a fresh analysis.
It rejects changed source content, changed auxiliary inputs, an already
completed run, and legacy runs without a start snapshot. If the interrupted
run's lock has a fresh heartbeat, report that another agent may still be
working. Ask the user whether to take over; only after an explicit affirmative
reply may the agent append `--take-over` below.

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/orchestrate.py" resume \
  --project "$PROJECT" [--take-over]
```

Retain the returned `run_id`, `config`, and `resume_from_phase`. Execute only
that run's first incomplete phase and later phases. Do not call ordinary
`inspect` or `dispatch`, do not call `pipeline.py prepare`, and do not run a
full cleanup when a previous `phase_cleanup` succeeded. Revalidate every
function-level artifact in a resumed specification, verification, or bug
validation phase; retain only hash-compatible valid artifacts and produce only
the missing or invalid ones. Refresh the lock heartbeat before and after each
phase through the existing `pipeline.py` transitions.

Keep the original call-graph backend for the resumed run. If the first
incomplete phase is `call_graph` or `rebuild_graph` and its saved backend is
CodeGraph, check the existing index; reuse it when readable and rebuild only
when missing or invalid. If an already-completed graph phase is valid, do not
touch `.codegraph/`. Never silently replace a CodeGraph-selected resumed run
with `agent-static`.

When `--resume` is absent, use the ordinary workflow below.

If inspection returns `noop` and `refresh_observed_commit` is false, report its
baseline commit and finish. Do not run `codegraph.py status`. If
`refresh_observed_commit` is true, run the stateful
`dispatch` command below **without** `--codegraph`; it writes the no-op record
and refreshes only Git provenance, then finish.

Only when inspection returns `full` or `incremental`, inspect CodeGraph:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/codegraph.py" status --project "$PROJECT"
```

If it is available, include the internal `--codegraph` option below and rebuild
`$PROJECT/.codegraph/` automatically. Proceed directly. If it is
not available, do not install software; dispatch without `--codegraph`, use
`agent-static`, and record the fallback reason. A selected CodeGraph rebuild
failure fails the run; do not silently change backend.

After determining availability, run exactly one stateful dispatch command:

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
