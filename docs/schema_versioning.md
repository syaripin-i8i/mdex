# JSON Schema Versioning Policy

## Scope

This policy covers the machine-readable JSON contracts in `schemas/`.

Schema-backed CLI output:

- `scan.schema.json`
- `start.schema.json`
- `context.schema.json`
- `doctor.schema.json`
- `status.schema.json`
- `impact.schema.json`
- `finish.schema.json`
- `error.schema.json` (stderr error payloads)

CLI input:

- `scan_config.schema.json` (document scan configuration)

Other local protocol:

- `telemetry_event.schema.json` (opt-in local JSON Lines events)

The successful utility JSON commands (`list`, `find`, `orphans`, `stale`, `query`, `first`, `related`, `enrich`, `new`, and `stamp`), table output, and `open` text are intentionally outside the schema-backed output set. Their current shapes remain documented in `README.md`.

## Identifier and Release Retrieval Policy

`contract_schema` is a stable logical identifier. Its value stays paired with `contract_version`; consumers must not download a mutable branch and assume it represents that version.

For an already published release, retrieve the immutable schema content from:

```text
https://raw.githubusercontent.com/syaripin-i8i/mdex/v{contract_version}/schemas/{schema_filename}
```

The release tag is the version boundary. Release validation must confirm that every emitted schema filename exists under that tag. A schema added only in an unreleased checkout, including a newly introduced input schema, uses the checkout or installed package copy until a release containing it is tagged; documentation must not claim that an older tag contains it.

The `$id` values and emitted `contract_schema` values remain logical identifiers so existing consumers are not broken merely to change transport. Immutability comes from the version-tagged retrieval URL, not from a mutable default branch.

## Versioning Model

`mdex` uses Semantic Versioning for CLI contract compatibility.

- `MAJOR`: breaking contract changes
  - removing a required field
  - changing the type of an existing field
  - tightening semantics in a way that breaks existing consumers
- `MINOR`: backward-compatible additions
  - adding optional fields
  - adding optional object members
- `PATCH`: no contract break
  - typo fixes
  - description clarifications
  - schema metadata/documentation-only changes

Before `1.0.0`, `mdex` may ship contract-tightening changes in a `0.x` minor release when the change is explicit in `CHANGELOG.md` and the package is still in public-preview mode. After `1.0.0`, the `MAJOR` rules above apply strictly.

## Compatibility Guarantees

- Existing required fields keep name and type within the same MAJOR line.
- Optional fields may be added in MINOR releases.
- Consumers should ignore unknown fields for forward compatibility.
- The same semver/deprecation guarantees apply to schema-backed success and error schemas.
- Input schema tightening that rejects a configuration accepted by the previous release is contract-impacting and must follow the same release/change process.

## Deprecation Policy

- A field scheduled for removal is first marked deprecated in docs and release notes.
- Minimum deprecation window: one MINOR release cycle before MAJOR removal.
- During deprecation, field behavior and type stay stable.
- `recommended_next_actions` is deprecated in `start` and actionable `context` payloads. Agents should prefer structured `recommended_next_actions_v2`; v1 remains present in 0.2.x for compatibility.

## Change Process

1. Update schema files in `schemas/`.
2. Update this document if policy assumptions changed.
3. Add or update tests validating CLI outputs against schemas.
4. Validate input schemas against every shipped example/configuration and verify that the schema is present in built artifacts.
5. Record contract-impacting changes in `CHANGELOG.md`.

## Notification Rules

- Breaking changes are announced in `CHANGELOG.md` under an explicit breaking-change note.
- Deprecations are announced in `CHANGELOG.md` before removal.
- README links remain the entry point for schema location and policy reference.

## Recent Minor Additions

- `0.5.0` adds the optional `zero_hits` disclosure (`lanes_searched` / `lanes_inactive` / `caveat` / `remediation`, shared vocabulary with cdex) to `context`, and the success-case stderr `{"zero_hits": ...}` line for `find` when a searched query matches nothing. `lanes_inactive` is an always-present map with an open reason-token set; consumers must not reject unknown tokens.
- `0.4.0` adds optional multi-index, discovery lane, score explanation, budget audit, anomaly, suspicion, status, incremental-scan, and agent prompt-pack fields.
- `scan.schema.json` adds optional `warnings` for per-file parse failures in non-strict scan mode.
- `start` and `context --actionable` support `--digest minimal|full`. Minimal digest may omit full-only `actionable_digest` members.
- `context` and `start` full actionable digests may include `relevant_artifacts` when an artifact index is queried.

## 0.4.0 Contract Additions

- `context` and `start` may include `multi_index`, `per_index_context` / `per_index_start`, `discovery_candidates`, and `budget_dropped_nodes`.
- `context` and `start` may include artifact `metadata` and `freshness` fields on node, read-order, discovery, and digest rows.
- `context` and `start` may include `agent_prompt_pack` when `--for-agent` is requested.
- Node `score_breakdown` now stabilizes `keyword.matched_terms`, `keyword.matched_fields`, `path_symbol`, `graph_reason`, and `token_cost.budget_drop_reason`.
- `impact` may include `unusual_neighbors`, `isolated_changes`, `missing_decision_links`, and `unreflected_specs`.
- `finish` may include `suspicion_signals`.
- These additions are optional/additive; existing required keys remain unchanged.

## 0.3.0 Contract Tightening

- Schema-backed success and error schemas require `contract_schema` and `contract_version`.
- Error schemas require machine-readable `code` alongside human-readable `error`.
- `recommended_next_actions` remains present but deprecated; agents should prefer `recommended_next_actions_v2`.
- `telemetry_event.schema.json` is versioned independently from command stdout/stderr schemas because telemetry is opt-in observability data.
