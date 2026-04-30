# Agent Integration

This document is for AI agents and orchestration code that call `mdex` as a subprocess.

## CLI Execution Model

Run `mdex` as a normal CLI process and treat each invocation as a complete request/response exchange. The default safe flow is `scan -> start -> context -> impact -> finish`, but agents can often use the shortest path documented in `AGENT.md`.

Use `--db <path>` when the database path is known. If DB resolution fails, run `mdex scan` from the repository root or pass an explicit `--db`.

## Stdout and Stderr

Successful commands write JSON to stdout. Failure commands write JSON to stderr and return a non-zero exit code.

Do not parse human prose from stderr. Read the JSON `error`, `detail`, and `resolution_attempts` fields when present.

## Exit Codes

Treat exit code `0` as success. Treat any non-zero exit code as failure, even if stdout contains text.

Recovery should be command-specific:

- DB not found: run `mdex scan` or pass `--db`.
- Low confidence: use `recommended_next_actions_v2` or `suggested_rg`.
- Stale index: run `mdex scan`.
- Manual targeting required: use explicit `mdex enrich <node-id> --summary-file <path>`.

## JSON Parsing

Parse stdout or stderr as JSON before making decisions. New success and error payloads include:

```json
{
  "contract_schema": "https://github.com/syaripin-i8i/mdex/schemas/start.schema.json",
  "contract_version": "0.3.0"
}
```

Consumers should ignore unknown fields for forward compatibility.

## Schema Validation

Schemas live in `schemas/`. Use `contract_schema` to select the expected schema. In 0.3.x, `contract_schema` and `contract_version` are required in success and error schemas.

Error payloads include a machine-readable `code` field. Switch on `code`; display `error` / `detail` to humans.

Common codes:

| code | recovery |
|---|---|
| `db_not_found` | run `mdex scan` or pass `--db` |
| `invalid_arguments` | fix argv construction before retrying |
| `context_selection_failed` | retry with a narrower query or refresh the DB |
| `not_a_git_repository` | omit `--changed-files-from-git` or run inside a git repo |
| `summary_file_not_found` | create/pass the intended summary file |
| `node_not_indexed` | run `mdex scan` or use an indexed node id |

## Structured Actions

Prefer `recommended_next_actions_v2` over `recommended_next_actions`. The v1 string array is deprecated and kept only for 0.2.x compatibility.

Treat structured actions as data. Validate the command before execution, and pass `command` plus `args` as an argv array.

```python
import subprocess

allowed = {"rg", "mdex"}

def run_action(action):
    command = action["command"]
    args = action.get("args", [])
    if command not in allowed:
        raise ValueError(f"command not allowed: {command}")
    return subprocess.run(
        [command, *args],
        check=False,
        text=True,
        capture_output=True,
    )
```

## Safe `suggested_rg`

`actionable_digest.suggested_rg` uses the same structured execution model.

- Treat `command` and `args` as argv array.
- Do not join them into a shell string.
- Do not use `shell=True`.
- Validate command allowlist before executing.
- Recommended allowlist: `rg`, `mdex`, possibly `python` only for local test commands if intentionally allowed.
- Preserve path boundaries. Do not rewrite suggested paths into absolute paths outside the repo.

`pattern` and `paths` are explanatory fields. Execute `command` with `args`.

## Minimal vs Full Digest

`mdex start` and `mdex context --actionable` accept:

```bash
--digest minimal
--digest full
```

`full` is the default and preserves the existing `actionable_digest` shape. `minimal` returns only `intent`, `relevant_docs`, `suggested_rg`, and `context_gaps` to reduce context usage.

Use `minimal` when the agent only needs a short bridge into docs and exact search. Use `full` when task history, likely code entrypoints, and known guardrails are needed.

## Common Recovery Loops

When `start` returns `index_status.fresh == false`, run `mdex scan`, then rerun `mdex start "<task>"`.

When `confidence < 0.6`, run the structured `mdex find` action if present, or execute `suggested_rg` safely.

When changed files exist after edits, run `mdex impact --changed-files-from-git`.

Before closing work, run `mdex finish --task "<task>" --dry-run`. Apply summaries only when an intentional summary file exists.
