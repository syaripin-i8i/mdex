# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-19

### Added

- Protocol-first README/AGENT contract structure for agent workflows.
- JSON Schema contracts for `scan`, `start`, `context`, `impact`, and `finish`.
- GitHub Actions CI workflow for tests, dependency audit, and dependency review.
- Public project governance docs: security policy, contributing guide, and support matrix.
- Privacy note for scan-generated artifacts and sensitive source handling in README.

### Changed

- `open` and `stamp` now enforce indexed node-id usage with scan-root containment checks.
- License metadata and repository licensing documents aligned to Apache-2.0.
- Distribution package name changed to `mdex-cli` (CLI command remains `mdex`).
- `pyproject.toml` now includes maintainer and project URLs metadata for repository operations.
- Runtime config-derived paths are now constrained to stay inside the repository boundary.

### Security

- Rejected absolute-path and parent-traversal targets for `open`/`stamp`.
- Rejected non-indexed targets for `stamp` to prevent out-of-scope writes.
