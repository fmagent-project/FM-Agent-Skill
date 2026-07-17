# Specification rules

Write behavioral contracts, not a reconstruction of the implementation. Begin
from what callers require, then state behavior over all paths: preconditions,
postconditions, errors, data/format/range invariants, and cross-path
invariants. A defect is a gap between that contract and implementation; do not
weaken the contract to document a bug.

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
