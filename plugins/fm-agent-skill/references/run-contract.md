# Run contract

`orchestrate.py inspect` performs the read-only mode decision before any
CodeGraph action. A valid baseline requires parsed phases, a reachable commit
at `refs/fm-agent-skill/baseline`, complete `.spec.json` and `.info.json`
extracted artifacts, full verification output, and a matching scope fingerprint.
Failure of any condition selects full analysis. Stateful dispatch then creates a
private Git snapshot commit and runs the selected mode in its detached worktree.

| State | Agent action |
| --- | --- |
| Invalid baseline | Prepare and execute the full phase plan. |
| Valid, no code change | Write no-op state and do not analyze. |
| Valid, source change | Write a restricted intent and execute the incremental plan. |

`pipeline.py` records the plan and current phase; it does not perform project
understanding, extraction, specification generation, or reasoning. Those are
the coding agent's responsibilities under the run skill.

`terminal_report.py` is the sole terminal authority. A run may expose findings
only when `official_result_available` is true. `diagnose.py` remains read-only
operational inspection.
An isolated failure writes `fm_agent_skill/failure.json` to the original
worktree and commits the latest valid semantic artifacts to
`fm_agent_skill/checkpoint/`; the detached worktree remains only a disposable
execution cache.

Ordinary run automatically resumes when compatible; explicit `--resume` is
also supported. It continues the eligible interrupted analysis after exact source and
configuration checks; see [resume-contract.md](resume-contract.md). It does
not create a baseline, select a new mode, or replace the active analysis.
