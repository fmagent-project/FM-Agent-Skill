# Full pipeline

Follow the listed order. Start and complete every phase through `pipeline.py`;
the corresponding gate in [stage-gates.md](stage-gates.md) is the acceptance
authority.

1. `preflight`: validate Git, source files, merged settings, and lock ownership.
2. `project_understanding`: dispatch `fm-phase-plan-worker`, then run `executor.py normalize-phases --project "$PROJECT"` before gating native-style `phases.json`. It writes `modules[].source_files` and `depends_on_phases`, rejects missing sources, and excludes test paths from all analysis. Separate headers/types, implementation, and entry points when their dependencies differ: implementation depends on headers/types and CLI/entry points depend on implementation. Name each phase after functions assigned to that phase's source files, not functions that merely call into it; a phase must not claim ownership of a REPL, parser, or other function assigned to another phase.
3. `phase_cleanup`: remove empty/duplicate phases without expanding the selected scope. Its `pipeline.py phase-start` transition automatically preserves the current `phases.json` and removes old derived extraction, specification, verification, Bug Validator, trace, and Skill-control artifacts. A full run must not reuse semantic outputs from an earlier run.
4. `extraction`: when CodeGraph is selected, first run `codegraph.py export --output "$PROJECT/fm_agent_skill/control/codegraph_export.json"`, then run `executor.py extract --project "$PROJECT" --codegraph-export "$PROJECT/fm_agent_skill/control/codegraph_export.json"` (plus every selected `--submodule`). Otherwise omit that option. Extraction uses mapped CodeGraph spans first, Tree-sitter when installed, then the profile-declared language-specific fallback. ArkTS deliberately uses its FM-Agent-compatible `arkts-brace` extractor because CodeGraph does not support `.ets`. It writes immutable function copies and the `fm_agent_skill` control inventory without calling FM-Agent. Never analyze test paths, emit a pseudo-function for declaration-only headers, hash-name artifacts, or write inline `[SPEC]`/`[INFO]` into copied source.
5. `call_graph`: when CodeGraph is selected, reuse that same export with `executor.py graph --project "$PROJECT" --codegraph-export "$PROJECT/fm_agent_skill/control/codegraph_export.json"`. It maps exported nodes/edges to current extracted artifacts and records `exact` only then. Without CodeGraph, dispatch `fm-agent-static-edge-worker`, validate its `fm_agent/agent_static_edges_candidate.json` through `executor.py record-agent-edges`, then rerun `executor.py graph`; only validated edges are used and the backend remains `agent-static/best-effort`.
6. `specification`: after knowledge materialization, run `job_planner.py --phase specification` once, then `semantic_executor.py prepare`. It registers domain context plus every function across all phases/layers before execution, using caller-first dependencies and batches of up to eight by default. Dispatch only exact tickets for `fm-domain-context-worker` and `fm-spec-batch-worker`; never generate a Workflow script. Each Worker follows original FM-Agent semantics: derive intended condition B from domain/interface/caller relations before inspecting implementation details. Same-layer batches may run concurrently; each writes native three-field `.spec.json` and native `.info.json` sidecars without changing source copies. The deterministic boundary strips harmless legacy/identity fields, reports every invalid pair in the batch at once, and redispatches only rejected pairs.
7. `verification`: run `job_planner.py --phase verification` once, then `semantic_executor.py prepare`. Dispatch one exact `fm-verify-function-worker` ticket for every pending function; the executor never substitutes a confidence shortcut. The Worker independently derives actual postcondition A and performs FM-Agent's structured A→B check against the native spec B. Mark a direct local violation `MISMATCH` only with a concrete counterexample and exact source quote; mark callers affected only through an invalid callee `DEPENDENCY_RISK`, not `MISMATCH`. Retry malformed output and never treat it as semantic evidence. Do not pass the phase when fewer than half the scope reaches MATCH/MISMATCH or any result is ERROR.
8. `bug_validation`: run `job_planner.py --phase bug_validation` once, then start every registered direct-`MISMATCH` `fm-bug-validate-worker` job. Up to two independent jobs may run at once by default. The preparation pass writes a reviewed public-entrypoint probe contract in its immutable attempt directory. In default `agent-executed` mode, every registered language uses one `host-project-toolchain` Worker execution pass: it records a real public-entry probe using only an attempt-local workspace and cache, then the Worker finalization pass consumes that immutable evidence. The Worker selects the concrete project command; a missing SDK, device, compiler, cache, or public entry produces `unsupported/inconclusive`. In optional `adapter` mode, the Coordinator may instead run `probe_runner.py run` and `reproduction_runner.py run`; their build profile and scratch are attempt-private. A build/syntax result cannot confirm a defect. An execution error requeues the same job. Write reports and a current summary only when direct candidates exist.
9. `finalize`: write summaries and a successful baseline only after every previous gate passed.

If a phase fails, record `phase-fail`, retain its outputs, then run
`pipeline.py fail`, which releases the owned lock. Retrying is bounded by the configured retry count; do not
silently bypass a missing artifact. A failed pipeline has no FM-Agent bug
result. Do not replace the failed phase or any later phase with direct source
auditing, and do not report static suspicions as findings from this run.

## Resume

For an explicit resume, read [resume-contract.md](resume-contract.md). Start
at the active analysis's first incomplete phase, not at `preflight`. Keep all
earlier gate-validated work. Within the resumed phase, retain only artifacts
from the saved snapshot commit and create missing work. Do not repeat
`phase_cleanup` after it has succeeded. Reuse a readable same-snapshot
CodeGraph index only when resuming the incomplete `call_graph` phase.

The host Coordinator dispatches the semantic work to the named workers
instead of calling FM-Agent's original remote-LLM pipeline. Read
[agent-orchestration.md](agent-orchestration.md) and
[subagent-scheduler.md](subagent-scheduler.md) before beginning.

When snapshot creation finds ArkTS source, it additionally performs the
LanguageProfile-declared, read-only dependency hydration: project-local,
lock-bound `oh_modules/` is copied into the private snapshot while `.hvigor/`
is excluded. A missing or unsafe tree is recorded as unavailable without
blocking static analysis; the later dynamic Worker must be inconclusive rather
than install dependencies or use HDC.
