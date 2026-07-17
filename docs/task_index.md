---
type: reference
project: mdex
status: active
updated: 2026-07-10
---

# Task History Index

Completed task history belongs in a separate index so it does not outrank current
design, decision, and runbook documents in the main repository index.

Build the main and task-history indexes independently:

```bash
mdex scan --root . --config control/scan_config.json
mdex scan --root tasks --node-id-root . --config control/task_scan_config.json \
  --db .mdex/task_history.db --output .mdex/task_history.json
```

The task config keeps node ids rooted at the repository, such as
`tasks/T20260101010101.md`. Query the task lane only when historical work is useful:

```bash
mdex context "similar runtime controller task" --include repo,task --actionable
```

`tools/context_refresh.py` refreshes this index when a path under `tasks/` changes.

Set `.mdex/config.json` `indexes.task.db` when the task DB uses another name. The
shared multi-index resolver is authoritative for `status`, `context`, and `start`.
For compatibility it also recognizes an existing `.mdex/task_index.db` as the
legacy task lane and reports `source: legacy:task`; new setups should use the
configured path or `.mdex/task_history.db`.
