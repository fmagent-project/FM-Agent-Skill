# Bug validation

Treat a reasoning mismatch as a candidate, not a confirmed defect. A Bug Validator
report should retain the specification claim, observed behavior, code evidence,
trigger condition, reproduction/probe, output, and confirmation state. Do not
run destructive probes without the user's authorization. The diagnosis skill
reports these fields faithfully and never upgrades an unconfirmed candidate.

Only a direct `MISMATCH` is a candidate. A `DEPENDENCY_RISK` result means the
caller must be reconsidered in incremental selection but must not create a
duplicate bug report. For a CMake project, build every probe in
`fm_agent_skill/probes/<bug-id>/build/` through `probe_build.py`.
Never reuse the project's existing `build/` directory or its `CMakeCache.txt`.

For an incremental run, consider only direct `MISMATCH` results whose
`function_id` appears in that run's `incremental_decision.json.included` map.
If candidates exist, replace `summary.json` with a new current-analysis summary
is the current run; never reuse an earlier summary or probe as confirmation.

## Retry policy

Treat runtime failure separately from a negative finding. A host timeout, rate
limit, tool crash, probe-build failure, or missing/invalid result is reported by
the Coordinator as `execution`, `output`, or `interrupted` and is retried up to
`bug_validation_max_attempts` (default: five runtime attempts). Do not convert
those failures into a rejected candidate.

A completed probe with `not_reproduced`, `rejected`, or `inconclusive` is a
negative validation result. Repeat it under the same job ID until it has made
`bug_validation_negative_retries + 1` completed negative attempts (default:
three probes: the first plus two repeats). Vary only the permitted trigger or
probe parameters; preserve the same source snapshot and candidate identity.
Stop immediately on `confirmed`. On exhaustion, use `rejected` only where the
candidate was actually tested and not reproduced; retain `inconclusive` when
evidence remains insufficient.

The assigned result JSON must retain an `attempts` array. Each entry records its
ordinal, classification, trigger/probe, output, and timestamp, so a later probe
never overwrites earlier evidence.
