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
Claude Code or Codex subagent facility. The enforced default profile is sixteen
active workers globally, eight specification batches, twelve verification workers,
four Bug Validators, and four read-only incremental plan workers. Configure these
through `max_active_subagents`, `spec_concurrency`, `verify_concurrency`,
`bug_validation_concurrency`, and `read_only_plan_concurrency`.
`worker_target_tokens` (12000), `worker_max_functions` (20), and
`worker_max_source_bytes` (262144) drive deterministic adaptive spec/verify
partitioning. `spec_batch_size` is a deprecated explicit compatibility cap.
`worker_lease_seconds` defaults to 900. `bug_validation_max_attempts` defaults to
`5` for runtime failures, and `bug_validation_negative_retries` defaults to
`2` additional completed negative probes. These scheduling and retry
limits are operational only and are excluded from the analysis fingerprint.

`worker_prompt_change_policy` defaults to `invalidate`: any Worker document
change blocks automatic resume. `allow_prompt_only` is an explicit future-facing
exception that may ignore a changed `worker_prompt_hash` only when the sealed
`worker_execution_hash` remains identical. Existing Workers have no delimited
prompt-only region, so any document change currently changes both hashes.

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

Every analysis creates a disposable detached Git worktree from a private snapshot
commit. The snapshot captures tracked and non-ignored working-tree changes
without moving the user's branch, index, or `HEAD`. The Coordinator must use
the returned snapshot path until terminal completion. On `pipeline.py complete`,
the Skill promotes that commit to `refs/fm-agent-skill/baseline`, publishes only
the complete generated `fm_agent/`, and removes the worktree. Every gate already
persisted state under source `fm_agent_skill/checkpoint/`, so failure and resume
do not depend on retaining the temporary directory.
