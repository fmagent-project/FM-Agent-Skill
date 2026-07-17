# Run contract

`orchestrate.py inspect` performs the read-only mode decision before any
CodeGraph authorization. `baseline.py` determines the mode. A valid baseline requires parsed phases,
reachable successful commit, complete `[SPEC]` and `[INFO]` extracted artifacts,
full verification output, and a matching scope fingerprint. Failure of any
condition selects full analysis.

| State | Agent action |
| --- | --- |
| Invalid baseline | Prepare and execute the full phase plan. |
| Valid, no code change | Write no-op state and do not analyze. |
| Valid, source change | Write a restricted intent and execute the incremental plan. |

`pipeline.py` records the plan and current phase; it does not perform project
understanding, extraction, specification generation, or reasoning. Those are
the coding agent's responsibilities under the run skill.
