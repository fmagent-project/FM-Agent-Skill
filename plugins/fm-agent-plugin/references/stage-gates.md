# Stage gates

| Phase | Inputs | Required output / acceptance | Failure behavior |
| --- | --- | --- | --- |
| project understanding | scope, source inventory | native-style `phases.json` with modules and dependencies | retry; then fail |
| extraction | phases, language extractors | non-empty `extracted_functions/`, plugin control analysis index | no functions/index failure: fail |
| call graph | control analysis index, optional edges | native caller-first layer files and plugin precision record | tool error: fail; fallback must be recorded |
| specification | layers, batch manifest | every current artifact has paired `[SPEC]` and `[INFO]` | bounded retry; then fail |
| verification | ready functions | parseable result per current function, hash-aligned; direct mismatch separated from dependency risk | record per-function `ERROR`, continue |
| bug validation | direct `MISMATCH` results | isolated probe build, finding records, result/report, `summary.json`; no artifact required without candidates | record error; never promote candidate |

Incremental gates additionally require plugin-control preserved-spec, function-diff,
and inclusion/exclusion records. `pipeline.py phase-complete` invokes
these checks; state cannot advance on a failed gate.
