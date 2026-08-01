# Stage gates

| Phase | Inputs | Required output / acceptance | Failure behavior |
| --- | --- | --- | --- |
| project understanding | scope, source inventory | native-style `phases.json` with modules and dependencies | retry; then fail |
| extraction | phases, language extractors | non-empty `extracted_functions/`, Skill control analysis index | no functions/index failure: fail |
| call graph | control analysis index, optional edges | native caller-first layer files and Skill precision record | tool error: fail; fallback must be recorded |
| specification | layers, batch manifest, observational domain context | oracle-free `system_prompt.md`, marked engine/phase context, and every current source's schema-v3 paired `.spec.json` / `.info.json`; intended B uses normative or inferred evidence independent of implementation observations | bounded retry; then fail |
| verification | ready functions | schema-v2 result envelope per current function/snapshot; every pending function runs a Worker; MATCH/MISMATCH require structured A→B reasoning against normative or inferred B, and MISMATCH additionally requires a concrete counterexample plus exact source quote | malformed output retries; any ERROR blocks the gate; fewer than half independent contracts or fewer than half conclusive outcomes returns `insufficient_specification` |
| bug validation | direct `MISMATCH` results | one current-snapshot dynamic result per candidate, report/result, and `summary.json`; build-only evidence cannot promote a candidate; no artifact required without candidates | runtime failure retries; unsupported evidence remains inconclusive |

Incremental gates additionally require Skill-control preserved-spec, function-diff,
and inclusion/exclusion records. `pipeline.py phase-complete` invokes
these checks; state cannot advance on a failed gate.
