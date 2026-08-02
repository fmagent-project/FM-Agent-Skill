# FM-Agent Skill

[English](#fm-agent-skill) | [简体中文](#中文说明)

FM-Agent Skill is a Claude Code and Codex correctness-analysis Skill following the
staged analysis ideas of [FM-Agent](https://github.com/fmagent-project/FM-Agent).
Its host Coordinator uses deterministic tools for state and graphs, and
dispatches the original FM-Agent semantic-worker boundaries as controlled host
subagents. It is a direct implementation: it never launches or imports the
original FM-Agent pipeline.

The Skill runs every analysis in a detached Git worktree and does not modify
business source code. It captures uncommitted non-ignored changes as a private
local snapshot commit without moving the user's branch, index, or `HEAD`.
The current release supports full analysis, Git-based incremental analysis,
no-op detection, persistent checkpoints, and automatic safe continuation.

## Features

- Extract function views and produce behavioral specifications and staged
  call-graph artifacts.
- Compare implementations with specifications, distinguishing direct violations
  (`MISMATCH`) from propagated dependency risks (`DEPENDENCY_RISK`).
- Preserve FM-Agent's core postcondition reasoner: derive actual behavior A,
  compare it with externally grounded specification B, and accept `MISMATCH`
  only with a concrete `A ∧ ¬B` counterexample and exact source evidence.
  Pre-schema-v2 verification artifacts are intentionally not reusable as a
  baseline and cause the next ordinary analysis to rebuild them.
- Build controlled isolated reproductions for eligible direct violations and
  report a confirmed defect only after actual dynamic evidence.
- Keep schema-v2 normative evidence separate from implementation observations.
  High-confidence contracts require exact copied user/public documentation;
  generated context, implementation literals, caller-only cycles, and
  benchmark/debug oracle markers cannot prove `MATCH`.
- Run a full analysis when no usable baseline exists.
- Run an incremental analysis automatically when the current Git snapshot
  differs from a valid baseline commit.
- Let users continue editing and committing the original worktree while the
  Skill analyzes its stable snapshot worktree.
- Rebuild a lost `/tmp` worktree from the active Git ref and persistent
  content-addressed checkpoint without repeating completed specs.
- Use CodeGraph automatically for an exact call graph when it is available, or
  record an `agent-static` best-effort fallback when it is unavailable.
- Run same-layer semantic workers concurrently with explicit write ownership,
  persisted job state, same-job bounded retries, and phase gates.
- Stream a SQLite-backed dependency DAG from specification through per-function
  verification and dynamic Bug Validation with atomic leases and receipts.

## Prerequisites

- The target must be a Git repository with a resolvable `HEAD`.
- The target must contain at least one supported source file.
- Use Claude Code or Codex with its subagent capability available.
- CodeGraph is optional. When available, it is rebuilt automatically for a
  full or incremental analysis. The Skill does not install missing software;
  it records an `agent-static` fallback instead.

## Installation

Both marketplaces expose the Skill as `fm-agent-skill`.

### Claude Code

```bash
claude plugin marketplace add fmagent-project/FM-Agent-Skill
claude plugin install fm-agent-skill@fm-agent-skill
```

Start a new Claude Code session after installation.

## Usage

Open the Git project in Claude Code or Codex and make a natural-language request:

```text
Use FM-Agent to analyze the current Git project.
```

Optionally state the change under review:

```text
Use FM-Agent to analyze the current Git project. Focus on changes to calculator input parsing.
```

The workflow accepts the following change note and options when a command entry
or natural-language request passes them in:

```text
/fm-agent:run [change note]
  [--submodule PATH ...]
  [--knowledge FILE ...]
  [--extra-edge FILE_OR_DIR]
  [--one-phase]
  [--resume]
```

| Option | Purpose |
| --- | --- |
| `--submodule` | Restrict analysis to one or more subdirectories. |
| `--knowledge` | Add Markdown domain knowledge. |
| `--extra-edge` | Add static call-graph edges. |
| `--one-phase` | Generate specifications in one phase. |
| `--resume` | Explicitly continue the eligible interrupted full or incremental analysis. It cannot be combined with a new note or configuration options. |

There is normally no need to select full or incremental mode manually. The
Skill selects it from its Git baseline commit and the current snapshot commit.

## Host worker scheduler

The Coordinator is the only SQLite/control-state writer. It maps phase
planning, domain context, specification batches, function verification, Bug
Validator, incremental selection, update planning, and caller reconciliation
to named host subagents. It streams ready DAG jobs with an enforced global
maximum of `10` workers (specification
`4`, verification `8`, Bug Validator `2`, read-only planning `2`) and evaluates
each phase gate after its jobs converge. The Coordinator receives compact worker receipts and
uses a phase receipt to inspect only escalations. Read
[the scheduler contract](plugins/fm-agent-skill/references/subagent-scheduler.md)
for the worker mapping and recovery rules.

### Failure, retry, and recovery

Each semantic unit has one durable job id in
`fm_agent_skill/checkpoint/state.db`; JSON is a Worker-readable mirror. A timeout, rate limit,
Agent-tool failure, missing output, or invalid output becomes `retryable`; the
Coordinator requeues the **same job** until its configured attempt limit is
reached (five total attempts by default). It never creates a replacement job
or rewires dependent jobs.

This follows FM-Agent's worker semantics: phase planning and domain context
retry after ten seconds; a specification layer immediately retries only its
remaining batches after partial progress, but waits ten seconds after no
progress. Valid sidecars are retained. A well-formed verification `ERROR` is a
terminal per-function reasoning result; a malformed structured reasoner output
is retried as an output failure. Bug Validator retries
runtime failures up to five attempts and repeats a completed negative result
twice by default, preserving all three probes.

Specification JSON uses original FM-Agent's closed three-field schema:
`signature`, `pre_condition`, and `post_condition`. Observable failure and
exception behavior belongs in `post_condition`. The scheduler deterministically
strips legacy identity, evidence, phase, summary, and convenience fields when
the three native fields are valid, folding a legacy `error_behavior` string
into `post_condition`; it reports every genuinely invalid pair in
the batch and retries only those pairs. A failed phase never falls back to a
static-audit result. The original worktree receives a small failure receipt,
and `diagnose.py` reports that no official result exists until all gates pass.

Bug validation has two deliberately separate surfaces. `probe_runner.py` is a
language-profile-selected build or syntax check and is never behavioral proof.
The default `agent-executed` Bug Validator first designs a public-entrypoint
probe, then its Codex/Claude Worker runs the smallest project-scoped command
sequence needed to reproduce it. This follows FM-Agent's broad language model:
it can use installed toolchains for C/C++, Python, Go, Rust, Java, JavaScript,
TypeScript, CUDA, ArkTS and Erlang, while recording exact commands, outputs and
exit codes in immutable evidence. All registered languages use the same
`host-project-toolchain` Worker contract; it derives the concrete command from
the project. A language still needs its upstream extraction/capability support
to create a `MISMATCH` candidate. ArkTS uses the same brace-boundary extractor
as FM-Agent because CodeGraph does not index `.ets`. For an ArkTS snapshot, the
Skill copies only project-local, lock-verified `oh_modules/` into the private
snapshot; it never runs `ohpm install` or copies `.hvigor/`. If those existing
dependencies are unavailable or unsafe, ArkTS dynamic validation is
`unsupported/inconclusive` rather than a reason to require HDC. This compatibility mode is not a sandbox
guarantee: use it only for projects you trust. It forbids `sudo`, dependency
installation, Git-state changes and unrelated file access, but it does not
provide the fixed-argv isolation of the optional `adapter` mode.

Up to two Bug Validator jobs run concurrently by default. Every dynamic Worker
uses its own attempt-local workspace and cache; it must not write shared
project-root build outputs. Adapter-mode build profiles are also stored beside
their owning attempt rather than in shared control state.

Set `bug_validation_execution` to `adapter` to require the hardened Bubblewrap
path: read-only project, private scratch, disabled network, and no host root or
home mount. That mode recognizes fewer ecosystems and is the basis for the
future controlled-adapter branch.

Bug Validation is host-coordinated: `bug_validation_executor.py` enforces the
preparation → Agent execution → finalization → retry/summary state machine,
while Codex or Claude Code invokes the three Worker passes through its native
subagent capability. It does not run or import the original FM-Agent pipeline.

`input`, `semantic`, and `cancelled` failures are terminal. They leave
dependents unscheduled and fail the current phase without discarding valid,
independent job outputs. On resume, the Coordinator first reconciles stale
`running` jobs: valid completed output is accepted, while incomplete output is
made retryable in place when attempts remain.

The Skill keeps no run history. A full analysis clears old generated FM-Agent
artifacts, trace payloads, current jobs, and probe builds. An incremental
analysis retains compatible sidecars and unchanged-file verification
results, removes changed/removed results, and clears prior bug, trace, job, and
probe outputs. A successful terminal analysis removes current jobs and
probes; `active.json` is overwritten by the next analysis. The long-lived
source baseline is `refs/fm-agent-skill/baseline`; `baseline.json` retains only
run configuration and completion provenance.

An ordinary run automatically continues a compatible stopped analysis; an
explicit request is also supported:

```text
Continue the interrupted FM-Agent analysis.
```

Resume requires the active Git ref, complete checkpoint, and unchanged saved
inputs. It rebuilds a missing worktree, validates hashes, and starts at the
first incomplete task.
It also reconciles per-worker job state before dispatching new work. Changes in
the original worktree do not alter the retained snapshot.

FM-Agent displays a `Stage current/total` update before each analysis stage. A
resumed analysis announces its recovery stage; a no-op explicitly
states that no analysis stage was required.

If the interrupted run has a heartbeat newer than the configured ten-minute
resume grace period, FM-Agent asks before taking over its lock. Confirm only
after the earlier agent or task has stopped. Resume state is held only in the
current `fm_agent_skill/active.json` record.

## Dispatch behavior

| State | Mode | CodeGraph behavior |
| --- | --- | --- |
| No usable baseline, or incomplete baseline artifacts | full | Rebuilds the index automatically when CodeGraph is available. |
| Valid baseline and a different snapshot commit | incremental | Rebuilds the index automatically when CodeGraph is available. |
| Valid baseline and the same snapshot commit | no-op | Does not inspect or rebuild CodeGraph. |

The baseline is the commit held by `refs/fm-agent-skill/baseline`; the latest
line in `fm_agent/version.log` mirrors it. A dirty worktree is captured in a
private local snapshot commit, so committed and uncommitted source states both
have one exact Git identity.

## CodeGraph and precision

CodeGraph is used only for a full or incremental analysis. When it is
available, the Skill automatically removes and rebuilds
`$PROJECT/.codegraph/`; no separate authorization is requested.

- Available, rebuilt, and mapped to current extracted artifacts: call-graph
  precision is recorded as `exact`.
- Unavailable: `agent-static` is used with `best-effort` precision and a
  fallback reason.
- No-op: `.codegraph/` is not touched.
- Resume: a completed valid graph phase is not rebuilt. When resuming an
  incomplete graph phase, a readable same-snapshot index is reused; otherwise
  the originally selected backend is rebuilt rather than silently changed.

## Artifacts

Artifacts are written to the target project, not the Skill installation:

| Directory | Contents |
| --- | --- |
| `fm_agent/` | Function copies plus `.spec.json` / `.info.json` sidecars, specification context, layers, verification results, and Bug Validator reports. |
| `fm_agent_skill/` | Baselines, the current active analysis, locks, control indexes, precision records, incremental decisions, and isolated probes. |
| `.codegraph/` | Generated CodeGraph index, rebuilt only during full or incremental runs when CodeGraph is available. |

`fm_agent/` follows FM-Agent's analysis-workspace model. An extracted function
is immutable source plus two sidecars, rather than source code annotated with
generated comments:

```text
fm_agent/
├── phases.json
├── fm_agent_file_list.json
├── extracted_functions/<source-path>/<function>.<ext>
├── extracted_functions/<source-path>/<function>.<ext>.spec.json
├── extracted_functions/<source-path>/<function>.<ext>.info.json
├── spec_prompts/
│   ├── system_prompt.md
│   ├── domain_context/engine_overview.txt
│   ├── domain_context/phase_XX_types.txt
│   ├── domain_context/user_knowledge/       # only when supplied
│   └── phase_XX_topdown_layers.json
├── logic_verification_results/
├── bug_validation/                           # only for direct MISMATCH probes
├── incremental_updated_specs.json            # incremental runs only
└── trace/events.jsonl
```

Each `.spec.json` contains exactly `signature`, `pre_condition`, and
`post_condition`. Each `.info.json` contains `callees`, with the caller's
expected contract for every in-scope callee. `fm_agent_file_list.json` and the
Skill control index list only function source copies, never sidecars.

`fm_agent_skill/` is deliberately separate from those analysis artifacts. It
is the sole location for mutable orchestration data such as `config.json`, the
current analysis, locks, Git baseline metadata, incremental decisions, and
isolated probe builds. It is not an FM-Agent analysis result.

For an incremental run, the latest module/file-selection records and
specification-update records remain in `fm_agent/`; only verification results
for changed or removed functions and prior Bug Validator results are cleared.
This preserves a complete Git-commit baseline without presenting old bug
reports as new findings.

Useful files include:

```text
fm_agent/bug_validation/summary.json
fm_agent/bug_validation/<function>.md
fm_agent_skill/active.json
fm_agent/version.log
fm_agent_skill/control/call_graph_precision.json
```

Add these generated directories to the target project's `.gitignore`; they are
analysis state and reports, not business source.

## Safety and recovery

- The Skill does not modify business source files or write specification
  comments back into them. It also does not modify extracted function copies;
  generated contracts are sidecars.
- Each analysis owns a current-workspace lock. Completion, failure, or an
  explicit stop releases it while preserving `active.json` for resume.
- A full run clears only old derived artifacts; it does not remove business
  source or the current phase definition.
- An incremental run preserves unaffected specifications and revalidates direct
  violations instead of reusing bug-validation conclusions from an old run.
- Resume is automatic when identities match and validates the Git snapshot,
  checkpoint, plugin/Worker versions, and configuration. A fresh lease is a potentially active
  analysis; lock takeover requires an explicit user confirmation.
- Every non-noop analysis runs in a disposable detached Git worktree. The source
  checkpoint updates after every gate; only complete `fm_agent/` is published
  on success. A failed/stopped run does not depend on retaining `/tmp`.

## Verified workflow

The following two-step workflow was verified with `cpp-demo`:

1. Run a full analysis while a source file is still uncommitted. The Skill
   creates a private snapshot commit and analyzes it in a detached worktree.
2. Continue editing or commit in the original worktree while analysis proceeds;
   the active snapshot remains stable. The next run compares the new snapshot
   against the successful baseline commit.

The scheduler test covers retryable Agent failures, exhausted attempts,
interrupted-job recovery, invalid outputs, and Bug Validator admission limits.

## Repository layout

```text
.
├── .agents/plugins/marketplace.json    # Codex marketplace manifest
├── .claude-plugin/marketplace.json     # Claude Code marketplace manifest
└── plugins/fm-agent-skill/
    ├── agents/                         # Host workers mapped to FM-Agent LLM workers
    ├── skills/                         # run, help, install, diagnose, config
    ├── scripts/                        # dispatch, scheduler, state, locks, graph, validation
    ├── src/fm_agent_core/              # shared state and artifact logic
    └── references/                     # workflow, specification, verification rules
```

## License

Licensed under the [Apache License 2.0](LICENSE).

---

# 中文说明

FM-Agent Skill 是面向 Claude Code 与 Codex 的代码正确性分析 Skill。它借鉴
[FM-Agent](https://github.com/fmagent-project/FM-Agent) 的分阶段分析思路：Coordinator
负责基线、锁、状态、调用图和调度，Claude subagent 负责与原 FM-Agent LLM worker 一一对应的
语义工作，并由本地确定性脚本管理 checkpoint、DAG 与结果权限；脚本不调用模型 API。

它在 Git 工作区中运行，不修改业务源码。当前支持完整分析、自动增量分析、no-op，以及安全地续跑中断分析。

## 安装与使用

Skill 名为 `fm-agent-skill`。Claude Code 使用其插件安装命令分发该 Skill：

```bash
# Claude Code
claude plugin marketplace add fmagent-project/FM-Agent-Skill
claude plugin install fm-agent-skill@fm-agent-skill
```

安装后新建 task 或会话。在目标 Git 项目中直接请求：

```text
使用 FM-Agent 分析当前 Git 项目
```

支持命令入口或自然语言请求附带修改说明、`--submodule`、`--knowledge`、`--extra-edge`、
`--one-phase` 或 `--resume`。

如需续跑被中断的 full 或 incremental，请明确请求：

```text
继续执行刚才中断的 FM-Agent 分析。
```

普通 run 会自动续跑兼容的中断分析；临时 worktree 丢失时会由 active Git ref 与持久化 checkpoint 重建，不受原工作区后续源码修改影响。

full、incremental 和 resume 都会在每个阶段前显示“当前阶段/总阶段数”；resume 会先显示恢复位置，no-op 会明确说明没有执行分析阶段。

若当前分析的心跳仍在默认 10 分钟宽限期内，FM-Agent 会先询问是否接管锁。只有确认旧 task 已停止后才应同意接管。恢复次数、恢复时间和恢复阶段会记录在 `fm_agent_skill/active.json`。

## Claude worker 调度、失败与恢复

Coordinator 是 SQLite 与控制状态的唯一写入者。它按 durable DAG 流式推进，最多并发
全局最多运行 10 个独立 worker（规格 4、验证 8、Bug Validator 2、只读增量计划 2）；调度器在启动前强制占用名额，join 后写入阶段回执并通过 gate 才能进入下一阶段。
每个语义单元有固定 job id，权威状态保存在 `fm_agent_skill/checkpoint/state.db`，JSON 仅供 Worker 读取和旧状态诊断。

超时、限流、Agent 工具失败、缺失产物或无效产物会成为 `retryable`。Coordinator 会在**同一个
job** 上有界重试，默认总尝试次数为 5，不创建替代 job，也不修改下游依赖。phase plan 和
domain context 每次失败后等待 10 秒；spec 阶段若已得到部分有效 sidecar，会立即补跑剩余 batch，
无任何进展才等待 10 秒。有效 sidecar 会保留。

验证推理本身失败时应写出有效的 `ERROR` result，而不是重复调度；只有 Agent 或结果产物失败才
重试。默认的 Bug Validator 先设计公共入口 probe，再由 Codex/Claude Worker 使用项目已有工具链实际执行，并记录精确命令、输出和退出码；默认最多并发两个 job，每个 job 只能使用自身 attempt 目录下的 workspace 和缓存，不能写入项目根目录的共享构建产物。构建或语法检查不能确认缺陷。该快速兼容模式不是沙箱保证：仅用于可信项目，且 Worker 不得使用 `sudo`、安装依赖、修改 Git 状态或读取无关用户文件。需要固定 argv、禁网和只读项目隔离时，将 `bug_validation_execution` 设为 `adapter`。运行或产物失败最多尝试 5 次；正常完成但未复现或证据不足时会在同一 job 内额外复测 2 次，并保留全部 probe 证据。`input`、`semantic` 和 `cancelled` 是终止失败：下游
不会启动，当前阶段失败，但已有独立有效产物会保留。resume 前会先回收遗留 `running` job：产物有效
则直接成功，否则在次数未耗尽时原地转为 `retryable`。

Skill 不保留 run 历史。full 会清理旧的派生产物、trace payload、当前 jobs 与 probe；
incremental 会保留兼容 sidecar，但清理旧 verification、bug、trace、jobs 与 probe。
成功结束后会删除当前 jobs 与 probe；下一次分析覆盖 `active.json`。源码基线由
`refs/fm-agent-skill/baseline` 与 `fm_agent/version.log` 保存。

## 运行方式与产物

- 无可用基线时执行 full；当前 Git snapshot 与基线不同则执行 incremental。
- 当前 Git snapshot 与基线相同时执行 no-op，不会重建 CodeGraph。
- CodeGraph 仅在 full 或 incremental 时自动重建；不可用时记录 `agent-static` 回退。
- resume 不会重建已经完成且有效的调用图；若调用图阶段本身中断，则复用可读的同快照索引或按原后端重建。
- `fm_agent/` 保存函数副本、sidecar 规约、验证和缺陷报告；`fm_agent_skill/` 保存基线、运行记录和控制状态；
  `.codegraph/` 保存生成索引。建议将三者加入目标项目的 `.gitignore`。

基线是 `refs/fm-agent-skill/baseline` 指向的 commit。未提交源码会先被写入私有 snapshot
commit，不移动用户分支、暂存区或 `HEAD`。

## 已验证场景

在 `cpp-demo` 中验证：对未提交源码启动分析后，Skill 在 detached snapshot worktree 中运行；
用户可以同时修改原工作区，下一次运行会以新的 snapshot 与成功基线进行 Git diff。

调度测试覆盖：超时重试、次数耗尽、中断恢复、无效产物自动重试，以及 Bug Validator 的单次限制。
