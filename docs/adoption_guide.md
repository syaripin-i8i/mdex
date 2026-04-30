---
type: reference
project: mdex
status: active
updated: 2026-04-30
---

# Adoption Guide

This guide is for adding `mdex` to an existing repository without turning the index into a warehouse.

## Adoption Goal

The main repo index should answer:

> What should an AI agent read first to make a good decision?

It should not contain every file that might someday be useful.

## Recommended Rollout

This guide describes the current GitHub source state. Install with `python -m pip install git+https://github.com/syaripin-i8i/mdex.git` or from a local checkout with `python -m pip install -e .`.

1. Create a narrow scan config.
2. Index only source-authority documents first.
3. Run `mdex doctor`.
4. Try `mdex start "<real task>"`.
5. Add metadata to the few missing entrypoints.
6. Keep task history, memory, evals, and raw logs in separate lanes.

## What To Put In The Main Repo Index

Good first-pass candidates:

- README and agent/operator instructions
- current architecture and design docs
- active runbooks
- decision records
- update and release policies
- small representative examples

Usually avoid:

- large fixtures
- eval case corpora
- generated runtime output
- raw logs
- task history as a complete corpus
- chat or memory dumps
- old investigation notes

The detailed policy is `docs/context_hygiene.md`.

## Minimal Config

```json
{
  "scan_roots": ["."],
  "include_extensions": [".md"],
  "exclude_patterns": [
    ".git/**",
    ".mdex/**",
    "node_modules/**",
    "outputs/**",
    "tmp/**",
    "*.local.json",
    "**/*.local.json",
    ".env*",
    "**/.env*",
    "secrets.*",
    "credentials.*",
    "tests/fixtures/**",
    "fixtures/**",
    "**/eval/**",
    "**/evals/**",
    "**/logs/**",
    "**/dumps/**",
    "**/raw_logs/**"
  ],
  "output_file": ".mdex/mdex_index.json"
}
```

Start with Markdown because most repos keep first-pass guidance in README, runbooks, design docs, and decision records. Add `.json` or `.jsonl` later only for small, intentional metadata files that should influence entrypoint selection.

## Metadata Strategy

Start with the files that should win first-pass ranking:

```yaml
type: design | decision | reference | spec | task
project: your-project
status: active | draft | pending | done | archived
updated: 2026-04-30
tags: [entrypoint, runtime]
depends_on:
  - docs/other-prerequisite.md
relates_to:
  - docs/nearby-context.md
```

Do not add metadata everywhere on day one. A few authoritative entrypoints are more useful than a noisy complete corpus.

## Validation Loop

Run:

```bash
mdex scan --root . --config control/scan_config.json
mdex doctor --db .mdex/mdex_index.db
mdex start "a real task from your backlog" --db .mdex/mdex_index.db --limit 5
mdex context "same task" --db .mdex/mdex_index.db --actionable --limit 5
```

Then ask:

- Did the first two documents make the task safer?
- Did old or generated material appear too high?
- Did `doctor` flag paths that should move to a separate index?
- Are missing results caused by absent summaries, absent frontmatter, or overly broad excludes?

## Separate Indexes

Use separate indexes when the question is not "where should I start reading?"

Examples:

- Task history index: "Has a similar task happened before?"
- Memory index: "What past user preference or session fact matters?"
- Eval index: "Which cases cover this behavior?"
- Raw logs: read directly when investigating a concrete incident.

Separate indexes keep the main repo index small enough to guide first-pass judgment.

## Adoption Checklist

- `control/scan_config.json` exists and excludes generated/high-volume paths.
- `.mdex/` and database artifacts are ignored by git.
- README or agent instructions mention `mdex scan`, `start`, `context --actionable`, and `doctor`.
- At least the main design/runbook docs have `type`, `status`, and `updated`.
- `mdex doctor` is clean or its warnings are intentional.
- A real task produces a plausible `recommended_read_order`.
