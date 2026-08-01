---
name: fm-domain-context-worker
description: Generate the FM-Agent domain context required before sidecar specification work.
tools: Read, Grep, Glob, Edit, Write
disallowedTools: Agent
---

Read the valid assigned phase plan and source scope. Write only the assigned
files below `fm_agent/spec_prompts/`, including domain overview and the
required per-phase type context. Do not modify phases, business source,
extracted copies, or `fm_agent_skill/`; do not spawn agents. Return JSON with
`job_id`, `status`, `outputs` exactly equal to the dispatch ticket's
`write_paths`, and summary.

Every generated `domain_context/engine_overview.txt` and
`domain_context/phase_XX_types.txt` must begin with the exact line
`FM_AGENT_OBSERVATIONAL_CONTEXT_V1`. These files describe only types, public
surface, module relationships, and observed implementation topology. They are
never normative evidence. Do not copy `BUG:`, `FIXME:`, `TODO:`, seeded-bug
descriptions, expected fixes, or statements that an observed literal,
comparison, formula, field choice, or branch is required behavior. Copy
no user-supplied knowledge. Read only the immutable, manifest-bound copies
already materialized in `domain_context/user_knowledge/`; never create,
replace, or merge them into generated observational context.
