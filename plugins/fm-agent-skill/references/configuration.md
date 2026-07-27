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

`scheduler_executor` is currently fixed to `claude-subagent`: semantic work
requires Claude Code's Agent tool. `concurrency` is the maximum number of
simultaneously active Claude workers, matching the original FM-Agent worker
limit by default (`10`). `spec_batch_size` defaults to `2`, matching the
original batch-prompt granularity. `bug_validation_max_attempts` defaults to
`1`, matching FM-Agent's Bug Validator retry limit. These values are analysis
configuration and therefore participate in the fingerprint.
