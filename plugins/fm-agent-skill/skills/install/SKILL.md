---
name: install
description: Set up or verify the FM-Agent analysis capability and its local deterministic tools. Use when the user asks to install, update, check prerequisites for, or enable FM-Agent analysis.
---

# FM-Agent installation

Treat installation as an environment-preparation workflow, not an analysis run.

Read [runtime-path.md](../../references/runtime-path.md) and resolve
`FM_AGENT_SKILL_ROOT` before invoking a script.

This shared skill must work in both Codex and Claude Code. Never use
`CLAUDE_SKILL_DIR`.

First identify the target Git repository.

Then run:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/preflight.py" --project "$PROJECT"
```

Verify that the target is a Git repository with a resolvable `HEAD` and that it
contains supported source files. These are the current installation checks.

For source extraction without CodeGraph, verify that the declared Tree-sitter
grammar is available to the Skill. Python may use its native AST and C/C++ may
use their profile-declared Clang AST; all other languages require Tree-sitter.
Do not enable a text or regular-expression fallback: absence of the applicable
syntax source is a prerequisite failure, not a reason to guess boundaries.

Explain any missing prerequisites.

If all prerequisite checks succeed, explain that the environment is ready to
use the `run` skill.

Never print API keys or copy `.env` contents.

Never install packages, edit configuration files, or modify provider settings
without explicit user authorization.

Do not run `main.py` as a substitute for this Skill.

The Skill's entry points orchestrate the FM-Agent workflow.

The scripts are implementation tools that manage state, artifacts, and
validation.
