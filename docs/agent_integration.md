# Agent Integration

This document is for AI agents and orchestration code that call `mdex` as a subprocess.

## CLI Execution Model

Run `mdex` as a normal CLI process and treat each invocation as a complete request/response exchange. The default safe flow is `scan -> start -> context -> impact -> finish`, but agents can often use the shortest path documented in `AGENT.md`.

Use `--db <path>` when the database path is known. If DB resolution fails, run `mdex scan` from the repository root or pass an explicit `--db`.

## Stdout and Stderr

Successful commands write one of four documented formats to stdout:

- schema-backed JSON objects: `scan` / `scan-artifacts`, `doctor`, `status`, `start`, `context`, `impact`, `finish`;
- unwrapped utility JSON: arrays from `list`, `find`, `orphans`, and `stale`, plus objects from `query`, `first`, `related`, `enrich`, `new`, and `stamp`;
- tab-separated rows when a supported command uses `--format table`;
- source text from `open`.

Every failure writes a schema-backed JSON object to stderr and returns a non-zero exit code.

Do not parse human prose from stderr. Read the JSON `error`, `detail`, and `resolution_attempts` fields when present.

One success case also uses stderr: when `find` searches and matches nothing, stdout keeps its contract (`[]` for json, empty output for `--format table`) and stderr carries one `{"zero_hits": ...}` JSON line with exit `0`. The exit code is the source of truth for success; never merge stdout and stderr in machine processing. Distinguish the disclosure from failures by the exit code and the `zero_hits` key (error payloads carry `error` and `code`).

## Zero Hits Are Not Absence

`find` and `context` match node metadata only (title / tags / summary / search_terms), never full body text — a documented non-goal. When a searched query matches nothing, the payload includes `zero_hits` (`lanes_searched`, `lanes_inactive`, `caveat`, `remediation`; shared vocabulary with cdex). `lanes_inactive` is always present (`{}` when every known lane was searched) and its reason tokens are an open set — do not reject unknown tokens. `remediation` is descriptive prose; executable commands come from the structured argv surfaces (`recommended_next_actions_v2`, `suggested_rg`). Never conclude from an empty result that a document or wiring does not exist: search body text with `rg`, or add the term to the document's frontmatter tags to make it findable (`cdex search` covers code prior art where cdex is available).

## Exit Codes

Treat exit code `0` as success. Treat any non-zero exit code as failure, even if stdout contains text.

Recovery should be command-specific:

- DB not found: run `mdex scan` or pass `--db`.
- Low confidence: use `recommended_next_actions_v2` or `suggested_rg`.
- Stale index: run `mdex scan`.
- Manual targeting required: use explicit `mdex enrich <node-id> --summary-file <path>`.

## JSON Parsing

Select the parser from the command and `--format` before reading stdout. Parse schema-backed and utility JSON as JSON; do not JSON-parse table output or `open` source text. Error stderr is always JSON.

Schema-backed success and error payloads include:

```json
{
  "contract_schema": "https://github.com/syaripin-i8i/mdex/schemas/start.schema.json",
  "contract_version": "0.6.0"
}
```

Utility JSON intentionally has no `contract_schema` or `contract_version` wrapper. Consumers should ignore unknown object fields for forward compatibility and must not infer the output category merely from metadata absence.

## Schema Validation

Schemas live in `schemas/`. Use `contract_schema` to select the expected schema only for the schema-backed command set above. Since 0.3.x, `contract_schema` and `contract_version` are required by those success schemas and by the error schema. `scan-artifacts` shares `scan.schema.json`.

`contract_schema` is a stable logical identifier, not a mutable-branch download location. Pair it with `contract_version`. For a published release, derive the immutable validation source as:

```text
https://raw.githubusercontent.com/syaripin-i8i/mdex/v{contract_version}/schemas/{schema_filename}
```

Only resolve versions that have a published `v{contract_version}` tag. For unreleased checkout changes, validate the local `schemas/` copy; installed scan configuration validation uses the packaged `scan_config.schema.json`.

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

`full` is the default and preserves the existing `actionable_digest` shape, plus optional lanes such as `relevant_artifacts` when artifact indexes are included.
`minimal` returns only `intent`, `relevant_docs`, `suggested_rg`, and `context_gaps` to reduce context usage.

Use `minimal` when the agent only needs a short bridge into docs and exact search.
Use `full` when task history, artifact observations, likely code entrypoints, and known guardrails are needed.

## Artifact Indexes

Generated observations should stay out of the main repo index.
Use a separate artifact lane when ignored `outputs/` content is relevant:

```bash
mdex scan-artifacts --root outputs --db .mdex/artifacts.db
mdex context "<task>" --include repo,artifacts --actionable
```

Artifact rows include `metadata` and `freshness` so agents can distinguish current observations from stale history.
Artifact freshness accepts a default and kind-specific overrides:

```json
{
  "indexes": {
    "artifacts": {
      "roots": [
        "outputs/",
        {
          "path": "/absolute/private/artifact/root",
          "id_prefix": "private/artifacts",
          "expose_source_root": false
        }
      ],
      "include_globs": ["**/*.json", "**/*.jsonl", "**/*.md", "**/*.txt"],
      "exclude_globs": ["**/raw_logs/**", "**/quarantine/**", "**/runtime_state/**"],
      "stale_after_days": 14,
      "stale_after_days_by_kind": {
        "voice_monitor": 3,
        "investigation": 14,
        "eval_result": 7
      },
      "index_stale_after_hours": 24,
      "max_file_size_bytes": 26214400,
      "max_jsonl_rows_read": 20
    }
  }
}
```

Use object roots for repository-external paths. `id_prefix` gives stable node ids, and `expose_source_root: false` prevents absolute local roots from appearing in artifact metadata or scan output.

When `--include repo,artifacts` is used, `multi_index.indexes.artifacts.artifacts_index_age` reports the artifact index freshness. If the artifact DB is missing or older than `index_stale_after_hours`, `recommended_next_actions_v2` includes a structured `mdex scan-artifacts --db ...` action. Agents should surface the action, not auto-run it.

`scan-artifacts` treats files that disappear during a scan, malformed artifacts, and files over `max_file_size_bytes` as warnings rather than fatal errors.
The scan payload includes `warning_summary` so agents can report counts without reading every warning row.

## Common Recovery Loops

When `start` returns `health.reusable == false`, inspect `health.reason`, run `mdex scan`, then rerun `mdex start "<task>"`. `index_status.fresh` remains as a compatibility projection. Non-reusable read-order candidates and prompt packs are explicitly marked unverified.

When `confidence < 0.6`, run the structured `mdex find` action if present, or execute `suggested_rg` safely.

When a payload carries `zero_hits`, treat the result as "not indexed under this term", not "does not exist"; follow its `remediation` before concluding anything.

When changed files exist after edits, run `mdex impact --changed-files-from-git`.

Before closing work, run `mdex finish --task "<task>" --dry-run`. Apply summaries only when an intentional summary file exists.

## Refresh After Edits

Use `tools/context_refresh.py` when changed files may affect one or more context indexes:

```bash
python tools/context_refresh.py --dry-run
python tools/context_refresh.py docs/design.md tasks/T20260101010101.md
```

The script classifies changed files into repo/task/memory refresh targets. Unless
`--dry-run` is set, it refreshes the main repo index and the documented task-history
index when either lane is affected. Memory refresh remains repo-wrapper specific and
is reported without being executed.

## Local Telemetry

Telemetry is opt-in and local only. Enable it with either:

```bash
MDEX_TELEMETRY=1 mdex start "<task>"
```

or:

```json
{
  "telemetry": true
}
```

Events append to `.mdex/telemetry.jsonl` and follow `schemas/telemetry_event.schema.json`. They are designed as eval fuel, not analytics: mdex does not send telemetry anywhere.

Telemetry events avoid raw task/query text, raw argv values, absolute repo paths, and `suggested_rg.pattern`. Use fields such as `command`, `duration_ms`, `confidence`, `result_size`, `suggested_rg_count`, and `code` for aggregate analysis.
