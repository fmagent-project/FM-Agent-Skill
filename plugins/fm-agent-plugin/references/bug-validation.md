# Bug validation

Treat a reasoning mismatch as a candidate, not a confirmed defect. A Bug Validator
report should retain the specification claim, observed behavior, code evidence,
trigger condition, reproduction/probe, output, and confirmation state. Do not
run destructive probes without the user's authorization. The diagnosis skill
reports these fields faithfully and never upgrades an unconfirmed candidate.

Only a direct `MISMATCH` is a candidate. A `DEPENDENCY_RISK` result means the
caller must be reconsidered in incremental selection but must not create a
duplicate bug report. For a CMake project, build every probe in
`fm_agent_plugin/probes/<run-id>/<bug-id>/build/` through `probe_build.py`.
Never reuse the project's existing `build/` directory or its `CMakeCache.txt`.
