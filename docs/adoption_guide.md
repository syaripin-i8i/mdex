---
type: reference
project: mdex
status: active
updated: 2026-07-11
---

# Adoption Guide

This guide is for adding `mdex` to an existing repository without turning the index into a warehouse.

## Adoption Goal

The main repo index should answer:

> What should an AI agent read first to make a good decision?

It should not contain every file that might someday be useful.

## Fit Check

A pilot is worthwhile when the repository already has maintained source-authority
documents and the team wants AI agents to choose among them before searching code.

Do not start a pilot merely to compensate for missing or stale documentation. Also
stop here if the desired product is full-text search, broad semantic recall, or a
human-facing knowledge base.

## Public-Preview Boundary

`mdex` is in public preview. A `0.x` minor release may tighten documented contracts.
Pin one version for the pilot, keep the index disposable, and review the changelog
before upgrading. A successful pilot is evidence for that repository and workflow,
not a claim of broad production relevance.

## One-Repo Pilot

This guide targets `mdex` 0.6.0. Pin the pilot with
`python -m pip install "mdex-cli==0.6.0"`. If evaluating a local checkout instead,
record the commit and do not compare results across different commits.

Choose one repository and 3–5 real, representative tasks. Before running `mdex`,
record for each task:

- the task statement;
- the one or two documents expected to guide the first decision;
- material that must not rank highly;
- the current way the agent finds its starting context.

Then:

1. Create a narrow Markdown-only scan config.
2. Index current source-authority documents.
3. Run `scan` and `doctor`.
4. Run `start` and `context --actionable` for every recorded task.
5. Record the first two entries in `recommended_read_order`, harmful noise, disclosed context gaps, and required config or document maintenance.
6. Make only bounded changes that you would be willing to maintain.
7. Repeat the same tasks and decide whether to continue.

## Continue Criteria

Continue adoption only when:

- the pre-recorded source-authority documents appear in the first two entries of `recommended_read_order`, or the output clearly discloses why the context is missing;
- stale, generated, fixture, and unrelated history material does not displace authoritative documents;
- the result gives the agent a useful next read or a bounded bridge into `rg`;
- the required config and metadata maintenance stays limited to intentional entrypoints;
- the team can identify an owner for index hygiene and version upgrades.

## Stop Criteria

Stop or defer adoption when:

- the pilot reveals that current source-authority documents do not exist;
- repeated tuning within the predeclared maintenance budget still produces misleading entrypoints;
- useful results require indexing the repository as an undifferentiated warehouse;
- metadata work expands into annotating most of the corpus;
- the result adds no useful reading order beyond the team's existing search workflow;
- the team cannot accept pinned public-preview contracts and reviewed upgrades.

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
