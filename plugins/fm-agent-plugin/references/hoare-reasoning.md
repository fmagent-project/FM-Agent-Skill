# Hoare reasoning

Use the current function's parsed `Pre-condition` as the first block's
precondition. Split large functions at safe syntax boundaries (brace-aware for
braced languages; conservative fallback otherwise). For each block, derive its
postcondition from source, current precondition, `[INFO]`, and domain context;
pass that postcondition as the next block's precondition.

Check each terminating block and the final block against the specification
postcondition. A mismatch must retain the triggering statements, derived
postcondition, and reason. Malformed spec, model/tool failure, or unparseable
output is `ERROR`, not `MATCH` or `MISMATCH`; proceed with other functions.

Use at most `MAX_SPC_ITER = 5` attempts for a postcondition/spec check unless a
smaller configured retry limit applies. Emit a verification JSON object matching
[artifact-contract.md](artifact-contract.md): `MATCH` for a proved check,
`MISMATCH` for a reasoned local violation, `DEPENDENCY_RISK` when only a
callee's direct mismatch affects the caller's outcome, and `ERROR` for failure
to reason. Do not manufacture a caller `MISMATCH` solely from a callee result.

For an input parser/converter, reject a `MATCH` when its specification excludes
malformed input solely by assuming validity, while an in-scope caller can pass
unvalidated text or bytes. Re-open the specification using
[specification-rules.md](specification-rules.md)'s input-domain rule, then
check full-consumption and rejection paths as local behavior.
