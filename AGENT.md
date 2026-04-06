# mdex — Codex Agent Instructions

## What This Repo Is

`mdex` is a lightweight Markdown knowledge exploration harness.
It parses local Markdown files and produces AI-navigable index/graph JSON.
The goal is staged retrieval: AI reads index first, then opens only needed nodes.

```
mdex/
  runtime/      ← Python implementation
  control/      ← Config JSONs (scan paths, schema, filters)
  docs/         ← Design notes and decision records
  AGENT.md      ← This file
```

## Operational Facts

- Python: `<python>`
- Entry point (Phase 1): `runtime/cli.py`
- Compile check: `python -m py_compile runtime/cli.py`
- Primary output: `mdex_index.json` (nodes + edges)

## Core Design Rules

1. Phase 1 is parse-only. No AI calls, no DB, no server.
2. Input is a directory of Markdown files. Output is JSON.
3. Frontmatter (`---`) and `[[wikilink]]` are the primary relationship signals.
4. Never parse more than needed. index-first, open-on-demand.
5. Do not add a web server or MCP layer until Phase 2 is stable.

## Node Schema (Phase 1)

```json
{
  "id": "relative/path/to/file.md",
  "title": "...",
  "type": "decision|task|design|log|spec|reference|unknown",
  "project": "...",
  "status": "active|done|draft|archived|unknown",
  "summary": "first non-heading paragraph, max 3 sentences",
  "tags": [],
  "updated": "ISO date from frontmatter or mtime",
  "links_to": ["other/file.md"],
  "depends_on": ["other/file.md"],
  "relates_to": ["other/file.md"]
}
```

## Edge Schema (Phase 1)

```json
{
  "from": "file.md",
  "to": "file.md",
  "type": "links_to|depends_on|relates_to|decides|implements|blocks|mentions"
}
```

## Phase 1 Completion Criteria

- `python runtime/cli.py scan --root <dir>` walks all `.md` files
- Outputs `mdex_index.json` with nodes + edges arrays
- Extracts: title, type, project, status, tags, updated, wikilinks, frontmatter relations
- `python runtime/cli.py query --node <id>` prints single node + direct neighbors
- All output is valid JSON

## Verification Order

1. `git status --short`
2. `python -m py_compile runtime/cli.py`
3. `python runtime/cli.py scan --root docs/`
4. Inspect output JSON manually

## Non-Goals (Phase 1)

- AI-generated summaries
- Similarity / embedding
- Web UI
- MCP server
- Database persistence
