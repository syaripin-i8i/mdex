# Support Matrix

## Runtime and Platform Support

| Tier | Target | Notes |
|---|---|---|
| Primary (maintained and CI-tested) | `ubuntu-latest` + Python 3.10/3.11/3.12/3.13/3.14 | Maintainer baseline for release decisions |
| Best-effort (CI-tested, community issues welcome) | `macos-latest`, `windows-latest` + Python 3.10/3.11/3.12/3.13/3.14 | Fixes are prioritized after primary-tier stability |

## Python Support

- Supported Python versions: 3.10, 3.11, 3.12, 3.13, 3.14
- CI matrix runs all supported Python versions across all supported OS tiers

### Python 3.13+ status

- Current state: `requires-python = ">=3.10,<3.15"` and the CI matrix covers 3.10-3.14.
- "3.13+" currently means Python 3.13 and 3.14. Future Python minors are added after CI and lockfile verification.
- Local verification has also passed on Python 3.14.3.

## Compatibility Scope

- CLI JSON output compatibility is governed by `schemas/` and `docs/schema_versioning.md`.
- Backward-compatible additions are allowed in minor releases.
- Breaking output-contract changes require a major version bump.

## Deprecation Policy

- Deprecations are announced in `CHANGELOG.md` before removal.
- Minimum deprecation window: one minor release cycle.
- Deprecated fields keep type/meaning stable during the window.

## Support Response Targets

- Primary tier issue acknowledgment: within 3 business days (best effort)
- Best-effort tier issue acknowledgment: within 7 business days (best effort)
- Fix timing depends on severity and maintainer capacity

## Out-of-Scope Guarantees

- No guarantee for undocumented/internal APIs.
- No guarantee for repositories that violate `docs/convention.md` input assumptions.
