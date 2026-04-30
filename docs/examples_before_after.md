---
type: reference
project: mdex
status: active
updated: 2026-04-30
---

# Before And After Examples

These examples show the failure modes `mdex` is meant to make visible.

## Example 1: Everything Indexed

Initial config:

```json
{
  "scan_roots": ["."],
  "include_extensions": [".md", ".json", ".jsonl"],
  "exclude_patterns": [".git/**", ".mdex/**"]
}
```

Common result:

- fixture summaries match the query
- eval cases outrank design docs
- old task notes look current because they contain many matching words
- `context` spends budget on detail before finding the entrypoint

Better config:

```json
{
  "scan_roots": ["."],
  "include_extensions": [".md"],
  "exclude_patterns": [
    ".git/**",
    ".mdex/**",
    "tests/fixtures/**",
    "**/eval/**",
    "**/logs/**",
    "**/dumps/**"
  ]
}
```

Add `.json` / `.jsonl` only for known small metadata files that should guide first-pass judgment.

Then run:

```bash
mdex scan --root . --config control/scan_config.json
mdex doctor --db .mdex/mdex_index.db
```

Expected improvement:

- the main index is smaller
- `doctor` is quieter
- first-pass ranking favors README, runbooks, designs, and decisions

## Example 2: Important Entrypoint Is Unknown

Weak document:

```markdown
# Runtime Notes

Some notes about runtime behavior.
```

Problem:

- type is `unknown`
- status is `unknown`
- no updated timestamp
- title may be too vague

Improved document:

```markdown
---
type: design
project: example
status: active
updated: 2026-04-30
tags: [runtime, entrypoint]
depends_on:
  - docs/deployment.md
---

# Runtime Design

Current runtime boundaries and the files to inspect before changing controller behavior.
```

Expected improvement:

- `type_status` contributes to ranking
- tags make intentional matches easier
- prerequisites can be pulled into read order

## Example 3: Task History Pollutes Current Work

Symptom:

- `context "runtime controller"` returns many completed tasks
- active design docs are present but lower

Better layout:

- keep completed task history in a task-specific index
- keep the main repo index focused on active design, decisions, and runbooks
- use `status: done` for completed task nodes if they remain in the main index

Expected workflow:

```bash
mdex start "runtime controller change" --db .mdex/mdex_index.db
# only if needed:
mdex context "similar runtime controller task" --db .mdex/task_index.db --actionable
```

## Example 4: Doctor Finds A Warehouse Path

Example finding:

```json
{
  "name": "indexed_path_hygiene",
  "status": "warning",
  "findings": [
    {
      "path": "eval/cases.json",
      "message": "fixture/eval/log/dump-style path is indexed; prefer a separate index or direct reads"
    }
  ]
}
```

Good responses:

- add the path to `exclude_patterns`
- move it to a separate index
- leave it intentionally only if it is a small representative example

## Example 5: Sparse Repo

If a repo has almost no docs, `mdex` will not invent structure.

Start by adding:

- README with source authority and common commands
- one active design doc
- one runbook
- one decision record for the current architecture

Then scan again and run a real task query. The goal is not a large index; the goal is a useful first read order.
