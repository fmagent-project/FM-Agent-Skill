# Stage gates

| Phase | Inputs | Required output / acceptance | Failure behavior |
| --- | --- | --- | --- |
| project understanding | scope, source inventory | native-style `phases.json` with modules and dependencies | retry; then fail |
| extraction | phases, language extractors | non-empty `extracted_functions/`, Skill control analysis index | no functions/index failure: fail |
| call graph | control analysis index, optional edges | native caller-first layer files and Skill precision record | tool error: fail; fallback must be recorded |
| specification | layers, batch manifest, observational domain context | oracle-free `system_prompt.md`, marked engine/phase context, and every current source's native three-field FM-Agent `.spec.json` plus `.info.json`; intended B is derived before implementation behavior A | deterministic conversion of harmless extra fields, then bounded repair of only invalid pairs |
| verification | ready functions | schema-v2 result envelope per current function/snapshot; every pending function runs a Worker; MATCH/MISMATCH require structured A→B reasoning against native spec B, and MISMATCH additionally requires a concrete counterexample plus exact source quote | malformed output retries; any ERROR blocks the gate; fewer than half conclusive outcomes returns `insufficient_specification` |
| bug validation *(optional)* | direct `MISMATCH` results | one current-snapshot dynamic result per candidate, report/result, and `summary.json`; build-only evidence cannot promote a candidate; no artifact required without candidates. Skipped by default; run only via `--validate FUNCTION_ID`. | runtime failure retries; unsupported evidence remains inconclusive |

Incremental gates additionally require Skill-control preserved-spec, function-diff,
and inclusion/exclusion records. `pipeline.py phase-complete` invokes
these checks; state cannot advance on a failed gate.

## Findings classification

| Classification | Source | Meaning |
| --- | --- | --- |
| `static_finding` | Verification `MISMATCH` | High-confidence static identification with concrete counterexample and source quote; not dynamically confirmed |
| `confirmed` | Bug Validation `CONFIRMED` probe | Dynamically reproduced through the public entry point |
| `rejected` | Bug Validation exhausted negative probes | Static candidate not reproduced after sufficient dynamic attempts |
| `inconclusive` | Bug Validation with missing toolchain/evidence | Cannot be confirmed or rejected with available runtime |
