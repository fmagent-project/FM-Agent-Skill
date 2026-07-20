# Resume contract

Resume continues one interrupted `full` or `incremental` run. It is explicit:
ordinary `run` selection never treats an incomplete run as an automatic
incremental analysis.

## Eligibility

`orchestrate.py resume-inspect` selects the newest run whose status is
`running`, `failed`, or `interrupted`. It accepts the run only when all of the
following hold:

- Its immutable effective configuration and auxiliary-input hashes still match.
- Its scoped supported-source snapshot still matches exactly. A Git commit may
  differ when its source content is identical.
- It has a valid run id, phase list, and a first incomplete phase.
- It was created with resumable run state, including `source_snapshot`.

Do not pass a new note, scope, knowledge file, supplemental edge file,
`one_phase`, `isolate`, or backend choice to a resume. A source or input change
makes the old workspace unsafe; report the reason and require a normal new
analysis rather than silently switching modes.

## Lock ownership

Resume reuses the existing run id but obtains a new lock lease. If that same
run has a heartbeat newer than `resume_grace_seconds` (default 600), treat it
as potentially active and do not take it over. Request explicit user
confirmation before retrying with `--take-over`. A lock owned by a different
non-terminal run is never replaced by resume.

## Checkpoints

`pipeline.py resume` validates every phase already marked `succeeded` using
the normal stage gate. It then chooses the first phase not marked successful.
It records the prior incomplete phase status in `phase_history`, increments
`resume.count`, and returns the run to `running` without creating a new
baseline.

Re-enter the selected phase through `pipeline.py phase-start`; do not rerun
earlier successful phases. A full-run `phase_cleanup` executes again only when
it is itself the first incomplete phase.

## Artifact reuse

Within a resumed incomplete phase, preserve an artifact only when its current
function identity and source hash still match. Generate only missing, malformed,
or hash-incompatible work:

- extraction: current function copies and analysis-index entries;
- specification: paired `[SPEC]` and `[INFO]` headers;
- verification: schema-valid result records with matching function id and hash;
- bug validation: current-run reports for unresolved direct `MISMATCH` items.

For a resumed graph phase, keep the backend recorded in the run. A readable
same-snapshot CodeGraph index may be reused; a missing or unreadable selected
index must be rebuilt. A completed valid graph phase is not rebuilt merely
because the run resumes later.

Only `pipeline.py complete` may create or advance `baseline.json`.
