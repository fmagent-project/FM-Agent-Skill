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
adapter. A later adapter-mode `probe_runner.py run` writes its profile beside
that attempt rather than to this shared discovery path. Language capabilities are defined once in
`src/fm_agent_core/languages.py`: canonical names, file extensions, CodeGraph
names, Tree-sitter grammar names, span extractor, public-entry strategy, build
metadata detector, build adapter, dynamic adapter, and support level must all
come from that registry. Do not add a parallel extension or ecosystem map in a
runner. The registry separates `dynamic_adapter` (a controlled fixed-argv
adapter) from `agent_execution_ecosystems`. All registered profiles use the
same `host-project-toolchain` ecosystem in `agent-executed` mode, matching the
original FM-Agent Bug Validator: the Worker identifies Maven, CMake, NVCC,
Hvigor, Erlang/OTP, or another project toolchain from the project itself.
`dynamic_adapter` remains separate and may be absent in restricted `adapter`
mode. ArkTS has an explicit FM-Agent-compatible brace extractor because
CodeGraph does not index `.ets`; CUDA and Erlang still need their upstream
capability support before they can produce a Bug Validator candidate. Any
candidate that exists uses this same dynamic execution contract.

For static extraction, CodeGraph spans are authoritative. Without a matching
CodeGraph span, use the declared Tree-sitter grammar; Python may use its native
AST and C/C++ may use their profile-declared Clang AST when that grammar is
unavailable. ArkTS is the explicit exception: its profile declares the
FM-Agent-compatible `arkts-brace` extractor because CodeGraph has no ArkTS
backend. There is no generic regular-expression boundary fallback.

### ArkTS dependency hydration

ArkTS declares an `oh_modules` hydration policy in its LanguageProfile. Git
does not include this ignored dependency tree in the analysis snapshot, so
snapshot creation scans only project-local `oh_modules/` directories that have
an adjacent `oh-package-lock.json5` (or legacy `oh-package-lock.json`). It
parses the lockfile and checks every installed `oh-package.json5` name/version
against it, rejects symbolic links, records a lockfile SHA-256 in the snapshot
marker, then copies the verified tree into the private snapshot. Each Worker
copies that snapshot into its own attempt workspace before Hvigor runs.

This is a read-only dependency transfer, not dependency installation: it never
invokes `ohpm install`, contacts a registry, reads a global cache, or copies
`.hvigor/`. Missing, malformed, stale, or unsafe dependency trees leave static
analysis intact but require ArkTS dynamic validation to report
`unsupported/inconclusive`. Pure host-runnable utilities may be probed; code
requiring Ability, UI, device, or system-service runtime support is likewise
inconclusive and must not cause HDC/emulator setup.

For JavaScript and TypeScript, Tree-sitter extraction includes named arrow
functions declared through a `variable_declarator`, such as
`export const parse = (input) => { ... }`. The emitted span covers its owning
single declaration (including `const`/`export`) and is keyed by `parse`.
Build adapters run only fixed commands and may not be mistaken for a
reproduction.

## Fast agent-executed reproduction

For each candidate and attempt:

1. Start the same `bug_validate` job and invoke `fm-bug-validate-worker` in its
   preparation pass. It writes `reproduction.json` and `probe.<ext>` under its
   assigned `fm_agent_skill/probes/<bug-id>/attempt_<n>/` directory.
2. Validate that the contract names the current snapshot, a structured public
   entry point (`ecosystem`, `kind`, repository-relative `target`, `symbol`),
   a fixed language extension, no shell command, and fixed `CONFIRMED` /
   `NOT CONFIRMED` markers. In `agent-executed` mode every profile uses the
   registry's `host-project-toolchain` ecosystem; in `adapter` mode, it must
   equal the profile's `dynamic_adapter`. The default
   `bug_validation_execution: agent-executed` requests the same Worker in an
   execution pass. It uses the repository's real toolchain and build system,
   records every command, exit code and bounded output in
   `reproduction_result.json`, and may support any FM-Agent language. Up to
   `bug_validation_concurrency` jobs (two by default) may execute together;
   every recorded command must use a working directory below that attempt, and
   any build workspace or cache must be private to it.
3. Submit that immutable execution evidence to the executor. If it records
   `execution_error`, the executor requeues the same job; do not write a
   semantic result.
4. Otherwise invoke the same worker in its finalization pass with the immutable
   `reproduction_result.json`, then call `scheduler.py complete` with the
   matching classification.

The final report's last `attempts[]` item must have `ordinal` equal to the
current overall job attempt and must point at that exact attempt's
`reproduction_result.json`. A prior negative report never completes a retried
probe; the host state machine requests finalization again until the current
dynamic evidence is appended.

`agent-executed` is FM-Agent-compatible quick mode, not a sandbox guarantee.
The Worker may execute project-scoped commands for any registered language,
including Python, JavaScript/TypeScript, Go, Rust, Java, C/C++, CUDA, ArkTS,
and Erlang. It must select the project's actual toolchain, and when a required
SDK, device, compiler, dependency cache, or public entry point is absent it
must write `unsupported/inconclusive`, not a false negative. It must not use
`sudo`, install packages, alter Git state or
read unrelated user files; it must build only from its private attempt
workspace rather than a shared project-root output directory, and its report
must retain exact command evidence.
Use `bug_validation_execution: adapter` only when the controlled local adapter
is required: that mode uses Bubblewrap, fixed argv, no network, a read-only
project and private scratch, but recognizes fewer ecosystems.

## Host-coordinated state machine

The dynamic lifecycle is driven by `bug_validation_executor.py`, not a manual
sequence of ad-hoc runner calls. It never invokes an Agent itself. Instead it
returns an exact `host_worker` request for the active Codex/Claude Coordinator
at the preparation, execution, and finalization boundaries. The Coordinator must invoke the
named Worker through its native subagent mechanism, then return its compact
receipt to the state machine. The state machine owns admission, contract
validation, execution evidence, retry/requeue, receipt
validation, and the terminal summary.

For each job, the Coordinator must process exactly one returned action at a
time. Independent admitted Bug Validator jobs may progress concurrently. After
a preparation or execution Worker receipt, call `next`; adapter mode alone uses
`run-dynamic`; after a finalization receipt, call `submit-finalization`. Never infer completion from a
report path and never launch the next Worker for that job before the executor
returns its request.

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
