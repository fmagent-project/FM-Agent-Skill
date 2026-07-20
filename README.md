# FM-Agent Skill

[English](#fm-agent-skill) | [简体中文](#中文说明)

FM-Agent Skill is a correctness-analysis plugin for **Codex** and **Claude
Code**. It follows the staged analysis ideas of
[FM-Agent](https://github.com/fmagent-project/FM-Agent), while the active
coding agent reads code, writes behavioral specifications, reasons about
implementations, and validates candidate defects. Bundled deterministic tools
manage state, locks, baselines, call-graph metadata, and artifact checks.

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

## Prerequisites

- The target must be a Git repository with a resolvable `HEAD`.
- The target must contain at least one supported source file.
- Install and sign in to either Codex or Claude Code.
- CodeGraph is optional. When available, it is rebuilt automatically for a
  full or incremental analysis. The plugin does not install missing software;
  it records an `agent-static` fallback instead.

## Installation

Both marketplaces expose the plugin as `fm-agent-skill`.

### Codex

```bash
codex plugin marketplace add fmagent-project/FM-Agent-Skill
codex plugin add fm-agent-skill@fm-agent-skill
```

Start a new Codex task after installation or an update so the refreshed skills
are loaded.

### Claude Code

```bash
claude plugin marketplace add fmagent-project/FM-Agent-Skill
claude plugin install fm-agent-skill@fm-agent-skill
```

Start a new Claude Code session after installation.

## Usage

Open the Git project to analyze and make a natural-language request in either
client:

```text
Use FM-Agent to analyze the current Git project.
```

Optionally state the change under review:

```text
Use FM-Agent to analyze the current Git project. Focus on changes to calculator input parsing.
```

In Codex, invoke the `run` skill with the natural-language request above;
installing a skill does not register a Codex slash command. The workflow accepts
the following change note and options when a client exposes a command entry or
passes them in the request:

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

To continue a stopped analysis, make an explicit request instead of selecting a
mode manually:

```text
Continue the interrupted FM-Agent analysis.
```

Resume requires unchanged supported-source content and unchanged saved analysis
inputs. It retains the original run id, validates completed stages, and starts
at the first incomplete one. A source-changing commit requires a normal new
analysis; a commit with identical source content can still resume.

If the interrupted run has a heartbeat newer than the configured ten-minute
resume grace period, FM-Agent asks before taking over its lock. Confirm only
after the earlier agent or task has stopped. A resumed run records its count,
timestamp, and resumed phase in `fm_agent_plugin/runs/<run-id>.json`.

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
| `fm_agent/` | Function extraction, phase specifications, verification results, and FM-Agent-style bug-validation reports. |
| `fm_agent_plugin/` | Baselines, run records, locks, control indexes, precision records, incremental decisions, and isolated probes. |
| `.codegraph/` | Generated CodeGraph index, rebuilt only during full or incremental runs when CodeGraph is available. |

Useful files include:

```text
fm_agent/bug_validation/summary.json
fm_agent/bug_validation/<function>.md
fm_agent_plugin/runs/<run-id>.json
fm_agent_plugin/baseline.json
fm_agent_plugin/control/call_graph_precision.json
```

Add these generated directories to the target project's `.gitignore`; they are
analysis state and reports, not business source.

## Safety and recovery

- The plugin does not modify business source files or write specification
  comments back into them.
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

The deterministic resume integration test can be run from WSL or a compatible
Python environment:

```bash
python3 tests/test_resume.py
```

It simulates an interrupted full run, verifies that resume keeps its run id and
continues after the last successful phase, verifies the fresh-lock takeover
guard, and rejects resume after a supported source change.

## Repository layout

```text
.
├── .agents/plugins/marketplace.json    # Codex marketplace manifest
├── .claude-plugin/marketplace.json     # Claude Code marketplace manifest
└── plugins/fm-agent-skill/
    ├── skills/                         # run, help, install, diagnose, config
    ├── scripts/                        # dispatch, state, locks, graph, validation
    ├── src/fm_agent_core/              # shared state and artifact logic
    └── references/                     # workflow, specification, verification rules
```

## License

Licensed under the [Apache License 2.0](LICENSE).

---

# 中文说明

FM-Agent Skill 是同时支持 Codex 和 Claude Code 的代码正确性分析插件。它借鉴
[FM-Agent](https://github.com/fmagent-project/FM-Agent) 的分阶段分析思路：当前 Agent
负责读代码、写行为规约、推理和验证；插件脚本负责基线、锁、状态、调用图记录和产物校验。

它在 Git 工作区中运行，不修改业务源码。当前支持完整分析、自动增量分析、no-op，以及安全地续跑中断分析。

## 安装与使用

插件名为 `fm-agent-skill`。安装命令：

```bash
# Codex
codex plugin marketplace add fmagent-project/FM-Agent-Skill
codex plugin add fm-agent-skill@fm-agent-skill

# Claude Code
claude plugin marketplace add fmagent-project/FM-Agent-Skill
claude plugin install fm-agent-skill@fm-agent-skill
```

安装后新建 task 或会话。在目标 Git 项目中直接请求：

```text
使用 FM-Agent 分析当前 Git 项目
```

Codex 使用上面的自然语言请求触发 `run` skill；安装 skill 不会自动注册 Codex 斜杠命令。
支持命令入口的客户端或请求可附带修改说明、`--submodule`、`--knowledge`、`--extra-edge`、
`--one-phase`、`--isolate` 或 `--resume`。

如需续跑被中断的 full 或 incremental，请明确请求：

```text
继续执行刚才中断的 FM-Agent 分析。
```

resume 会保留原 run id，从第一个未完成阶段继续；只有源码内容和原分析配置均未变化时才会执行。源码改变后应重新运行；仅提交了相同源码内容时仍可续跑。

若旧 run 的心跳仍在默认 10 分钟宽限期内，FM-Agent 会先询问是否接管锁。只有确认旧 task 已停止后才应同意接管。恢复次数、恢复时间和恢复阶段会记录在 `fm_agent_plugin/runs/<run-id>.json`。

## 运行方式与产物

- 无可用基线时执行 full；业务源码变化时自动执行 incremental。
- 源码内容未变时执行 no-op；即使只新增 Git 提交，也只更新 `observed_commit`，不会重建
  CodeGraph。
- CodeGraph 仅在 full 或 incremental 时自动重建；不可用时记录 `agent-static` 回退。
- resume 不会重建已经完成且有效的调用图；若调用图阶段本身中断，则复用可读的同快照索引或按原后端重建。
- `fm_agent/` 保存规约、验证和缺陷报告；`fm_agent_plugin/` 保存基线、运行记录和控制状态；
  `.codegraph/` 保存生成索引。建议将三者加入目标项目的 `.gitignore`。

`analysis_commit` 表示当前分析结果对应的提交，`observed_commit` 表示最近一次确认源码快照
一致的提交。因此，先分析未提交内容、再提交相同内容不会重复分析。

## 已验证场景

在 `cpp-demo` 中已验证：首次对未提交源码执行 full 后，再提交完全相同的源码并运行，第二次会
正确返回 no-op，保留分析基线与缺陷结论，并更新 `observed_commit`。

可执行 `python3 tests/test_resume.py` 验证 resume 的状态机：模拟中断、同 run id 续跑、对新鲜锁的接管保护，以及源码变化后的拒绝恢复。
