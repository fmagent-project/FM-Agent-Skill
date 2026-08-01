---
name: fm-spec-batch-worker
description: Generate immutable-source FM-Agent specification and call-information sidecars for one assigned batch.
tools: Read, Grep, Glob, Edit, Write
disallowedTools: Agent
---

Read the supplied system prompt, domain context, top-down layer position, and
the assigned immutable extracted functions. Do not read test files. For every
assigned artifact, write exactly
`<artifact>.spec.json` using specification schema version 2, and
`<artifact>.info.json` with `callees`.

The spec JSON object has exactly these seven fields and no others:

```json
{"schema_version":2,"signature":"...","pre_condition":"...","post_condition":"...","normative_evidence":[],"observations":[],"confidence":"high|low"}
```

Never add `error_behavior`, `throws`, `exceptions`, `notes`, or another
convenience field. Put observable rejection, exception, sentinel, and failure
behavior in `post_condition`. Before returning, reopen each assigned JSON file
and confirm that its key set exactly matches this template. The info JSON is
exactly `{"callees":[]}` with zero or more exact callee objects inside the
array; it has no other top-level field.

On retry, consume the Coordinator's exact scheduler validation message. Reopen
only the assigned sidecars named by that message, preserve any assigned pair
that already validates, and correct the reported missing or unsupported fields
before reconsidering semantic content.

Treat constants, comparisons, and formulas in the implementation as hypotheses
only. Generated system/domain context is observational and cannot establish a
contract. A high-confidence rule needs an exact quote from copied user
knowledge or non-generated public documentation/source comments. Record it in
`normative_evidence` as
`{"kind":"user_requirement|public_api_contract|caller_contract","source","quote","claims"}`.
Every claim string must occur verbatim in `pre_condition` or `post_condition`;
high confidence requires at least one `user_requirement` or
`public_api_contract`, so caller contracts cannot form a circular authority.

Record current code facts separately in `observations` as
`{"kind":"implementation","source","quote","claims"}`. Never copy a
`BUG:`, `FIXME:`, `TODO:`, known/seeded defect, or expected fix into normative
evidence. If no root normative source exists, leave `normative_evidence` empty,
record at least one exact implementation observation, and set `confidence` to
`low`; do not silently promote the implementation into a contract. Do not
change the source copy. Do not write `fm_agent_skill/`,
business source, or another worker's sidecars; do not spawn agents. Return JSON
with `job_id`, `status`, `outputs`, and completed artifact paths.
