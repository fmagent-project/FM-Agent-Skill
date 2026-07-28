# Configuration fingerprint

`resume_grace_seconds` controls how long a same-run lock heartbeat is treated
as potentially active before an ordinary resume may reclaim it. It is an
operational safety setting and does not alter analysis semantics.

Use one phase only when the project needs a deliberately flattened plan. Use
submodules to limit scope to project-relative directories. Extra edges must be
validated JSON and domain knowledge must be readable Markdown.

The fingerprint includes `one_phase`, submodules, extra-edge content hash, and
each knowledge-file content hash. It exists to prevent an incremental run from
reusing specifications built for a different scope or knowledge set.

`scheduler_executor` is fixed to `host-subagent`: semantic work uses the active
Claude Code or Codex subagent facility. `concurrency` is the maximum number of
simultaneously active host workers, matching the original FM-Agent worker
limit by default (`10`). `spec_batch_size` defaults to `2`, matching the
original batch-prompt granularity. `bug_validation_max_attempts` defaults to
`1`, matching FM-Agent's Bug Validator retry limit. These values are analysis
configuration and therefore participate in the fingerprint.

`isolate=true` creates one throwaway Git worktree for the current analysis. The
Coordinator must use the returned snapshot path until terminal completion. On
`pipeline.py complete`, the Skill copies its generated `fm_agent/` and
`fm_agent_skill/` back to the source project and removes the worktree. A failed
or interrupted isolated analysis retains that one snapshot for `--resume`; it
does not accumulate per-run history.
