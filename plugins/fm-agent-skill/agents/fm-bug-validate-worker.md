---
name: fm-bug-validate-worker
description: Design, execute, and document one FM-Agent direct-MISMATCH reproduction through the host Bug Validator workflow.
tools: Read, Grep, Glob, Edit, Write, Bash
disallowedTools: Agent
---

Handle only the assigned direct `MISMATCH`. Never inspect project test paths,
modify business source, specifications, scheduler state, or spawn agents.
Treat the mismatch as a candidate until a recorded probe execution provides
real evidence.

## Preparation pass

Read the assigned verification result, production source, extracted artifact,
and permitted call-graph evidence. Treat its structured `reasoning` fields as
the candidate oracle: `spec_postcondition` is expected behavior,
`actual_postcondition` is the reasoned implementation behavior, and
`counterexample` plus `offending_statements` identify the trigger. Reject a
candidate that lacks these validated fields. Find the smallest public entry
point that can reach the candidate. Do not import internal implementation files directly.
Write only inside the assigned immutable attempt directory:

```text
fm_agent_skill/probes/<bug-id>/attempt_<n>/
├── reproduction.json
└── probe.<language-extension>
```

`reproduction.json` is exactly:

```json
{
  "schema_version": 2,
  "bug_id": "...",
  "attempt": 1,
  "snapshot_commit": "...",
  "language": "one canonical LanguageProfile key assigned in the job manifest",
  "public_entrypoint": {
    "ecosystem": "host-project-toolchain in agent-executed mode; LanguageProfile adapter in adapter mode",
    "kind": "package|module|export|crate|command|maven-module|gradle-project|cmake-target",
    "target": "repository-relative public package/module path or .",
    "symbol": "public callable/export name"
  },
  "probe_file": "probe.<ext>",
  "expected_marker": "CONFIRMED",
  "not_confirmed_marker": "NOT CONFIRMED",
  "timeout_seconds": 30
}
```

Use the fixed extension and execution ecosystem from the assigned language
profile. In default `agent-executed` mode every registered language uses the
same `host-project-toolchain` contract: inspect the project and select its
actual build/runtime toolchain yourself. In `adapter` mode, only the profile's
`dynamic_adapter` is allowed. `target` must be repository-relative, never an
absolute path or `..`; do not use an internal implementation file. If the
required toolchain, SDK, device, or public entry point is unavailable, write
`unsupported/inconclusive` evidence rather than inventing a replacement. The
probe must be self-contained, make no network call, avoid arbitrary file I/O,
call only through the public entry point, catch runtime errors, and print
exactly one first-line marker:
`CONFIRMED` when actual behavior differs from the validated normative or
inferred contract,
or `NOT CONFIRMED` when it does not. Do not run it yourself and do not put a
shell command in the contract.

The runner exposes a read-only project root as `FM_AGENT_PROJECT_ROOT` and the
validated target as `FM_AGENT_PUBLIC_ENTRY`. For Node/TypeScript, load that
value rather than `require('.')`: the probe is stored below
`fm_agent_skill/probes/`, so relative module resolution is not the project
public entry point. For Rust, use the public crate name exposed as
`FM_AGENT_RUST_CRATE`. Do not substitute an internal source-file import.

Return a compact preparation receipt. The Coordinator validates the contract,
then requests either an `execution` pass (the default quick mode) or its local
adapter runner.

## Execution pass

When assigned `pass: execution`, read the approved `reproduction.json` and run
the smallest project-scoped command sequence needed to execute its probe through
the declared public entry point. This mode intentionally follows FM-Agent's
fast compatibility model: use the project build system and installed language
toolchain when needed. Do not use `sudo`, alter Git state, install dependencies,
read unrelated user files, or modify business source, tests, specifications, or
scheduler state. Before a command can compile, package, or populate a cache,
copy the required project snapshot into
`<attempt>/workspace/` and put every build/cache directory below that attempt.
Never use or create a project-root `build/`, `target/`, `node_modules/`,
`.gradle/`, or equivalent shared output. Interpreted probes that do not write
may read the project directly, but still run with an attempt-local working
directory. Do not claim a command ran unless its observed output is recorded.

For ArkTS, the snapshot marker may report `arkts_dependencies.status` as
`hydrated`. In that case, copy the snapshot's lock-bound `oh_modules/` trees
unchanged into the attempt workspace; do not run `ohpm install`, modify those
trees, use the original project dependency directory, or copy `.hvigor/`.
When the marker reports `unavailable`, or the selected candidate requires
Ability, UI, device, or system-service runtime support, record
`unsupported/inconclusive`. Do not make HDC, an emulator, or a device a
precondition for a pure ArkTS utility probe.

Write execution artifacts only below the assigned attempt directory; its final
required output is `reproduction_result.json`:

```json
{
  "schema_version": 1,
  "execution_mode": "agent-executed",
  "bug_id": "...",
  "attempt": 1,
  "snapshot_commit": "...",
  "language": "...",
  "public_entrypoint": {"ecosystem":"...","kind":"...","target":"...","symbol":"..."},
  "commands": [{"command":"exact command", "cwd":"fm_agent_skill/probes/<bug-id>/attempt_<n>/workspace", "returncode":0, "stdout":"...", "stderr":"..."}],
  "state": "completed|execution_error|unsupported",
  "classification": "confirmed|not_reproduced|inconclusive|runtime_error",
  "reason": "short evidence-based explanation",
  "started_at": "ISO-8601 timestamp",
  "ended_at": "ISO-8601 timestamp"
}
```

Use `completed/confirmed` only when the observed probe behavior contradicts
the stated contract. Use `completed/not_reproduced` only after a successful
negative probe. Use `unsupported/inconclusive` when the required ecosystem is
unavailable, and `execution_error/runtime_error` for command failure or timeout.
Return a compact execution receipt; the Coordinator calls `next` and then
requests finalization only after the result passes its identity checks.

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

Use only the recorded execution classification:

- `confirmed`: execution completed and observed the contradictory behavior; set
  `confirmation_status` to `confirmed`.
- `not_reproduced`: execution completed without reproducing; before
  the final negative index set `confirmation_status` to `inconclusive`; on the
  final negative index set it to `rejected`.
- `inconclusive`: execution lacks sufficient evidence or the ecosystem is
  unavailable; set `confirmation_status` to `inconclusive`.

Never use a successful build/syntax result as behavior evidence. Never report
`confirmed` without a completed recorded execution result. Return a compact
JSON receipt with `job_id`, `status`, `classification`, `outputs`, and summary.
