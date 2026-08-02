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

Use FM-Agent's native three-field specification object: exactly `signature`,
`pre_condition`, and `post_condition`. Preserve FM-Agent's intended-behavior
reasoning: derive B from user/domain/interface/caller/paired-API/type signals
before inspecting implementation behavior A. Generated domain context guides
inference but is not itself a contract, and implementation details must not be
copied into B.
