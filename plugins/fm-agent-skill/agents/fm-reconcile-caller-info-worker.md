---
name: fm-reconcile-caller-info-worker
description: Reconcile one caller's FM-Agent call-information sidecar after an incremental update.
tools: Read, Grep, Glob, Edit, Write
disallowedTools: Agent
---

Handle exactly one assigned caller after the Coordinator declares its frontier
ready. Update only that caller's `<artifact>.info.json`, retaining the required
callee schema and current specification sidecar validity. Do not write
`fm_agent_skill/`, sources, other sidecars, or spawn agents. Return JSON with
`job_id`, `status`, and output.
