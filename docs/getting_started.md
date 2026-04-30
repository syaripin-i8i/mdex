---
type: reference
project: mdex
status: active
updated: 2026-04-30
---

# Getting Started

This guide is the shortest path to seeing whether `mdex` helps your repo.

`mdex` is not a replacement for full-text search. It is a small protocol-first index for choosing what an AI agent should read first.

## 1. Install

This page documents the current repository state. Some commands, including `mdex doctor`, are not in the 0.1.0 package yet. For the exact flow below, install from this checkout:

```bash
python -m pip install -e .
```

After the next release, the package install path is:

```bash
python -m pip install mdex-cli
```

Supported Python versions are documented in `docs/support_matrix.md`.

## 2. Try the Fixture Repo

From the mdex checkout:

```bash
mdex scan --root tests/fixtures/quality_repo --db .mdex/quality_example.db --output .mdex/quality_example.json
mdex start "root decision" --db .mdex/quality_example.db --limit 5
mdex context "root decision" --db .mdex/quality_example.db --actionable --limit 5
mdex doctor --db .mdex/quality_example.db --json-index .mdex/quality_example.json
```

Expected shape:

- `scan` reports node and edge counts.
- `start` returns `recommended_read_order`.
- `context --actionable` returns next actions plus `actionable_digest`, including relevant docs, task history, likely code entrypoints, known guardrails, suggested `rg`, and context gaps.
- `doctor` reports index hygiene issues or `status: "ok"`.

## 3. Try Your Repo

Start narrow. Do not index every generated file and fixture on the first pass.

If your repo does not have `control/scan_config.json`, create it before scanning:

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
    "tests/fixtures/**",
    "**/eval/**",
    "**/logs/**",
    "**/dumps/**"
  ],
  "output_file": ".mdex/mdex_index.json"
}
```

Then run:

```bash
mdex scan --root . --config control/scan_config.json
mdex doctor --db .mdex/mdex_index.db
mdex start "the task you are about to do" --db .mdex/mdex_index.db
```

Add `.json` / `.jsonl` only after you know which JSON files should influence first-pass judgment:

```json
{
  "include_extensions": [".md", ".json", ".jsonl"],
  "exclude_patterns": ["data/**", "logs/**", "fixtures/**"]
}
```

## 4. Add Just Enough Metadata

`mdex` works best when important entrypoint documents have frontmatter:

```markdown
---
type: design
project: example
status: active
updated: 2026-04-30
depends_on:
  - docs/runtime.md
---

# Runtime Design

Short summary of the current design intent.
```

You do not need to annotate every file. Start with README, AGENT-style rules, active design docs, runbooks, and decision records.

## 5. Interpret Results

- If `start` returns the right entrypoint, continue with `context --actionable`.
- If `actionable_digest.context_gaps` says no code entrypoint was indexed, run the suggested `rg` command instead of broadening the main index too early.
- If results are noisy, run `doctor` and tighten `exclude_patterns`.
- If results are sparse, add frontmatter and short summaries to the few documents that should guide first-pass judgment.
- If you need task history, memory, eval cases, or raw logs, prefer a separate index or direct reads.

## 6. Common First Fixes

- Exclude `fixtures/`, `eval/`, `logs/`, `dumps/`, and generated output.
- Mark current docs as `status: active`.
- Mark old docs as `status: archived` or move them out of the main index.
- Add `depends_on` only for true prerequisites.
- Keep summaries short and current.
