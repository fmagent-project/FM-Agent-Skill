# User-visible progress

Progress reporting is part of the run contract. Emit a short user-visible
status before every `pipeline.py phase-start` call and after every successful
`phase-complete` call. Do not replace this with raw script JSON or detailed
internal reasoning.

Use the user's language for surrounding text, while retaining the mode, run id,
and phase identifier. Use this form before a phase:

```text
[FM-Agent] Stage <current>/<total>: <phase label> (<phase id>)
```

On a fresh full or incremental run, first announce:

```text
[FM-Agent] Started <mode> analysis: <run id> (<total> stages).
```

On resume, first announce the existing run and its recovery point:

```text
[FM-Agent] Resuming <run id> from Stage <current>/<total>: <phase label> (<phase id>).
```

After a successful phase, state that it completed, then announce the next
stage before starting it. For a no-op, report that no analysis stages ran and
identify the retained baseline. On failure or a user stop, state the last
completed phase and the phase that did not finish.

| Mode | Ordered phase ids and labels |
| --- | --- |
| full | `preflight` Preflight; `project_understanding` Project understanding; `phase_cleanup` Prepare workspace; `extraction` Extract functions; `call_graph` Build call graph; `specification` Generate specifications; `verification` Verify implementations; `bug_validation` Validate bug candidates; `finalize` Finalize analysis |
| incremental | `validate_baseline` Validate baseline; `refresh_plan` Refresh plan; `preserve_specs` Preserve compatible specifications; `diff` Analyze source changes; `rebuild_graph` Rebuild call graph; `select_scope` Select affected scope; `update_specs` Update specifications; `verify_affected` Verify affected functions; `bug_validation` Validate bug candidates; `finalize` Finalize analysis |
