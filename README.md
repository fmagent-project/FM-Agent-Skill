# FM-Agent Skill

[English](#fm-agent-skill) | [简体中文](#中文说明)

FM-Agent Skill is a correctness-analysis plugin for **Codex** and **Claude
Code**. It follows the staged analysis ideas of
[FM-Agent](https://github.com/fmagent-project/FM-Agent), while the active
coding agent reads code, writes behavioral specifications, reasons about
implementations, and validates candidate defects. Bundled deterministic tools
manage state, locks, baselines, call-graph metadata, and artifact checks.

The plugin runs in a Git working tree and does not modify business source code.
The current release supports full analysis, automatic incremental analysis, and
no-op provenance refreshes.

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
- Use CodeGraph with explicit permission for an exact call graph, or record an
  `agent-static` best-effort fallback when it is unavailable or declined.

## Prerequisites

- The target must be a Git repository with a resolvable `HEAD`.
- The target must contain at least one supported source file.
- Install and sign in to either Codex or Claude Code.
- CodeGraph is optional. The agent asks for permission before it installs or
  rebuilds an index; the plugin does not install software on its own.

## Installation

Both marketplaces expose the plugin as `fm-agent-plugin`.

### Codex

```bash
codex plugin marketplace add fmagent-project/FM-Agent-Skill
codex plugin add fm-agent-plugin@fm-agent-plugin
```

Start a new Codex task after installation or an update so the refreshed skills
are loaded.

### Claude Code

```bash
claude plugin marketplace add fmagent-project/FM-Agent-Skill
claude plugin install fm-agent-plugin@fm-agent-plugin
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

The public entry point is `/fm-agent:run`:

```text
/fm-agent:run [change note]
  [--submodule PATH ...]
  [--knowledge FILE ...]
  [--extra-edge FILE_OR_DIR]
  [--one-phase]
  [--isolate]
```

| Option | Purpose |
| --- | --- |
| `--submodule` | Restrict analysis to one or more subdirectories. |
| `--knowledge` | Add Markdown domain knowledge. |
| `--extra-edge` | Add static call-graph edges. |
| `--one-phase` | Generate specifications in one phase. |
| `--isolate` | Request analysis in an isolated Git worktree. |

There is normally no need to select full or incremental mode manually. The
plugin selects it from its baseline and source snapshot.

## Dispatch behavior

| State | Mode | CodeGraph behavior |
| --- | --- | --- |
| No usable baseline, or incomplete baseline artifacts | full | Requests permission before rebuilding the index when CodeGraph is selected. |
| Valid baseline and changed business source content | incremental | Requests permission before rebuilding the index when CodeGraph is selected. |
| Valid baseline and unchanged business source content | no-op | Does not inspect, rebuild, or request permission for CodeGraph. |
| Only the Git commit changed | no-op | Refreshes `observed_commit` only and retains the analysis baseline. |

The baseline separates two kinds of provenance:

- `analysis_commit`: the commit associated with the current full or
  incremental analysis result.
- `observed_commit`: the most recent commit whose source snapshot was confirmed
  to match the baseline.

As a result, analyzing uncommitted source and then committing exactly the same
content does not cause a duplicate analysis.

## CodeGraph and precision

CodeGraph is used only for a full or incremental analysis. Before use, the
agent explains that `$PROJECT/.codegraph/` will be removed and rebuilt, then
asks for permission.

- Authorized and rebuilt successfully: call-graph precision is recorded as
  `exact`.
- Unavailable or declined: `agent-static` is used with `best-effort` precision
  and a fallback reason.
- No-op: `.codegraph/` is not touched.

## Artifacts

Artifacts are written to the target project, not the plugin installation:

| Directory | Contents |
| --- | --- |
| `fm_agent/` | Function extraction, phase specifications, verification results, and FM-Agent-style bug-validation reports. |
| `fm_agent_plugin/` | Baselines, run records, locks, control indexes, precision records, incremental decisions, and isolated probes. |
| `.codegraph/` | Generated CodeGraph index, rebuilt only during authorized full or incremental runs. |

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

## Verified workflow

The following two-step workflow was verified with `cpp-demo`:

1. Run a full analysis while a source file is still uncommitted. It produced an
   exact CodeGraph call graph, a baseline snapshot, and two confirmed defects.
2. Commit the identical source content and run again. The result was no-op:
   CodeGraph was not rebuilt, `analysis_commit` was retained,
   `observed_commit` advanced to the new commit, and no active lock remained.

This verifies that re-analysis is determined by source content rather than Git
commit identity alone.

## Repository layout

```text
.
├── .agents/plugins/marketplace.json    # Codex marketplace manifest
├── .claude-plugin/marketplace.json     # Claude Code marketplace manifest
└── plugins/fm-agent-plugin/
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

它在 Git 工作区中运行，不修改业务源码。当前支持完整分析、自动增量分析和 no-op。

## 安装与使用

插件名为 `fm-agent-plugin`。安装命令：

```bash
# Codex
codex plugin marketplace add fmagent-project/FM-Agent-Skill
codex plugin add fm-agent-plugin@fm-agent-plugin

# Claude Code
claude plugin marketplace add fmagent-project/FM-Agent-Skill
claude plugin install fm-agent-plugin@fm-agent-plugin
```

安装后新建 task 或会话。在目标 Git 项目中直接请求：

```text
使用 FM-Agent 分析当前 Git 项目
```

也可以使用 `/fm-agent:run` 并附带修改说明、`--submodule`、`--knowledge`、
`--extra-edge`、`--one-phase` 或 `--isolate`。

## 运行方式与产物

- 无可用基线时执行 full；业务源码变化时自动执行 incremental。
- 源码内容未变时执行 no-op；即使只新增 Git 提交，也只更新 `observed_commit`，不会重建
  CodeGraph。
- CodeGraph 仅在 full 或 incremental 时，经授权后重建；否则记录 `agent-static` 回退。
- `fm_agent/` 保存规约、验证和缺陷报告；`fm_agent_plugin/` 保存基线、运行记录和控制状态；
  `.codegraph/` 保存生成索引。建议将三者加入目标项目的 `.gitignore`。

`analysis_commit` 表示当前分析结果对应的提交，`observed_commit` 表示最近一次确认源码快照
一致的提交。因此，先分析未提交内容、再提交相同内容不会重复分析。

## 已验证场景

在 `cpp-demo` 中已验证：首次对未提交源码执行 full 后，再提交完全相同的源码并运行，第二次会
正确返回 no-op，保留分析基线与缺陷结论，并更新 `observed_commit`。
