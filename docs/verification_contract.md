---
type: design
project: mdex
status: draft
updated: 2026-07-10
---

# Verification-First Context Contract

## Core Rule

When an authoritative state and a deterministic verifier exist, cognition may
propose a hypothesis or choose a verifier, but it must not be the final authority
for the claim.

The design goal is not perfect consistency. It is inexpensive verification,
explicit uncertainty, and graceful degradation when verification is unavailable.

## Claim States

Every verifiable claim should expose one of these states instead of collapsing
all failures into true or false:

- `verified`: the declared verifier succeeded against the recorded evidence
- `stale`: a dependency or invalidation condition changed after verification
- `conflicting`: authoritative evidence supports incompatible values
- `unverifiable`: no approved verifier or source of truth is available
- `missing`: required evidence does not exist
- `verifier_error`: the verifier failed to complete
- `environment_mismatch`: the verifier environment differs from the recorded one
- `unchecked_since_scan`: dependencies may have changed since the last observation
- `unblessed`: a successful first result was observed but has not been approved as a baseline

An absent match is not evidence of absence unless the verifier contract explicitly
guarantees complete coverage for the declared scope.

## Authoring and Substrate Must Be Separate

The complete claim record is a storage substrate, not an authoring requirement.
Requiring every human or agent to maintain the full record would make verified
memory too expensive to write and would recreate the maintenance problem this
design is intended to remove.

Authors should provide only information that tools cannot infer, using a minimal
hook such as:

```yaml
verify:
  command: python
  args: [tools/check_cluster_count.py]
depends_on: [cdex/**, tests/golden/**]
```

Tools such as mdex or cdex should observe execution and generate the expanded
substrate automatically:

```yaml
claim_id: stable-claim-identifier
statement: human-readable claim
scope: corpus, repository, lane, or artifact boundary
source_of_truth: authoritative source identifier
state: verified | stale | conflicting | unverifiable | missing | verifier_error | environment_mismatch | unchecked_since_scan | unblessed
verified_at: ISO-8601 timestamp
valid_at: ISO-8601 timestamp or revision
evidence:
  commit: source revision
  paths_and_digests: []
  verifier_id: stable verifier identifier
  verifier_version: version or digest
  argv: []
  environment_digest: reproducible environment digest
  result_digest: normalized result digest
invalidation_rules: []
conflicts_with: []
```

The agent-facing result should be smaller again and contain only the decision state,
evidence reference, reason, and a structured reverification action:

```json
{
  "state": "stale",
  "reason": "dependency_digest_changed",
  "reverify": {
    "command": "python",
    "args": ["tools/check_cluster_count.py"],
    "timeout_ms": 30000,
    "side_effects": "none",
    "capabilities": {
      "filesystem": "read-only",
      "network": false
    }
  }
}
```

The governing rule is: authors write the minimum non-inferable hook; tools generate
identifiers, digests, verification timestamps, state transitions, and dependency
edges.

Prefer monotonic revisions, content digests, and commit hashes over wall-clock
timestamps when they can establish ordering more precisely.

## Baseline Capture and Blessing

Authors must not copy an expected digest into prose. The tool captures a normalized
`result_digest` from the first successful verifier execution and records it as an
`unblessed` candidate. An authorized human or policy gate then performs an explicit
`bless` operation to pin that candidate.

After blessing:

- the same result is `verified`
- a different result is `conflicting`
- the new result cannot become authoritative until an explicit `re-bless`

This is a two-phase TOFU model: first use observes a candidate but does not silently
trust it. The only human input is the verifier definition, non-inferable dependency
scope, and the intent to bless or re-bless.

## Verifier Contract

A verification hook is structured data, not a shell command string. It should
declare:

- executable and argv array
- accepted exit codes and output schema
- timeout
- side-effect class
- filesystem and network permissions
- environment or lockfile digest
- result normalization rules

The hook must be safe to run repeatedly. If it is not read-only, the side effects
must be explicit and separately authorized.

`side_effects` is a claim used for policy routing, not authority by itself. The
execution sandbox must enforce the declared filesystem and network capabilities.
Read-only hooks may be eligible for inline automatic execution; mutating or unknown
hooks require the appropriate gate. Timeout and capability failures return an
explicit verifier state rather than being interpreted as a false claim.

## Invalidation

`last_verified` alone is insufficient. A claim must automatically become `stale`
when any declared dependency changes, including:

- source paths or their content digests
- source commit or authoritative revision
- verifier implementation or version
- schema or normalization logic
- runtime environment
- scope definition

Reverification should therefore be triggered by a dependency graph rather than by
an agent deciding from prose that a memory looks old.

A continuously running daemon is not required. `scan`, `status --check`, and commit
gates may evaluate invalidation lazily. Until such a check runs, the claim must be
reported as `unchecked_since_scan`, not assumed fresh.

## Independent Anchors

A verifier must not derive both the expected result and the observed result from
the same implementation. Golden results should record their provenance and, when
practical, be supported by an independent implementation, an official tool, or a
metamorphic/property-based check.

Golden evidence is versioned evidence, not permanent truth. A change to its source,
environment, or normalization rules invalidates it.

Use the cheapest independent pressure first:

1. metamorphic invariants such as rename and whitespace stability, plus expected
   change under semantic edits
2. golden results from a pinned environment
3. comparison with an independent or official implementation

Metamorphic checks reduce oracle cost but do not prove that two implementations do
not share the same conceptual blind spot.

## Evaluation Contract

Retrieval quality must use an externally anchored denominator. A `70/70` result is
meaningful only when the 70 expected items were established independently of the
retrieval mechanism under evaluation.

Recommended measurements include:

- verified recall and precision
- provenance and age of the gold set
- declared but unsearched scope
- abstention and unverifiable rates
- false positives
- token, latency, verifier-compute, authoring, and maintenance cost

The primary optimization target is verified coverage per total cost, not token
reduction in isolation.

## Cross-Lane Capability Discovery

Information absent from every queried context cannot be recovered by a smarter
ranker. Independent lanes should publish a small capability manifest containing:

- capability ID and problem statement
- input and output contracts
- owner and lifecycle state
- evidence and last verification
- `overlaps`, `supersedes`, and dependency relationships

This coordination plane allows duplicate mechanisms and missing ownership to be
detected without relying on lexical coincidence.

Capability state must not rely on self-report alone:

- `declared`: the lane publishes the capability
- `observed`: matching code or artifacts exist
- `verified`: its liveness hook succeeds
- `stale`: evidence or dependencies changed after the last successful check
- `unknown`: liveness cannot currently be established
- `retired`: an owner explicitly ended or superseded it

Silence is not evidence of retirement. An inactive or unreachable lane becomes
`unknown` or `stale` unless explicit retirement evidence exists.

## Trust Boundary

Verification cannot recurse forever. Every deployment must declare the trusted
computing base at which anchor verification stops, for example:

```yaml
trust_root:
  - git-object-model
  - sha256-implementation
  - pinned-cpython-3.11-artifact
assurance: independent-golden-plus-metamorphic
```

The contract does not claim proof below this boundary. It makes explicit what was
verified and what was trusted.

## Lane Responsibilities

The shared protocol should keep product ownership narrow:

- a code-evidence lane such as cdex owns extraction, parity, and metamorphic checks
- mdex owns document and memory freshness, invalidation, and context exposure
- the shared contract owns evidence references, states, reverification actions, and
  capability manifests

## Protocol Ownership

The common protocol must not be canonically owned by mdex, cdex, or a deployment
template. Its preferred home is a small neutral contract repository containing only:

- versioned JSON Schemas for evidence, state, reverify actions, and capability manifests
- conformance fixtures
- compatibility and versioning policy
- changelog and release digests

mdex and cdex pin a released schema version and digest. A harness template may vendor
that pinned release under `protocol/` for wiring, but the vendored copy is not the
source of truth. Creating the neutral repository and assigning maintainers requires
owner approval; until then, this section records the agreed target architecture only.

## mdex Direction

`mdex` should use this contract to make doubt inexpensive:

1. Return evidence, freshness, and uncertainty with context recommendations.
2. Mark invalidated claims as stale before they enter an agent's first-pass view.
3. Keep exact search such as `rg` as an internal verifier or expansion component,
   not as proof that an unmatched item does not exist.
4. Measure retrieval against independently maintained golden cases.
5. Degrade to explicit unknown states instead of silently returning incomplete
   certainty.
6. Keep the prose authoring format small while storing the full verification record
   in a tool-maintained substrate.

This document defines a proposed design direction. It does not claim that every
field, state transition, or verifier hook is already implemented.
