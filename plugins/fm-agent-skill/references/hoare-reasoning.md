# Hoare reasoning

Use the current function's parsed `Pre-condition` as the first block's
precondition. Split large functions at safe syntax boundaries (brace-aware for
braced languages; conservative fallback otherwise). For each block, derive its
postcondition from source, current precondition, the current function's
`.info.json` callee expectations, and domain context;
pass that postcondition as the next block's precondition.

Check each terminating block and the final block by asking whether there exists
a concrete valid input satisfying the derived actual postcondition A and
violating the specification postcondition B. In logical form, `MISMATCH`
requires `∃x. Pre(x) ∧ A(x) ∧ ¬B(x)`; `MATCH` requires the bounded reasoner to
establish `∀x. Pre(x) → (A(x) → B(x))`. A mismatch must retain a concrete
counterexample, one exact contiguous source quote, A, B, and the reason.
Malformed spec, model/tool failure, or unparseable output is `ERROR`, not
`MATCH` or `MISMATCH`; proceed with other functions.

Before proving a result, inspect `contract_basis`, normative and inference
evidence, and implementation observations. A `normative` or `inferred` sidecar
provides condition B and must receive the same FM-Agent A→B reasoning. An
inferred contract is supported by interface/caller/paired-API/type consistency
rather than external prose; do not downgrade it solely for lacking documents.
An `unavailable` contract produces `INCONCLUSIVE` only after the Worker names
the actual missing semantic signal. Implementation observations may derive A
but cannot prove B or MATCH. Never turn an observed constant, comparison,
formula, field, or branch into its own proof obligation.

The assigned Codex/Claude Worker is the reasoner; no local script calls a model
API. It writes one structured postcondition/spec result. On invalid output the
Coordinator requeues the same job under the configured retry bound (five total
attempts by default); do not infer a verdict from prose. Emit a verification
JSON object matching
[artifact-contract.md](artifact-contract.md): `MATCH` for a proved check,
`MISMATCH` for a reasoned local violation, `DEPENDENCY_RISK` when only a
callee's direct mismatch affects the caller's outcome, `INCONCLUSIVE` for an
unavailable or genuinely unresolvable contract, and `ERROR` for failure to reason. Do not
manufacture a caller `MISMATCH` solely from a callee result.

For an input parser/converter, reject a `MATCH` when its specification excludes
malformed input solely by assuming validity, while an in-scope caller can pass
unvalidated text or bytes. Re-open the specification using
[specification-rules.md](specification-rules.md)'s input-domain rule, then
check full-consumption and rejection paths as local behavior.
