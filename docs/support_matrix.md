# Support Matrix

## Runtime and Platform Support

| Tier | Target | Notes |
|---|---|---|
| Primary (maintained and CI-tested) | `ubuntu-latest` + Python 3.10/3.11/3.12 | Maintainer baseline for release decisions |
| Best-effort (CI-tested, community issues welcome) | `macos-latest`, `windows-latest` + Python 3.10/3.11/3.12 | Fixes are prioritized after primary-tier stability |

## Python Support

- Supported Python versions: 3.10, 3.11, 3.12
- CI matrix runs all supported Python versions across all supported OS tiers
- Python 3.13: **not yet supported**（現時点で CI matrix 外）

### Python 3.13 status

- Current state: `requires-python = ">=3.10"` だが、公式サポート宣言は 3.10-3.12 のみ。
- Blocker: lockfile + release-hash catalog の検証ターゲットが 3.10/3.11/3.12 で固定されており、
  3.13 を追加するには hash closure と CI install 経路を先に拡張する必要がある。
- Policy: blocker が解消されるまで 3.13 は「未検証 / best effort」に留める。

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
