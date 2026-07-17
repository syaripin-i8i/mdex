# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-07-17

### Added

- Add one schema-backed `health` contract shared by `doctor`, `status`, `context`, and `start`, with source fingerprint validity taking precedence over timestamp age. `start` now propagates health into agent prompt packs, recommends `mdex scan` for non-reusable evidence, and marks returned candidates unverified. Doctor adds configurable untracked/generated/oversized text/single-node/total-index surface checks while keeping hygiene warnings separate from evidence reuse.

### Changed

- Advance the package and emitted contract version to `0.6.0` for the required `health` field, and make all four command schemas reference the canonical `health.schema.json` contract instead of embedding divergent copies.

### Fixed

- Generalize the linked-worktree fallback to multi-index aliases: `status` / `context` / `start` with `--include repo,task,...` no longer report task / memory / artifacts indexes as `index_db_missing` from a linked worktree when the db exists under the main checkout. A missing worktree-relative alias candidate (config `indexes.<alias>.db` or the default) is retried under the main checkout root — surfaced once as `worktree_common_root` in the resolved db info — borrowing only an existing db read-only with the auditable source labels `config:<alias>+worktree_common_root` / `default:<alias>+worktree_common_root` (plus `borrowed: true` and the worktree-side `local_path` in the index spec). The same fail-closed rules apply as for the repo db: absolute config values are never re-anchored, mirrors escaping the main checkout are skipped, and a borrowed db is never a creation target — missing/stale `scan-artifacts` recommendations keep pointing at the worktree-local path. `doctor` / `status` on a borrowed repo db now anchor the scan manifest's JSON confinement at the main checkout root instead of returning no JSON path, removing the misleading "manifest JSON output path is unavailable or unsafe; run mdex scan" warning for consistent borrowed generations.
- Resolve configured task indexes through the shared resolver and recognize an existing `.mdex/task_index.db` as an explicit legacy candidate, preventing false `index_db_missing` reports when the task DB exists under a supported configured or legacy name.
- Resolve the index DB from linked git worktrees: when the anchored repo root's `.git` is a worktree marker file and the config/default DB candidates are missing locally (tracked `.mdex/config.json` without the untracked DB), the same relative candidates are retried under the main checkout root (derived from the marker's `gitdir:` and its `commondir`, without subprocess) and recorded as `worktree_common_root` in `resolution_attempts`. The fallback is read-only twice over — `must_exist=False` creation never targets the main checkout, and the writing commands `enrich` / `stamp` / `finish` resolve with the fallback disabled, keeping their fail-closed `db not found` behavior. It keeps `--db` / `MDEX_DB` precedence, skips submodules, bare repositories, and stale worktree markers, skips (rather than fails on) mirror candidates escaping the main checkout, and never walks parent directories into an outer repository. `context` on a borrowed DB reports `evidence_identity` as `identified` with reason `worktree_borrowed_index` instead of `verified`/reusable, because freshness is verified against the main checkout's files, not the worktree's checked-out content.

## [0.5.0] - 2026-07-11

### Added

- Disclose `zero_hits` (`lanes_searched` / `lanes_inactive` / `caveat` / `remediation`, shared vocabulary with cdex) when `find` or `context` search the metadata lane and match nothing, so agents stop reading an empty result as proof of absence. `context` carries it as a payload key; `find` keeps its stdout contract (`[]` for json, empty output for table) and emits one stderr JSON line with exit `0`, which stays the source of truth for success. `lanes_inactive` is an always-present map with an open reason-token set; the standard remediation is self-contained (`rg` plus frontmatter tags) with cdex as an availability-qualified hint; multi-index claims the field only when every requested index was searched and each bounded a true zero. Ratified conditionally by Sol (decision record), effective with these corrections; ships in `0.5.0`, not a `0.4.x` patch.
- A packaged `scan_config.schema.json` input contract shared by `scan`, `finish --scan`, and the quality evaluator.
- A manually curated golden retrieval suite with Recall@3, Precision@3, MRR, case/operation baselines, and report-only p50/p95 latency measurements.
- `scan-artifacts` builds a separate `.mdex/artifacts.db` index for generated observation artifacts without adding `outputs/` to the main repo index.
- `context` and `start` full actionable digests include `relevant_artifacts`; artifact rows carry metadata/freshness fields such as kind, generated timestamp, age, and stale status.
- Artifact scans tolerate disappeared files and oversized files as warnings, expose `warning_summary`, and support `max_file_size_bytes` / `max_jsonl_rows_read` controls.
- Artifact roots may be configured as objects with `id_prefix` and `expose_source_root: false` to keep repository-external local paths out of node ids and metadata.
- Artifact scans default-exclude `runtime_state/**`, and multi-index output uses repo-relative DB paths when possible.
- Multi-index artifact context reports `artifacts_index_age` and recommends `scan-artifacts` when the artifact DB is missing or stale.

### Changed

- Clarify the intended adoption conditions, public-preview boundary, and non-fit cases, and recommend a one-repository, 3–5-task pilot with explicit continue and stop criteria.
- The public main-index config moves completed `tasks/**` into a documented task-history lane.
- The manual PyPI workflow now requires a version-matching tag, runs the full tests, hash-locks build tools, and publishes only the immutable smoke-tested artifact from an OIDC-isolated job.
- Security, installation, and release documentation now reflect the `0.5.x` supported line.

### Fixed

- Make document scan configuration fail closed on unknown keys, invalid types or lanes, non-finite numbers, missing explicitly configured files, and unavailable packaged schemas.
- Align the documented CLI output boundary with the existing schema-backed JSON, utility JSON, table, and source-text formats without wrapping legacy utility payloads.
- Pin published schema retrieval guidance to immutable version tags while retaining stable logical schema identifiers in payloads.
- Reject scan outputs that collide with each other, their lock files, or an indexed source file, and write JSON indexes atomically.
- Persist a versioned scan manifest and make `finish --scan` fail closed on stale config, scope, lane, output identity, or concurrent rescans.
- Make `finish --scan` refresh both SQLite and JSON outputs and propagate scan failures.
- Report SQLite-written/JSON-failed rescans as explicit partial updates.
- Preserve spaces and non-ASCII Git paths when collecting changed files.
- Prune virtual environments, dependency trees, and build caches before recursive scan traversal.
- Fail scans on root traversal errors instead of replacing an index with a false empty result.
- Serialize complete SQLite/JSON scan pairs and summary enrichment so concurrent agent updates or output generations are not lost.
- Confine config/default-generated DB and JSON paths to `.mdex/`; explicit CLI paths remain available for one-shot scans.

## [0.4.0] - 2026-06-01

### Added

- `context` and `start` support `--include repo,task,memory` for additive multi-index payloads.
- `context` and `start` emit `discovery_candidates`, stable match explanations, path/symbol scoring, and budget-drop audit rows.
- `context --for-agent` and `start --for-agent` emit compact prompt packs for `worker`, `reviewer`, and `commander` subagents.
- Scan configs may define repo-local `synonyms` / `search_synonyms` for mixed CJK/English query expansion.
- `mdex status` summarizes freshness/doctor health across selected indexes; `doctor` now reports index freshness, config paths, stale overrides, `.DS_Store`, large JSON, and archive-task hygiene.
- `impact` emits anomaly lanes, and `finish --dry-run` exposes `suspicion_signals` for closeout review.
- `scan --incremental` records mtime/size/SHA-256 fingerprints and reports changed/unchanged index rows.
- Actionable digest full mode includes `discovery_candidates`; minimal mode remains compact.

### Changed

- Contract version bumped to `0.4.0` for backward-compatible schema additions.

## [0.3.0] - 2026-05-01

### Added

- `start` and `context --actionable` support `--digest minimal|full`; `full` preserves the previous digest shape and `minimal` reduces context usage.
- Success JSON payloads for `scan`, `start`, `context`, `doctor`, `impact`, and `finish` include required `contract_schema` and `contract_version`.
- Error JSON payloads include required `contract_schema`, `contract_version`, and machine-readable `code`.
- Agent integration guidance in `docs/agent_integration.md`, including safe argv execution for structured actions and `suggested_rg.args`.
- `AGENT.md` now documents shortest safe paths and an entrypoint flowchart for low round-trip agent use.
- Opt-in local telemetry via `MDEX_TELEMETRY=1` or `.mdex/config.json` `telemetry: true`, appending redacted JSON Lines events to `.mdex/telemetry.jsonl`.

### Changed

- `recommended_next_actions` v1 is now documented and schema-annotated as deprecated; agents should prefer `recommended_next_actions_v2`.
- `recommended_next_actions_v2` now uses executable argv-style commands such as `mdex open ...` and `rg -n ...`.
- Parser-level argument failures now emit JSON error payloads instead of argparse prose.
- `mdex doctor` now checks telemetry health when telemetry is enabled.

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
