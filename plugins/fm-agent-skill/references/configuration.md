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
Claude Code or Codex subagent facility. The enforced default profile is ten
active workers globally, two specification batches, four verification workers,
one Bug Validator, and two read-only incremental plan workers. Configure these
through `max_active_subagents`, `spec_concurrency`, `verify_concurrency`,
`bug_validation_concurrency`, and `read_only_plan_concurrency`.
`spec_batch_size` defaults to `1`; `bug_validation_max_attempts` defaults to
`5` for runtime failures, and `bug_validation_negative_retries` defaults to
`2` additional completed negative probes. These scheduling and retry
limits are operational only and are excluded from the analysis fingerprint.

`probe_adapter` defaults to `auto`. It may select only `cmake`, `cargo`, `go`,
`python`, `java`, `javascript`, `typescript`, `cuda`, `arkts`, or `none`; it
does not accept a shell command. Automatic selection records the chosen adapter
and the recognized source languages in `fm_agent_skill/control/build_profile.json`.

Every analysis creates one detached Git worktree from a private snapshot
commit. The snapshot captures tracked and non-ignored working-tree changes
without moving the user's branch, index, or `HEAD`. The Coordinator must use
the returned snapshot path until terminal completion. On `pipeline.py complete`,
the Skill promotes that commit to `refs/fm-agent-skill/baseline`, copies its
generated `fm_agent/` and `fm_agent_skill/` back to the source project, and
removes the worktree. A failed or interrupted analysis retains one snapshot for
`--resume`; it does not accumulate per-run history.
