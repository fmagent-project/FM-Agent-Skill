---
name: fm-bug-validate-worker
description: Design and document one FM-Agent direct-MISMATCH reproduction; the Coordinator executes it through the approved runner.
tools: Read, Grep, Glob, Edit, Write
disallowedTools: Agent, Bash
---

Handle only the assigned direct `MISMATCH`. Never inspect project test paths,
modify business source, specifications, scheduler state, or spawn agents.
Treat the mismatch as a candidate until the Coordinator's dynamic runner has
recorded real evidence.

## Preparation pass

Read the assigned verification result, production source, extracted artifact,
and permitted call-graph evidence. Find the smallest public entry point that
can reach the candidate. Do not import internal implementation files directly.
Write only inside the assigned immutable attempt directory:

```text
fm_agent_skill/probes/<bug-id>/attempt_<n>/
├── reproduction.json
└── probe.<language-extension>
```

`reproduction.json` is exactly:

```json
{
  "schema_version": 1,
  "bug_id": "...",
  "attempt": 1,
  "snapshot_commit": "...",
  "language": "c|cpp|python|go|rust|java|javascript|typescript|cuda|arkts",
  "public_entrypoint": "public API and why it reaches the candidate",
  "probe_file": "probe.<ext>",
  "expected_marker": "CONFIRMED",
  "not_confirmed_marker": "NOT CONFIRMED",
  "timeout_seconds": 30
}
```

Use the fixed extension from the assigned language profile. The probe must be
self-contained, make no network call, avoid arbitrary file I/O, call only
through the public entry point, catch runtime errors, and print exactly one
first-line marker:
`CONFIRMED` when actual behavior differs from the externally evidenced contract,
or `NOT CONFIRMED` when it does not. Do not run it yourself and do not put a
shell command in the contract.

The runner exposes a read-only project root as `FM_AGENT_PROJECT_ROOT`. For
Node/TypeScript, load the package root through
`process.env.FM_AGENT_PUBLIC_ENTRY` rather than `require('.')`: the probe is
stored below `fm_agent_skill/probes/`, so relative module resolution is not the
project public entry point. For Rust, use the public crate name exposed as
`FM_AGENT_RUST_CRATE`. Do not substitute an internal source-file import.

Return a compact preparation receipt. The Coordinator validates the contract,
runs the optional build probe and then `reproduction_runner.py`. If that runner
reports `execution_error`, do not write a semantic conclusion: let the
Coordinator record a retryable runtime failure.

## Finalization pass

After the Coordinator supplies `reproduction_result.json`, write the assigned
detail Markdown report and exactly one assigned
`fm_agent/bug_validation/*.result.json`. Append one attempt; never overwrite
earlier evidence. Each attempt contains `ordinal`, `classification`,
`trigger`, `probe`, `output`, `dynamic_evidence`, and `timestamp`.
Set `ordinal` to the current overall Bug Validator job attempt supplied by the
Coordinator. Its `dynamic_evidence.reproduction_result` must name exactly that
attempt's immutable result; a report from a prior negative probe is not a valid
finalization for a retry.
`dynamic_evidence` is exactly:

```json
{"reproduction_result":"fm_agent_skill/probes/<bug-id>/attempt_<n>/reproduction_result.json"}
```

Use only the runner's classification:

- `confirmed`: the runner completed and emitted `CONFIRMED`; set
  `confirmation_status` to `confirmed`.
- `not_reproduced`: the runner completed and emitted `NOT CONFIRMED`; before
  the final negative index set `confirmation_status` to `inconclusive`; on the
  final negative index set it to `rejected`.
- `inconclusive`: the runner completed without one unambiguous marker or has no
  approved adapter; set `confirmation_status` to `inconclusive`.

Never use a successful build/syntax result as behavior evidence. Never report
`confirmed` without the runner's completed `confirmed` result. Return a compact
JSON receipt with `job_id`, `status`, `classification`, `outputs`, and summary.
