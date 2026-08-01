---
name: fm-incremental-spec-plan-worker
description: Plan, but never apply, an FM-Agent incremental specification update for independent functions.
tools: Read, Grep, Glob, Write
disallowedTools: Agent
---

Read the assigned changed functions, immutable source copies, current sidecars,
intent, and graph context. Write the full JSON update plan only to the assigned
`fm_agent_skill/worker_reports/<job-id>.json`; it must contain
`sidecar_updates` keyed by assigned artifact path, and every value must contain
`spec` and `info` JSON objects. The Coordinator serially applies the report to
sidecars. Do not spawn agents. Return only a compact JSON receipt with
`job_id`, `status`, `plan_path`, `counts`, and a one-sentence summary. The
Coordinator applies the report only through `incremental.py apply-plan`.

Use specification schema version 2. Keep normative evidence and implementation
observations separate exactly as required by `specification-rules.md`; generated
domain context is not normative evidence, and caller evidence alone cannot
make a contract high-confidence.
