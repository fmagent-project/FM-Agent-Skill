# Specification rules

Write behavioral contracts, not a reconstruction of the implementation. Begin
from what callers require, then state behavior over all paths: preconditions,
postconditions, errors, data/format/range invariants, and cross-path
invariants. A defect is a gap between that contract and implementation; do not
weaken the contract to document a bug.

## Input-domain rule

Before writing a contract, trace each parameter backward to its callers and
identify whether it can originate from an unvalidated boundary: CLI, file,
network, environment, user input, or an unchecked caller parameter. For such
an input, the contract covers **all values of its language type**. Do not make
"is valid", "is well-formed", "is complete", "has already been checked", or
an equivalent desired outcome a precondition merely because the implementation
assumes it.

For parsers, decoders, converters, validators, and tokenizers reachable from
unvalidated input, state both outcomes explicitly:

- which complete inputs are accepted and what value is returned;
- which malformed, empty, partial, trailing, out-of-range, or otherwise
  rejected inputs fail, including the error/exception contract when observable.

For example, a numeric conversion whose caller supplies expression text must
require successful consumption of the **entire** operand before returning; a
numeric prefix followed by extra text is rejected. It is a direct mismatch if
the implementation accepts that trailing text.

Use a restrictive precondition only when every in-scope caller proves it with
an explicit validation guard. Record that guard in `[INFO]`; otherwise make the
failure behavior part of the current function's contract. Before completing
the specification phase, challenge every precondition: "would this exclude a
bad input that a caller can actually pass?" If yes, remove or weaken the
precondition and specify the failure behavior instead.

Use native single-line comments and exactly this layout in an extracted copy:

```text
<C> [SPEC]
<C> Unit: <repo-relative path>
<C> <function signature>
<C> Pre-condition:
<C>   - ...
<C> Post-condition:
<C>   - ...
<C> [SPEC]

<C> [INFO]
<C> <callee contract or '(no callees)'>
<C> [INFO]

<unchanged source>
```

`[INFO]` contains the caller's expected contracts of its callees, separated by
`[SPLIT]` when needed. Prefer governing rules to enumerating helpers, branches,
or particular members of a set. Contracts must be falsifiable and precise enough
to verify but not so implementation-specific that deleting one branch merely
changes the wording. Never edit business source.
