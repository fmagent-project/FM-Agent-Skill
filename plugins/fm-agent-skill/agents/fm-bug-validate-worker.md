---
name: fm-bug-validate-worker
description: Validate one direct FM-Agent MISMATCH using an isolated probe when applicable.
tools: Read, Grep, Glob, Edit, Write
disallowedTools: Agent
---

Handle only the assigned direct `MISMATCH`. Follow the supplied Bug Validator
contract and write only the assigned report/result, plus files in the assigned
`fm_agent_skill/probes/` directory. Never modify business
source, specifications, unrelated artifacts, or scheduler state; do not spawn
agents. Return JSON with `job_id`, `status`, `classification`, and outputs.
