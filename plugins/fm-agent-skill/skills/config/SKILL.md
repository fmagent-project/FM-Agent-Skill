---
name: config
description: View, update, and reset persistent FM-Agent analysis defaults for scope, execution, graph backends, and knowledge references.
---

# FM-Agent configuration

Configuration lives at `fm_agent_plugin/config.json` in the target repository.
It holds defaults for submodules, one-phase and isolation policy, concurrency,
granularity, retry count, lock TTL, the CodeGraph command and selected graph
backend, extra-edge reference, and Markdown knowledge references. It never
copies API keys or knowledge file contents.

Read [runtime-path.md](../../references/runtime-path.md) and resolve
`FM_AGENT_PLUGIN_ROOT` before invoking a script. This shared skill must work in
both Codex and Claude Code; never use `CLAUDE_SKILL_DIR`.

Use `config.py show`, `set`, or `reset`; for example:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/config.py" set --project "$PROJECT" --submodule src --concurrency 8 --granularity 40
```

Validate an extra-edge file or directory before saving or using it:

```bash
<python3> "$FM_AGENT_PLUGIN_ROOT/scripts/call_graph_edges.py" "$EDGE_FILE"
```

Each run merges saved defaults with its explicit parameters, with explicit
parameters winning. The merged scope, policy settings, knowledge hashes, and
file-or-directory extra-edge content hash form the baseline fingerprint; a
change requires a full analysis. Read [configuration.md](../../references/configuration.md).
