---
name: config
description: View, update, and reset persistent FM-Agent analysis defaults for scope, execution, graph backends, and knowledge references.
---

# FM-Agent configuration

Configuration lives at `fm_agent_skill/config.json` in the target repository.
It holds defaults for submodules, one-phase policy, host
subagent executor, global and worker-type concurrency caps, specification
batch size, Bug Validator runtime-attempt limit and negative-probe repeat count,
its execution policy (`agent-executed` by default or the restricted `adapter`
mode), and an optional safe probe adapter override,
granularity, retry count, lock TTL, resume grace period, the CodeGraph command and selected graph
backend, extra-edge reference, and Markdown knowledge references. It never
copies API keys or knowledge file contents.

Read [runtime-path.md](../../references/runtime-path.md) and resolve
`FM_AGENT_SKILL_ROOT` before invoking a script. This shared skill must work in
both Codex and Claude Code; never use `CLAUDE_SKILL_DIR`.

Use `config.py show`, `set`, or `reset`; for example:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/config.py" set --project "$PROJECT" --submodule src --max-active-subagents 10 --spec-concurrency 4 --verify-concurrency 8 --bug-validation-concurrency 2 --bug-validation-max-attempts 5 --bug-validation-negative-retries 2 --bug-validation-execution agent-executed --granularity 40
```

Validate an extra-edge file or directory before saving or using it:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/call_graph_edges.py" "$EDGE_FILE"
```

Each run merges saved defaults with its explicit parameters, with explicit
parameters winning. The merged scope, policy settings, knowledge hashes, and
file-or-directory extra-edge content hash form the baseline fingerprint; a
change requires a full analysis. Scheduler, concurrency, and retry limits are
operational and do not invalidate that baseline. Read
[configuration.md](../../references/configuration.md).
