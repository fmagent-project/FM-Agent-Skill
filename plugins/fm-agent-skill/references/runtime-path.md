# Cross-client runtime path

The shared skills work in both Codex and Claude Code.  Before invoking a
Skill script, resolve `FM_AGENT_SKILL_ROOT` as the directory containing both
`skills/` and `scripts/`.  The loaded skill is always at:

```text
<skill-root>/skills/<skill-name>/SKILL.md
```

Use the location of the currently loaded `SKILL.md` to locate the directory containing
both `skills/` and `scripts/`. That directory is `FM_AGENT_SKILL_ROOT`.

Invoke shared tools as:

```text
<python3> <skill-root>/scripts/<tool>.py ...
```

where `<python3>` is the host's Python 3 command (`python3` or `python`).
It is a placeholder in Skill examples, not literal shell syntax.
