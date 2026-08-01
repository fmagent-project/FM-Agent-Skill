# Specification rules

Write behavioral contracts, not a reconstruction of the implementation. Begin
from what callers require, then state behavior over all paths: preconditions,
postconditions, errors, data/format/range invariants, and cross-path
invariants. A defect is a gap between that contract and implementation; do not
weaken the contract to document a bug.

## Contract basis and evidence rule

Preserve FM-Agent's core separation: Specification derives intended condition
B; Verification independently derives actual condition A from the body. Do not
write B as a narration of A.

Use schema-v3 `contract_basis`:

- `normative` / `high`: copied user knowledge or a non-generated public API
  contract provides at least one root claim. Record exact quotes in
  `normative_evidence`. Caller contracts may supplement but cannot form a
  circular root.
- `inferred` / `medium`: no root document states the behavior, so infer the
  governing contract from independent semantic signals, as original FM-Agent
  does. Use public names/signatures, caller expectations, paired APIs,
  cross-function consistency, and declared type invariants. Record exact source
  quotes and the derived claims in `inference_evidence`. This is a real,
  falsifiable B and proceeds to A→B Verification; a resulting MISMATCH requires
  dynamic Bug Validation before confirmation.
- `unavailable` / `low`: use only after no falsifiable B can be formed from the
  available domain, interface, graph, and type context. Verification still
  runs and records the concrete reasoning gap; the executor never manufactures
  an INCONCLUSIVE result from confidence alone.

Treat implementation literals, comparison operators, branch thresholds,
formulas, field selections, and control flow as observations, not contract
facts. Record them only under `observations`. They may derive A but cannot
justify B or MATCH. Generated engine/phase context may guide inference but is
not itself a quotable source. Lack of external documentation alone never
forces `unavailable`.

Do not read tests when extracting, planning phases, generating specifications,
or verifying functions. This matches FM-Agent's source-analysis scope: tests
are neither implementation targets nor specification evidence.

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
an explicit validation guard. Record that guard in the function's `.info.json`
callee expectation; otherwise make the
failure behavior part of the current function's contract. Before completing
the specification phase, challenge every precondition: "would this exclude a
bad input that a caller can actually pass?" If yes, remove or weaken the
precondition and specify the failure behavior instead.

Failure behavior belongs inside `post_condition`; it is not a separate sidecar
field. The nine-key spec schema is closed. Never emit `error_behavior`,
`throws`, `exceptions`, `notes`, or another top-level key. A sidecar containing
one must be repaired by the same specification job before its receipt can pass.

Keep every extracted function copy byte-for-byte equivalent to its extracted
source. Store its contract in paired sidecars instead:

```text
// fm_agent/extracted_functions/.../parse.cpp
<unchanged extracted source>

// fm_agent/extracted_functions/.../parse.cpp.spec.json
{"schema_version":3,"signature":"parse(std::string_view)","pre_condition":"input is complete","post_condition":"rejects trailing input","contract_basis":"normative","normative_evidence":[{"kind":"public_api_contract","source":"include/parse.hpp","quote":"Rejects trailing input.","claims":["rejects trailing input"]}],"inference_evidence":[],"observations":[{"kind":"implementation","source":"src/parse.cpp","quote":"return parse_prefix(input);","claims":["implementation returns a parsed prefix"]}],"confidence":"high"}

// fm_agent/extracted_functions/.../parse.cpp.info.json
{"callees":[{"name":"...","signature":"...","pre_condition":"...","post_condition":"..."}]}
```

`info.json` contains the caller's expected contracts of its in-scope callees;
use `{"callees":[]}` when there are none. Prefer governing rules to enumerating
helpers, branches, or particular members of a set. Contracts must be
falsifiable and precise enough to verify but not so implementation-specific
that deleting one branch merely changes the wording. Never edit business source
or its extracted copy.
