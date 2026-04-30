# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-05-01

### Added

- `start` and `context --actionable` support `--digest minimal|full`; `full` preserves the previous digest shape and `minimal` reduces context usage.
- Success JSON payloads for `scan`, `start`, `context`, `doctor`, `impact`, and `finish` include required `contract_schema` and `contract_version`.
- Error JSON payloads include required `contract_schema`, `contract_version`, and machine-readable `code`.
- Agent integration guidance in `docs/agent_integration.md`, including safe argv execution for structured actions and `suggested_rg.args`.
- `AGENT.md` now documents shortest safe paths and an entrypoint flowchart for low round-trip agent use.

### Changed

- `recommended_next_actions` v1 is now documented and schema-annotated as deprecated; agents should prefer `recommended_next_actions_v2`.
- `recommended_next_actions_v2` now uses executable argv-style commands such as `mdex open ...` and `rg -n ...`.
- Parser-level argument failures now emit JSON error payloads instead of argparse prose.

## [0.2.0] - 2026-05-01

First public-preview GitHub/source milestone. PyPI publication can use this version once publishing is enabled.

### Added

- `scan` supports `scan_roots` (array) with backward-compatible `scan_root` alias handling.
- `scan` now supports per-file warning isolation by default, with `warnings[{path,error}]` in JSON output.
- `scan --strict` for fail-fast parsing behavior.
- `nodes.estimated_tokens` persisted in SQLite and reused by `context` scoring.
- New release workflow skeleton (`workflow_dispatch` only) with trusted publishing, attestation, twine check, and install smoke.
- New CI security/quality checks: coverage gate, gitleaks, CodeQL workflow, twine check, and sdist/wheel smoke.
- Lock-based environment install helper: `.github/scripts/install_from_pylock.py`.
- Docs consistency test for archived planning docs and phase-complete wording drift.
- Default scan safety excludes for local/secret-like files such as `.env*` and `*.local.{md,json,jsonl}`.
- `mdex doctor` command for index hygiene checks, including scan warnings, JSON/SQLite drift, orphan overrides, legacy artifacts, and `old/`/`archive/` review paths.
- Context hygiene policy documenting that the main repo index is an entrypoint guide, not a fixture/eval/log warehouse.
- Getting started, adoption guide, and before/after examples for first-time mdex evaluation.
- `context --actionable` and `start` now include `actionable_digest` with relevant docs, task history, likely code entrypoints, known guardrails, suggested `rg`, and context gaps.
- Japanese guardrail terms and a detailed `suggested_rg` example for the `actionable_digest` workflow.
- Python 3.13 and 3.14 support in the CI/support matrix.

### Changed

- Internal Python package namespace moved from top-level `runtime` to `mdex` to avoid cross-project import shadowing (`mdex` CLI behavior unchanged).
- `scan` now rejects cross-root `node_id` collisions (fail-closed).
- `write_sqlite` is now transactional (rollback on failure, `node_overrides` preserved).
- `context` skips source file reads when `include_content=False`.
- `update_node_summary` now stores the caller-provided `source` value.
- `docs/phase_a_agent_flow.md` moved to `docs/archive/phase_a_agent_flow.md` with historical disclaimer.
- CI dependency install switched to lockfile-driven install from `pylock.toml`.
- `scan` now warns when local/secret-like files are explicitly indexed after disabling default excludes.
- SQLite regeneration now prunes `node_overrides` for nodes no longer present in the freshly built index.
- SQLite metadata now records scan warnings so `mdex doctor` can surface them after the scan run.
- Public scan config now excludes archive, fixture, eval, log, dump, and raw-log paths from the main repo index.
- Package metadata now bounds supported Python installs to `>=3.10,<3.15`.

### Removed

- Removed legacy wrappers `scripts/mdex_start.py` and `scripts/mdex_finish.py` (use `mdex start` / `mdex finish` directly).

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
