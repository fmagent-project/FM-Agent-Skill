# FM-Agent Skill

[English](#fm-agent-skill) | [简体中文](#中文说明)

FM-Agent Skill is a Claude Code correctness-analysis plugin following the
staged analysis ideas of [FM-Agent](https://github.com/fmagent-project/FM-Agent).
Its Coordinator uses deterministic tools for state and graphs, and dispatches
the original FM-Agent semantic-worker boundaries as controlled Claude
subagents. A Codex executor is planned but is not included in this release.

The plugin runs in a Git working tree and does not modify business source code.
The current release supports full analysis, automatic incremental analysis,
no-op provenance refreshes, and explicit safe resume of interrupted analyses.

## Features

- Extract function views and produce behavioral specifications and staged
  call-graph artifacts.
- Compare implementations with specifications, distinguishing direct violations
  (`MISMATCH`) from propagated dependency risks (`DEPENDENCY_RISK`).
- Build isolated reproductions for eligible direct violations and report
  confirmed defects.
- Run a full analysis when no usable baseline exists.
- Run an incremental analysis automatically when a valid baseline exists and
  business source content changes.
- Skip repeated analysis when business source content is unchanged, even when
  the Git commit changes.
- Continue an interrupted full or incremental run from its first incomplete
  phase without creating a new run id or repeating completed work.
- Use CodeGraph automatically for an exact call graph when it is available, or
  record an `agent-static` best-effort fallback when it is unavailable.
- Run same-layer semantic workers concurrently with explicit write ownership,
  persisted job state, same-job bounded retries, and phase gates.

## Prerequisites

- The target must be a Git repository with a resolvable `HEAD`.
- The target must contain at least one supported source file.
- Install and sign in to Claude Code with its `Agent` tool available.
- CodeGraph is optional. When available, it is rebuilt automatically for a
  full or incremental analysis. The plugin does not install missing software;
  it records an `agent-static` fallback instead.

## Installation

Both marketplaces expose the plugin as `fm-agent-skill`.

### Claude Code

```bash
claude plugin marketplace add fmagent-project/FM-Agent-Skill
claude plugin install fm-agent-skill@fm-agent-skill
```

Start a new Claude Code session after installation.

## Usage

Open the Git project in Claude Code and make a natural-language request:

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
  [--isolate]
  [--resume]
```

| Option | Purpose |
| --- | --- |
| `--submodule` | Restrict analysis to one or more subdirectories. |
| `--knowledge` | Add Markdown domain knowledge. |
| `--extra-edge` | Add static call-graph edges. |
| `--one-phase` | Generate specifications in one phase. |
| `--isolate` | Request analysis in an isolated Git worktree. |
| `--resume` | Explicitly continue the newest eligible interrupted full or incremental run. It cannot be combined with a new note or configuration options. |

There is normally no need to select full or incremental mode manually. The
plugin selects it from its baseline and source snapshot.

## Claude worker scheduler

The Coordinator is the only writer of `fm_agent_skill/` state. It maps phase
planning, domain context, specification batches, function verification, Bug
Validator, incremental selection, update planning, and caller reconciliation
to named Claude subagents. It runs phases serially, dispatches independent
same-layer jobs in parallel up to `concurrency` (default `10`), and joins them
before each gate. Read
[the scheduler contract](plugins/fm-agent-skill/references/subagent-scheduler.md)
for the worker mapping and recovery rules.

### Failure, retry, and recovery

Each semantic unit has one durable job id at
`fm_agent_skill/runs/<run-id>/jobs/<job-id>.json`. A timeout, rate limit,
Agent-tool failure, missing output, or invalid output becomes `retryable`; the
Coordinator requeues the **same job** until its configured attempt limit is
reached (five total attempts by default). It never creates a replacement job
or rewires dependent jobs.

This follows FM-Agent's worker semantics: phase planning and domain context
retry after ten seconds; a specification layer immediately retries only its
remaining batches after partial progress, but waits ten seconds after no
progress. Valid sidecars are retained. A verification reasoning problem is a
valid `ERROR` result rather than a scheduling retry. Bug Validator has one
total attempt by default, matching FM-Agent.

`input`, `semantic`, and `cancelled` failures are terminal. They leave
dependents unscheduled and fail the current phase without discarding valid,
independent job outputs. On resume, the Coordinator first reconciles stale
`running` jobs: valid completed output is accepted, while incomplete output is
made retryable in place when attempts remain.

To continue a stopped analysis, make an explicit request instead of selecting a
mode manually:

```text
Continue the interrupted FM-Agent analysis.
```

Resume requires unchanged supported-source content and unchanged saved analysis
inputs. It retains the original run id, validates completed stages, and starts
at the first incomplete one. It also reconciles per-worker job state before
dispatching new work. A source-changing commit requires a normal new analysis;
a commit with identical source content can still resume.

FM-Agent displays the run id and a `Stage current/total` update before each
analysis stage. A resumed run announces its recovery stage; a no-op explicitly
states that no analysis stage was required.

If the interrupted run has a heartbeat newer than the configured ten-minute
resume grace period, FM-Agent asks before taking over its lock. Confirm only
after the earlier agent or task has stopped. A resumed run records its count,
timestamp, and resumed phase in `fm_agent_skill/runs/<run-id>.json`.

## Dispatch behavior

| State | Mode | CodeGraph behavior |
| --- | --- | --- |
| No usable baseline, or incomplete baseline artifacts | full | Rebuilds the index automatically when CodeGraph is available. |
| Valid baseline and changed business source content | incremental | Rebuilds the index automatically when CodeGraph is available. |
| Valid baseline and unchanged business source content | no-op | Does not inspect or rebuild CodeGraph. |
| Only the Git commit changed | no-op | Refreshes `observed_commit` only and retains the analysis baseline. |

The baseline separates two kinds of provenance:

- `analysis_commit`: the commit associated with the current full or
  incremental analysis result.
- `observed_commit`: the most recent commit whose source snapshot was confirmed
  to match the baseline.

As a result, analyzing uncommitted source and then committing exactly the same
content does not cause a duplicate analysis.

## CodeGraph and precision

CodeGraph is used only for a full or incremental analysis. When it is
available, the plugin automatically removes and rebuilds
`$PROJECT/.codegraph/`; no separate authorization is requested.

- Available and rebuilt successfully: call-graph precision is recorded as
  `exact`.
- Unavailable: `agent-static` is used with `best-effort` precision and a
  fallback reason.
- No-op: `.codegraph/` is not touched.
- Resume: a completed valid graph phase is not rebuilt. When resuming an
  incomplete graph phase, a readable same-snapshot index is reused; otherwise
  the originally selected backend is rebuilt rather than silently changed.

## Artifacts

Artifacts are written to the target project, not the plugin installation:

| Directory | Contents |
| --- | --- |
| `fm_agent/` | Function copies plus `.spec.json` / `.info.json` sidecars, specification context, layers, verification results, and Bug Validator reports. |
| `fm_agent_skill/` | Baselines, run records, locks, control indexes, precision records, incremental decisions, and isolated probes. |
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
plugin control index list only function source copies, never sidecars.

`fm_agent_skill/` is deliberately separate from those analysis artifacts. It
is the sole location for mutable orchestration data such as `config.json`, run
records, locks, baselines, function hashes, incremental decisions, and
isolated probe builds. It is not an FM-Agent analysis result.

For an incremental run, the latest module/file-selection records and
specification-update records remain in `fm_agent/`; previous verification and
Bug Validator results are cleared before the new incremental run begins. This
prevents reports from different analysis runs being presented as one result.

Useful files include:

```text
fm_agent/bug_validation/summary.json
fm_agent/bug_validation/<function>.md
fm_agent_skill/runs/<run-id>.json
fm_agent_skill/baseline.json
fm_agent_skill/control/call_graph_precision.json
```

Add these generated directories to the target project's `.gitignore`; they are
analysis state and reports, not business source.

## Safety and recovery

- The plugin does not modify business source files or write specification
  comments back into them. It also does not modify extracted function copies;
  generated contracts are sidecars.
- Each analysis owns a run lock. Completion, failure, or an explicit stop
  releases that lock while preserving its run record.
- A full run clears only old derived artifacts; it does not remove business
  source or the current phase definition.
- An incremental run preserves unaffected specifications and revalidates direct
  violations instead of reusing bug-validation conclusions from an old run.
- Resume is explicit and validates the original source snapshot and analysis
  configuration. A fresh lock heartbeat is treated as a potentially active
  analysis; lock takeover requires an explicit user confirmation.

## Verified workflow

The following two-step workflow was verified with `cpp-demo`:

1. Run a full analysis while a source file is still uncommitted. It produced an
   exact CodeGraph call graph, a baseline snapshot, and two confirmed defects.
2. Commit the identical source content and run again. The result was no-op:
   CodeGraph was not rebuilt, `analysis_commit` was retained,
   `observed_commit` advanced to the new commit, and no active lock remained.

This verifies that re-analysis is determined by source content rather than Git
commit identity alone.

The scheduler test covers retryable Agent failures, exhausted attempts,
interrupted-job recovery, invalid outputs, and the single-attempt Bug Validator
limit.

## Repository layout

```text
.
├── .agents/plugins/marketplace.json    # Codex marketplace manifest
├── .claude-plugin/marketplace.json     # Claude Code marketplace manifest
└── plugins/fm-agent-skill/
    ├── agents/                         # Claude workers mapped to FM-Agent LLM workers
    ├── skills/                         # run, help, install, diagnose, config
    ├── scripts/                        # dispatch, scheduler, state, locks, graph, validation
    ├── src/fm_agent_core/              # shared state and artifact logic
    └── references/                     # workflow, specification, verification rules
```

## License

Licensed under the [Apache License 2.0](LICENSE).

---

# 中文说明

FM-Agent Skill 是面向 Claude Code 的代码正确性分析插件。它借鉴
[FM-Agent](https://github.com/fmagent-project/FM-Agent) 的分阶段分析思路：Coordinator
负责基线、锁、状态、调用图和调度，Claude subagent 负责与原 FM-Agent LLM worker 一一对应的
语义工作。Codex executor 仍在后续规划中，当前版本不应在 Codex 中宣称已完成语义分析。

它在 Git 工作区中运行，不修改业务源码。当前支持完整分析、自动增量分析、no-op，以及安全地续跑中断分析。

## 安装与使用

插件名为 `fm-agent-skill`。安装命令：

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
`--one-phase`、`--isolate` 或 `--resume`。

如需续跑被中断的 full 或 incremental，请明确请求：

```text
继续执行刚才中断的 FM-Agent 分析。
```

resume 会保留原 run id，从第一个未完成阶段继续；只有源码内容和原分析配置均未变化时才会执行。源码改变后应重新运行；仅提交了相同源码内容时仍可续跑。

full、incremental 和 resume 都会在每个阶段前显示 run id 与“当前阶段/总阶段数”；resume 会先显示恢复位置，no-op 会明确说明没有执行分析阶段。

若旧 run 的心跳仍在默认 10 分钟宽限期内，FM-Agent 会先询问是否接管锁。只有确认旧 task 已停止后才应同意接管。恢复次数、恢复时间和恢复阶段会记录在 `fm_agent_skill/runs/<run-id>.json`。

## Claude worker 调度、失败与恢复

Coordinator 是 `fm_agent_skill/` 的唯一写入者。它按阶段串行推进，在同一调用层内最多并发
`concurrency`（默认 10）个独立 Claude worker；join 后必须通过 gate 才能进入下一阶段。
每个语义单元有固定 job id，保存在
`fm_agent_skill/runs/<run-id>/jobs/<job-id>.json`。

超时、限流、Agent 工具失败、缺失产物或无效产物会成为 `retryable`。Coordinator 会在**同一个
job** 上有界重试，默认总尝试次数为 5，不创建替代 job，也不修改下游依赖。phase plan 和
domain context 每次失败后等待 10 秒；spec 阶段若已得到部分有效 sidecar，会立即补跑剩余 batch，
无任何进展才等待 10 秒。有效 sidecar 会保留。

验证推理本身失败时应写出有效的 `ERROR` result，而不是重复调度；只有 Agent 或结果产物失败才
重试。Bug Validator 默认总共只尝试 1 次。`input`、`semantic` 和 `cancelled` 是终止失败：下游
不会启动，当前阶段失败，但已有独立有效产物会保留。resume 前会先回收遗留 `running` job：产物有效
则直接成功，否则在次数未耗尽时原地转为 `retryable`。

## 运行方式与产物

- 无可用基线时执行 full；业务源码变化时自动执行 incremental。
- 源码内容未变时执行 no-op；即使只新增 Git 提交，也只更新 `observed_commit`，不会重建
  CodeGraph。
- CodeGraph 仅在 full 或 incremental 时自动重建；不可用时记录 `agent-static` 回退。
- resume 不会重建已经完成且有效的调用图；若调用图阶段本身中断，则复用可读的同快照索引或按原后端重建。
- `fm_agent/` 保存函数副本、sidecar 规约、验证和缺陷报告；`fm_agent_skill/` 保存基线、运行记录和控制状态；
  `.codegraph/` 保存生成索引。建议将三者加入目标项目的 `.gitignore`。

`analysis_commit` 表示当前分析结果对应的提交，`observed_commit` 表示最近一次确认源码快照
一致的提交。因此，先分析未提交内容、再提交相同内容不会重复分析。

## 已验证场景

在 `cpp-demo` 中已验证：首次对未提交源码执行 full 后，再提交完全相同的源码并运行，第二次会
正确返回 no-op，保留分析基线与缺陷结论，并更新 `observed_commit`。

调度测试覆盖：超时重试、次数耗尽、中断恢复、无效产物自动重试，以及 Bug Validator 的单次限制。
