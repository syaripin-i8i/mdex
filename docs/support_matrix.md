# Support Matrix

## Runtime Targets

| Dimension | Supported | Notes |
|---|---|---|
| Python | 3.10, 3.11, 3.12 | CI covers all three versions |
| OS | Linux, macOS, Windows | Path behavior is tested with normalized node ids |

## Compatibility Scope

- CLI JSON output compatibility is governed by `schemas/` and `docs/schema_versioning.md`.
- Backward-compatible additions are allowed in minor releases.
- Breaking output-contract changes require a major version bump.

## Deprecation Policy

- Deprecations are announced in `CHANGELOG.md` before removal.
- Minimum deprecation window: one minor release cycle.
- Deprecated fields keep type/meaning stable during the window.

## Out-of-Scope Guarantees

- No guarantee for undocumented/internal APIs.
- No guarantee for repositories that violate `docs/convention.md` input assumptions.
