---
name: help
description: Explain how to use the FM-Agent plugin, including its available skills, automatic full/incremental/no-op selection, explicit resume behavior, artifact locations, cross-client behavior, and current capabilities. Use when the user asks what FM-Agent can do, how to start, resume, or configure it, where results live, or why it selected a mode.
---

# FM-Agent help

Answer in the user's language. Explain only; do not start analysis, change
configuration, create a baseline, or modify project files. Direct a request
for current run state or a particular finding to `diagnose` instead.

## Explain the available skills

State that the plugin exposes these five skills:

| Skill | Use it for |
| --- | --- |
| `run` | Analyze a Git project and automatically select full, incremental, or no-op; explicitly resume an interrupted full or incremental run. |
| `install` | Check prerequisites and explain missing setup. |
| `config` | Save non-secret defaults such as scope, retries, knowledge files, and supplemental edges. |
| `diagnose` | Read the latest run state, summary, or a stored bug report without starting another analysis. |
| `help` | Explain workflow, usage, boundaries, and current capabilities. |

Do not promise a particular slash-command spelling: Codex and Claude Code may
present the same shared skills through different user interfaces. Give natural
language examples such as “analyze the current Git project with FM-Agent” or
“configure FM-Agent to analyze only `backend/`”.

## Explain automatic mode selection

Explain that users do not choose a low-level full or incremental flag. The
`run` skill deterministically inspects the baseline and chooses:

| Condition | Mode |
| --- | --- |
| No compatible successful baseline | Full analysis |
| Compatible baseline and no supported source-file change | No-op |
| Compatible baseline and supported source-file change | Incremental analysis |

A baseline is reusable only when its scope and configuration fingerprint,
function artifacts, specifications, verification results, and Git commit remain
compatible. A scope, knowledge-file, or supplemental-edge change deliberately
causes full analysis.

A source-file change means an added, modified, or deleted file whose extension
is supported by the plugin. Documentation-only changes do not start an
incremental analysis.

## Explain resume

Explain that resume is explicit: the user asks to continue an interrupted
analysis or supplies `--resume`. It preserves the run id and starts at the
first incomplete stage. It is accepted only when supported-source content and
the run's saved scope, knowledge files, supplemental edges, and other effective
configuration still match. A commit containing identical source content does
not prevent resume.

When a lock has a fresh heartbeat, explain that another agent may still be
working. Do not claim a takeover occurred unless the user explicitly confirmed
it. A completed run, changed source, or changed analysis inputs requires a new
ordinary analysis instead. Direct users to `diagnose` for the resumability
state and reason.

## Explain output and boundaries

Explain the artifact boundary:

- `fm_agent/` contains FM-Agent-compatible analysis artifacts: phases, extracted
  function copies, top-down layers, verification results, and any bug-validation output.
- `fm_agent_plugin/` contains plugin control state: saved configuration, locks,
  run records, baseline fingerprints, analysis indexes, graph precision, incremental
  decisions, and isolated probe builds.
- Project business source is never annotated with `[SPEC]` or `[INFO]`.

Explain that the same `skills/`, `scripts/`, and `references/` are shared by
Codex and Claude Code. Their manifests only provide discovery; each skill
resolves its plugin root from its own `SKILL.md` location as defined in
[runtime-path.md](../../references/runtime-path.md).

Explain that `run` detects optional CodeGraph support before analysis. When it
is available, full and incremental analysis automatically remove and regenerate
the derived CodeGraph index without requesting authorization. The plugin does
not install unavailable software; it records a best-effort static call graph
rather than claiming exact graph precision.

## Explain the current capabilities

Currently, the plugin supports a full `run_pipeline`, automatic baseline-driven
full/incremental/no-op selection, and safe explicit resume of an interrupted
full or incremental run.

Scripts coordinate plugin state and validate artifacts.

The coding agent performs repository understanding, specification writing, and reasoning during the `run` workflow.

Describe and execute only capabilities documented by this plugin's shared skills and references. When uncertain, state the documented current behavior rather than inferring features from the original FM-Agent project.
