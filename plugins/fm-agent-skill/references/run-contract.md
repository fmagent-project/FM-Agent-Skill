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

`diagnose.py` is the terminal authority check. A run may be described as an
FM-Agent result only when `result_authority.official_result_available` is true.
An isolated failure writes `fm_agent_skill/failure.json` to the original
worktree and retains semantic artifacts only in its resumable snapshot.

An explicit `--resume` is separate from the table above. It continues the
newest eligible interrupted full or incremental run only after exact source and
configuration checks; see [resume-contract.md](resume-contract.md). It does
not create a baseline, select a new mode, or replace the active analysis.
