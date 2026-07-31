# Bug validation

A direct reasoning `MISMATCH` is a candidate, never a confirmed defect. Do not
read project tests or run the full test suite. Instead, reproduce the candidate
through the production package's public entry point with one minimal generated
probe. `DEPENDENCY_RISK` is not a bug candidate.

## Evidence levels

Code/spec reasoning creates the candidate. A build or syntax probe only checks
that a snapshot is buildable; it is not behavioral evidence. Only an executed
minimal probe whose actual output differs from the externally evidenced contract
may produce `confirmed`.

Before the first candidate, run `probe_runner.py detect`; its profile at
`fm_agent_skill/control/build_profile.json` selects an optional safe build
adapter. Language capabilities are defined once in
`src/fm_agent_core/languages.py`: CodeGraph names, Tree-sitter grammar names,
file extensions, build ecosystems, runtime ecosystems, and support level must
all come from that registry. Erlang remains an explicit ELP capability plugin.
Build adapters run only fixed commands and may not be mistaken for a
reproduction.

## Controlled dynamic reproduction

For each candidate and attempt:

1. Start the same `bug_validate` job and invoke `fm-bug-validate-worker` in its
   preparation pass. It writes `reproduction.json` and `probe.<ext>` under its
   assigned `fm_agent_skill/probes/<bug-id>/attempt_<n>/` directory.
2. Validate that the contract names the current snapshot, a public entry point,
   a fixed language extension, no shell command, and fixed `CONFIRMED` /
   `NOT CONFIRMED` markers. Run the optional `probe_runner.py run` build check.
3. The Coordinator, not a worker, runs:

   ```bash
   <python3> "$FM_AGENT_SKILL_ROOT/scripts/reproduction_runner.py" run \
     --project "$PROJECT" --bug-id "$BUG_ID" --attempt "$JOB_ATTEMPT"
   ```

4. If the runner returns `execution_error`, call `scheduler.py fail` with
   `execution` and requeue the same job. Do not write a semantic result.
5. Otherwise invoke the same worker in its finalization pass with the immutable
   `reproduction_result.json`, then call `scheduler.py complete` with the
   matching classification.

The final report's last `attempts[]` item must have `ordinal` equal to the
current overall job attempt and must point at that exact attempt's
`reproduction_result.json`. A prior negative report never completes a retried
probe; the host state machine requests finalization again until the current
dynamic evidence is appended.

The runner accepts no Agent-provided command. It executes only an approved
ecosystem adapter inside Bubblewrap with a read-only project mount, private
temporary directory, timeout, disabled network, cleared environment, and no
mount of `/`, `/home`, or host configuration directories. Runtime binaries must
be provisioned under an approved system/runtime prefix; a runtime under a user
home directory is `unsupported`, rather than making that home visible. Python,
JavaScript, Go, and Cargo/Rust have Coordinator-owned adapters; TypeScript needs
the approved `tsx` runtime. Java (Maven/Gradle), C/C++ (CMake), CUDA, ArkTS and
ELP remain intentionally `unsupported` until their dedicated adapter validates
its project metadata and public entrypoint. Never substitute a guessed shell
command or run unsandboxed merely to improve coverage.

## Host-coordinated state machine

The dynamic lifecycle is driven by `bug_validation_executor.py`, not a manual
sequence of ad-hoc runner calls. It never invokes an Agent itself. Instead it
returns an exact `host_worker` request for the active Codex/Claude Coordinator
at the preparation and finalization boundaries. The Coordinator must invoke the
named Worker through its native subagent mechanism, then return its compact
receipt to the state machine. The state machine owns admission, contract
validation, optional build evidence, sandbox execution, retry/requeue, receipt
validation, and the terminal summary.

The probe must be self-contained, avoid network access and unrelated file I/O,
use a public entry point rather than an internal module, catch errors, and print
one unambiguous marker. `reproduction_result.json` retains its command, exit
code, stdout, stderr, source snapshot, and classification.

## Classification and retry

| Dynamic runner result | Worker receipt | Final report status |
| --- | --- | --- |
| completed `CONFIRMED` | `confirmed` | `confirmed`; stop |
| completed `NOT CONFIRMED` | `not_reproduced` | `inconclusive` until the last negative attempt, then `rejected` |
| completed unsupported/ambiguous output | `inconclusive` | `inconclusive` |
| timeout, command/tool failure, missing result | no receipt | retryable runtime failure |

Runtime failure retries up to `bug_validation_max_attempts` (default five).
A completed `not_reproduced` or `inconclusive` attempt repeats under the same
job ID until `bug_validation_negative_retries + 1` attempts (default three),
preserving every immutable attempt. Vary only legal inputs or trigger conditions;
keep the candidate identity and source snapshot fixed. A build failure is runtime
probe evidence only; it cannot reject or confirm a semantic candidate.

For incremental analysis, consider only direct `MISMATCH` results whose
`function_id` is in `incremental_decision.json.included`. Replace the current
Coordinator-authored `summary.json` with the current snapshot commit and exact
candidate/confirmed/rejected/inconclusive counts; never reuse older reports or
probes as current confirmation:

```bash
<python3> "$FM_AGENT_SKILL_ROOT/scripts/bug_summary.py" \
  --project "$PROJECT" --mode "$MODE"
```
