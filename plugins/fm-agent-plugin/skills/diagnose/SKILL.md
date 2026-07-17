---
name: diagnose
description: Inspect FM-Agent run state, Bug Validator summaries, incremental decisions, and a specified bug report without starting another analysis.
---

# FM-Agent diagnosis

Read [runtime-path.md](../../references/runtime-path.md) and resolve
`FM_AGENT_PLUGIN_ROOT` before invoking a script. This shared skill must work in
both Codex and Claude Code; never use `CLAUDE_SKILL_DIR`.

Read existing artifacts only:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/diagnose.py" --project "$PROJECT" --bug-id "$BUG_ID"
```

When no id is given, omit `--bug-id`. Summarize the latest mode, baseline commit,
analysis scope, candidate count, confirmed count, and run state. For an individual
report, distinguish `MISMATCH`, candidate, and confirmed states. Read
[bug-validation.md](../../references/bug-validation.md) before interpreting or
reporting a result.
