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
two Bug Validators, and two read-only incremental plan workers. Configure these
through `max_active_subagents`, `spec_concurrency`, `verify_concurrency`,
`bug_validation_concurrency`, and `read_only_plan_concurrency`.
`spec_batch_size` defaults to `1`; `bug_validation_max_attempts` defaults to
`5` for runtime failures, and `bug_validation_negative_retries` defaults to
`2` additional completed negative probes. These scheduling and retry
limits are operational only and are excluded from the analysis fingerprint.

`bug_validation_execution` defaults to `agent-executed`. In that FM-Agent
compatibility mode, the active Codex/Claude Bug Validator Worker executes the
smallest project-scoped public-entry probe using the installed project
toolchain and records its exact evidence. Its registered dynamic ecosystems
all use `host-project-toolchain`: the Worker derives Maven, CMake, NVCC,
Hvigor, Erlang/OTP, or another concrete command from the project. It is
appropriate only for trusted projects and is not a sandbox guarantee. Set it
to `adapter` to use the
restricted fixed-argv local adapter instead; that mode supports fewer runtime
ecosystems. Any other saved value is rejected.

`probe_adapter` defaults to `auto`. Its accepted values are derived only from
the LanguageProfile registry's non-null `build_adapter` values, plus `auto` and
`none`; it does not accept a shell command. CUDA has no generic build adapter;
ArkTS source extraction is supported through its dedicated brace extractor but
its Hvigor runtime remains Worker-selected in `agent-executed` mode. A saved value
that is no longer in the registry is rejected; it is never silently changed to
`auto`. Automatic selection records the chosen adapter and the recognized
source languages in `fm_agent_skill/control/build_profile.json`. A build probe
run for a Bug Validator attempt writes its own immutable
`fm_agent_skill/probes/<bug-id>/attempt_<n>/build_profile.json` instead, so
parallel attempts never share mutable build evidence.

Every analysis creates one detached Git worktree from a private snapshot
commit. The snapshot captures tracked and non-ignored working-tree changes
without moving the user's branch, index, or `HEAD`. The Coordinator must use
the returned snapshot path until terminal completion. On `pipeline.py complete`,
the Skill promotes that commit to `refs/fm-agent-skill/baseline`, copies its
generated `fm_agent/` and `fm_agent_skill/` back to the source project, and
removes the worktree. A failed or interrupted analysis retains one snapshot for
`--resume`; it does not accumulate per-run history.
