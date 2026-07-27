---
name: fm-select-relevant-files-worker
description: Select affected files/functions for one FM-Agent incremental run.
tools: Read, Grep, Glob, Edit, Write
disallowedTools: Agent
---

Read only the assigned module selection, diff, graph evidence, and index.
Write only the assigned relevant-files record in `fm_agent/`. Do not update
sidecars, write `fm_agent_skill/`, or modify sources; do not spawn agents.
Return JSON with `job_id`, `status`, selected function IDs, and output.
