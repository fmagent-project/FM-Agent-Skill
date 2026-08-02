# Resume contract

Resume continues one interrupted `full` or `incremental` analysis. Ordinary
run auto-selects continuation when every identity check passes; explicit
`--resume` remains compatible and never creates a new run.

## Eligibility

Require all of the following:

- `refs/fm-agent-skill/active` and a complete persistent checkpoint exist.
- Snapshot commit, fingerprint, saved configuration, plugin version, source
  commit, Worker-definition hash, and state/scheduler schemas match.
- No unexpired Coordinator lease exists. Require explicit takeover while a
  fresh lease may still represent live work.
- The active record has a valid phase list and first incomplete task.

Do not accept a new note, scope, knowledge, supplemental edges, `one_phase`, or
backend choice. Report every differing field; never silently start a fresh run.

## Worktree reconstruction

If the recorded detached worktree exists, verify its `HEAD` and checkpoint
HEAD. If it is missing, create a new detached worktree at the active ref,
restore `checkpoint/current/fm_agent`, then restore only recovery
`active.json`, `config.json`, `jobs/`, `control/`, and `probes/`. Verify every
manifest and SHA-256 object. Exact restoration applies tombstones, so a deleted
artifact cannot reappear. A missing active ref or complete checkpoint is
non-resumable and must be named explicitly.

## Continuation

Reconcile expired SQLite leases before dispatch. An identical receipt is
idempotent; an old attempt or changed hash is rejected. Re-enter only the first
unfinished task. Do not regenerate a schema-valid completed spec or rerun an
earlier successful phase. Preserve the saved graph backend.

Only `pipeline.py complete` may promote `refs/fm-agent-skill/baseline` and
publish root `fm_agent/`. Failure releases the Coordinator lease but retains the
active ref and checkpoint for a later turn/session.
