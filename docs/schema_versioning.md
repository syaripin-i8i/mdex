# JSON Schema Versioning Policy

## Scope

This policy covers the machine-readable JSON contracts in `schemas/`:

- `scan.schema.json`
- `start.schema.json`
- `context.schema.json`
- `doctor.schema.json`
- `impact.schema.json`
- `finish.schema.json`
- `error.schema.json` (stderr error payloads)
- `telemetry_event.schema.json` (opt-in local JSON Lines events)

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
- The same semver/deprecation guarantees apply to both success and error schemas.

## Deprecation Policy

- A field scheduled for removal is first marked deprecated in docs and release notes.
- Minimum deprecation window: one MINOR release cycle before MAJOR removal.
- During deprecation, field behavior and type stay stable.
- `recommended_next_actions` is deprecated in `start` and actionable `context` payloads. Agents should prefer structured `recommended_next_actions_v2`; v1 remains present in 0.2.x for compatibility.

## Change Process

1. Update schema files in `schemas/`.
2. Update this document if policy assumptions changed.
3. Add or update tests validating CLI outputs against schemas.
4. Record contract-impacting changes in `CHANGELOG.md`.

## Notification Rules

- Breaking changes are announced in `CHANGELOG.md` under an explicit breaking-change note.
- Deprecations are announced in `CHANGELOG.md` before removal.
- README links remain the entry point for schema location and policy reference.

## Recent Minor Additions

- `scan.schema.json` adds optional `warnings` for per-file parse failures in non-strict scan mode.
- `start` and `context --actionable` support `--digest minimal|full`. Minimal digest may omit full-only `actionable_digest` members.

## 0.3.0 Contract Tightening

- Success and error schemas require `contract_schema` and `contract_version`.
- Error schemas require machine-readable `code` alongside human-readable `error`.
- `recommended_next_actions` remains present but deprecated; agents should prefer `recommended_next_actions_v2`.
- `telemetry_event.schema.json` is versioned independently from command stdout/stderr schemas because telemetry is opt-in observability data.
