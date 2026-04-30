---
type: reference
project: mdex
status: active
updated: 2026-04-30
---

# Context Hygiene Policy

`mdex` is an entrypoint and context-selection tool, not a warehouse.

Putting a file in the main repo index means it can enter an assistant's first-pass view. The default repo index should therefore favor documents that improve early judgment:

- current design intent
- entrypoints and runbooks
- maps of which files to inspect
- decision rules, boundaries, and operating policy
- small representative examples

The main repo index should avoid high-volume or historical material that can bury current guidance:

- large JSON fixtures
- complete eval case sets
- runtime output
- full task history
- raw memory data
- old investigation logs
- generated artifacts, failure logs, and observation dumps

These files can still be valuable. They should usually be read directly, or placed in a separate purpose-built index such as a task-history index or memory index.

## Practical Rules

- Use `control/scan_config.json` to keep the public repo index focused on active source authority.
- Use `mdex doctor` to detect local/secret files, old/archive paths, fixture/eval/log/dump paths, JSON/SQLite drift, and orphan overrides.
- Keep examples small when they belong in the main index.
- Prefer a separate index when the data answers a different question than "where should I start reading?"

## Why

`mdex context` ranks things that appear related. If fixtures, eval corpora, logs, and historical dumps are always present, they can outrank thinner but more authoritative guidance. That creates a failure mode where the assistant appears to read deeply while missing the actual entrypoint.
