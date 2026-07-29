---
name: run
description: Run or safely resume an FM-Agent full, incremental, or no-op correctness analysis for a Git project. Use when the user asks to analyze code, continue an interrupted FM-Agent run, generate behavioral specifications, verify implementation against specifications, or assess changes for formal-methods bugs.
---

# FM-Agent analysis

This is the sole public analysis entry point. Execute it directly in Claude
Code or Codex using that host's worker/subagent capability. Never launch,
import, or shell out to the original FM-Agent project or its remote-LLM
pipeline. The bundled deterministic executor and the host Coordinator together
are this Skill's implementation.

The Coordinator is the Skill's equivalent of FM-Agent's original `main.py`.
It coordinates local deterministic tools and dispatches the named workers using
the current host's subagent mechanism; it never performs a worker's semantic
task inline. Read
[subagent-scheduler.md](../../references/subagent-scheduler.md) in addition to
[agent-orchestration.md](../../references/agent-orchestration.md) before
starting.

Before invoking a script, read [runtime-path.md](../../references/runtime-path.md)
and resolve `FM_AGENT_SKILL_ROOT`; never use `CLAUDE_SKILL_DIR`.

Read [progress-reporting.md](../../references/progress-reporting.md) before any
stateful action. User-visible phase progress is mandatory; do not rely on a
client to infer it from tool output.

## Output ownership

Write FM-Agent-compatible analysis artifacts only below `fm_agent/` and mutable
Skill control state only below `fm_agent_skill/`. Do not derive a target-project
directory name from the Skill's installation or marketplace packaging. Before
dispatching a worker, give it only the output paths listed in its job manifest.

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
to the target repository. Do not invent `$PROJECT` or a scope: the caller
supplies the project and `orchestrate.py dispatch` returns the current analysis
state, merged configuration, and mode as JSON.

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
<python3> "$FM_AGENT_SKILL_ROOT/scripts/orchestrate.py" inspect \
  --project "$PROJECT" \
  [--submodule "$PATH"]... [--knowledge "$FILE"]... \
  [--extra-edge "$FILE_OR_DIR"] [--one-phase] [--isolate]
```

### Explicit resume

`--resume` means continue the interrupted `full` or `incremental` analysis in
`fm_agent_skill/active.json`; it never changes its saved scope or
configuration. It is mutually exclusive with `--submodule`, `--knowledge`,
`--extra-edge`, `--one-phase`, `--isolate`, and a new change note. Read
[resume-contract.md](../../references/resume-contract.md), then inspect it
before any ordinary mode selection or CodeGraph action:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/orchestrate.py" resume-inspect \
  --project "$PROJECT"
```

If this returns an error, report its reason and do not start a fresh analysis.
It rejects changed source content, changed auxiliary inputs, an already
completed run, and legacy runs without a start snapshot. If the interrupted
run's lock has a fresh heartbeat, report that another agent may still be
working. Ask the user whether to take over; only after an explicit affirmative
reply may the agent append `--take-over` below.

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/orchestrate.py" resume \
  --project "$PROJECT" [--take-over]
```

Retain the returned `config` and `resume_from_phase`. Execute only the current
analysis's first incomplete phase and later phases. Do not call ordinary
`inspect` or `dispatch`, do not call `pipeline.py prepare`, and do not run a
full cleanup when a previous `phase_cleanup` succeeded. Revalidate every
function-level artifact in a resumed specification, verification, or bug
validation phase; retain only hash-compatible valid artifacts and produce only
the missing or invalid ones. Refresh the lock heartbeat before and after each
phase through the existing `pipeline.py` transitions.

Before beginning the resumed phase, emit the required `Resuming` status from
[progress-reporting.md](../../references/progress-reporting.md). It is also the
user-visible announcement for that phase; announce every later phase normally.

Keep the original call-graph backend for the resumed run. If the first
incomplete phase is `call_graph` or `rebuild_graph` and its saved backend is
CodeGraph, check the existing index; reuse it when readable and rebuild only
when missing or invalid. If an already-completed graph phase is valid, do not
touch `.codegraph/`. Never silently replace a CodeGraph-selected resumed run
with `agent-static`.

When `--resume` is absent, use the ordinary workflow below.

If inspection returns `noop` and `refresh_observed_commit` is false, report a
user-visible no-op status and its baseline commit, then finish. Do not run
`codegraph.py status`. If
`refresh_observed_commit` is true, run the stateful
`dispatch` command below **without** `--codegraph`; it writes the no-op record
and refreshes only Git provenance, then finish.

Only when inspection returns `full` or `incremental`, inspect CodeGraph:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/codegraph.py" status --project "$PROJECT"
```

If it is available, include the internal `--codegraph` option below and rebuild
`$PROJECT/.codegraph/` automatically. Proceed directly. If it is
not available, do not install software; dispatch without `--codegraph`, use
`agent-static`, and record the fallback reason. A selected CodeGraph rebuild
failure fails the run; do not silently change backend.

After determining availability, run exactly one stateful dispatch command:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/orchestrate.py" dispatch \
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

`dispatch` should return the inspected non-noop mode. Retain its current-analysis state, emit
the required `Started` status from
[progress-reporting.md](../../references/progress-reporting.md), and execute only the selected
pipeline. If `--codegraph` was selected, rebuild its generated index while the
run lock is held before extraction or graph construction:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/codegraph.py" init --rebuild --project "$PROJECT"
```

When `--isolate` is selected, replace `$PROJECT` for every subsequent command
with the `project` path returned by `dispatch`. It is a temporary Git worktree
containing the exact source snapshot and current artifacts. `pipeline.py
complete` copies `fm_agent/` and `fm_agent_skill/` back to the original project
and removes the temporary worktree. `pipeline.py fail` deliberately retains the
snapshot and marker for resume. Invoke `--resume` against the original project;
the marker redirects it to the retained snapshot.

During graph construction, export its normalized function and edge data into
Skill control state, then supply that file to `executor.py graph`:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/codegraph.py" export --project "$PROJECT" \
  --output "$PROJECT/fm_agent_skill/control/codegraph_export.json"
```

Before each phase emit the required `Stage current/total` status, then call
`pipeline.py phase-start`. For every semantic unit, create its job manifest
with `scheduler.py create`, call `scheduler.py start`, launch exactly the
mapped named worker with the host subagent mechanism, then call `scheduler.py complete`
only after its outputs validate. Start at most configured `concurrency`
background workers, join the jobs required by this phase, then call
`pipeline.py phase-complete` and emit the short completion status. A failed
gate means do not enter the next phase. On every exception, tool failure, or user-requested stop, run
`pipeline.py fail`; it releases its owned lock while preserving artifacts and
the active analysis state. `pipeline.py complete` likewise releases its owned lock.
Report the last completed phase and the phase that did not finish.

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

Use the worker names exactly as follows: `fm-phase-plan-worker`, optional
`fm-phase-refine-worker`, `fm-domain-context-worker`, `fm-spec-batch-worker`,
`fm-agent-static-edge-worker`, `fm-verify-function-worker`, `fm-bug-validate-worker`,
`fm-select-relevant-modules-worker`, `fm-select-relevant-files-worker`,
`fm-incremental-spec-plan-worker`, and `fm-reconcile-caller-info-worker`.
Pass each worker the project path, job id, exact inputs, assigned
outputs, and required reference files. A worker must return its concise JSON
summary; workers cannot spawn other workers. The Coordinator is the only
writer of `fm_agent_skill/jobs/` and all other control state.

For each specification job, provide only the assigned extracted artifacts and
permitted header, domain, and caller evidence. Do not read test files. Require
the worker to emit evidence and confidence in every spec sidecar; never
schedule a `MATCH` from a low-confidence or implementation-derived contract.

For incremental planning, `fm-incremental-spec-plan-worker` returns an update
plan in its response and writes no files. Record that response through
`scheduler.py complete --result-json`, validate it, then serially apply sidecar
updates before scheduling caller reconciliation. Do not let two active workers
write the same artifact or report path.

After each selector record is validated, merge it only through:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/incremental.py" merge-selection \
  --project "$PROJECT" --record "$SELECTOR_OUTPUT" --reason caller-propagation
```

Use `callee-propagation` or `spec-change` when that is the actual reason. Save
an accepted incremental-plan response as JSON, then apply it only through:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/incremental.py" apply-plan \
  --project "$PROJECT" --plan "$PLAN_JSON"
```

When an Agent call fails, classify it before scheduling anything downstream.
`execution` (timeout, rate limit, tool crash), `output` (missing or invalid
artifact), and `interrupted` are retryable. Call `scheduler.py fail` with that
class; if its status is `retryable`, wait 10 seconds and call
`scheduler.py retry`, retaining the same job id and stopping at configured
`retries`. For a spec layer with any newly valid sidecar, retry remaining
batches immediately; wait 10 seconds only when it made no progress. A retried
spec batch preserves valid paired sidecars and repairs only incomplete assigned
artifacts. A verification-level failure is a valid `ERROR` result, not a retry;
retry only when the Agent or result artifact itself failed. Bug Validator jobs
have one total attempt by default. `input`, `semantic`, and `cancelled`
failures are terminal: leave dependents unscheduled and call `pipeline.py fail`.
On resume call `scheduler.py recover` before `scheduler.py ready`; it retains a
stale running job only if current outputs validate, otherwise requeues it in
place when attempts remain.

During `call_graph` or `rebuild_graph`, write one native top-down layer artifact
for each phase in `fm_agent/phases.json`: `phase_01_topdown_layers.json`, then
`phase_02_topdown_layers.json`, and so on. A layer entry must retain its
original repository-relative `source_file`, so it can be checked against its
phase. Do not merge phases unless `--one-phase` was selected. For CodeGraph,
call `executor.py graph` with the exported control file; it records `exact`
only after mapping exported nodes and edges to current extracted artifacts.
Without that file, dispatch `fm-agent-static-edge-worker` first. It writes a
candidate below `fm_agent/`; validate and promote it, then rerun graph:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/executor.py" record-agent-edges \
  --project "$PROJECT" --edges-file "$PROJECT/fm_agent/agent_static_edges_candidate.json"
<python3> "$FM_AGENT_SKILL_ROOT/scripts/executor.py" graph --project "$PROJECT"
```

This records `agent-static/best-effort` and makes only validated edges available
to layer construction and incremental propagation.

Immediately after `fm-phase-plan-worker` returns, normalize and validate its
output before completing `project_understanding` or `refresh_plan`:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/executor.py" normalize-phases \
  --project "$PROJECT"
```

The normalized file must use `modules[].source_files` and `depends_on_phases`.
Test paths are excluded from every analysis stage, and no pseudo-function is
created for a declaration-only header.

Build the Skill control index after extraction:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/executor.py" extract --project "$PROJECT"
```

Build native phase-layer artifacts after a valid `phases.json`:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/executor.py" graph --project "$PROJECT" \
  [--codegraph-export "$PROJECT/fm_agent_skill/control/codegraph_export.json"]
```

Write `MISMATCH` only for a function's own specification violation. If the
function is affected solely by a mismatching callee, write `DEPENDENCY_RISK`
with the affected callee IDs and do not send it to Bug Validator. For each
direct CMake candidate, first run:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/probe_build.py" \
  --project "$PROJECT" --bug-id "$BUG_ID"
```

Use the resulting isolated build directory for the probe. Do not reuse a
project `build/` directory or its `CMakeCache.txt`.

Only after every phase gate succeeds may the agent call `pipeline.py complete`
and release the lock as `idle`. Never modify business source or extracted
function copies; write specifications only to their `.spec.json` and
`.info.json` sidecars. Do not expose raw full diffs in chat. Execute and describe only
capabilities documented by this Skill's shared instructions and references; do not
infer features from the original FM-Agent project.
