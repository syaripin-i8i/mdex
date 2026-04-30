# mdex
[![CI](https://github.com/syaripin-i8i/mdex/actions/workflows/ci.yml/badge.svg)](https://github.com/syaripin-i8i/mdex/actions/workflows/ci.yml)

**`mdex` は AI エージェント向けの protocol-first CLI です。**

- 標準フロー: `scan -> start -> (context | first | related | impact) -> finish --dry-run`
- 成功は `stdout` JSON、失敗は `stderr` JSON（`exit != 0`）
- field 名は prose より強い契約（別名を導入しない）
- primary keys は「Output Contract」表を参照

## For Agents

- first read order: `README.md -> AGENT.md -> docs/design.md -> docs/convention.md`
- read order と source of truth は同義ではない（正本は本 README の Source of Truth 表）
- `start` と `context --actionable` の詳細な分岐は `AGENT.md` を正本とする
- agents should prefer `recommended_next_actions_v2`; `recommended_next_actions` is deprecated but kept for 0.2.x compatibility
- use `--digest minimal` on `start` / `context --actionable` to reduce context use when the full `actionable_digest` is not needed
- JSON payloads require `contract_schema` / `contract_version`; error payloads also include machine-readable `code`

## For New Adopters

- 10分で試す: `docs/getting_started.md`
- 既存 repo へ入れる: `docs/adoption_guide.md`
- 失敗例と改善例を見る: `docs/examples_before_after.md`
- main index に入れるものを決める: `docs/context_hygiene.md`

## Where mdex Fits

`mdex` は「最初に何を読むべきか」を決めるための薄い index です。

| tool | best at | mdex relationship |
|---|---|---|
| `ripgrep` / full-text search | exact string search across source | `mdex` can recommend where to search, but does not replace it |
| codegraph tools | symbol and dependency structure | `mdex` points to docs and decisions; codegraph explains code topology |
| embedding/RAG systems | broad semantic recall over large corpora | `mdex` favors small, deterministic, contract-shaped context |
| knowledge graphs | rich typed relationships | `mdex` keeps only lightweight links such as `depends_on` and `relates_to` |

Use `mdex` for first-pass judgment and workflow contracts. Use the other tools for deep code search, broad recall, or detailed graph analysis.
In other words: `mdex` is the compass before `rg`, not a replacement for `rg`.

## Protocol

| phase | standard command | contract |
|---|---|---|
| before work | `mdex scan`, then `mdex start` | 索引を更新してから入口を決める |
| during work | `mdex context --actionable` / `mdex first` / `mdex related` | 必要な深掘りだけ追加する |
| when changed files exist | `mdex impact` | changed files 起点で関連文書を分類する |
| after work | `mdex finish --dry-run` | 更新候補と後処理を確認する |
| apply summary | `mdex finish --summary-file <path> --scan` | summary が実在するときだけ反映する |

```bash
mdex scan --root <dir> --config control/scan_config.json
mdex start "<task>" --db <db>
mdex context "<task>" --db <db> --actionable
mdex first <node-id> --db <db> --limit 5
mdex related <node-id> --db <db> --limit 5
mdex impact <changed-file-or-node> --db <db>
mdex finish --task "<task>" --db <db> --dry-run
mdex finish --task "<task>" --db <db> --summary-file ./summary.txt --scan
```

`scan` の既定出力は `.mdex/mdex_index.db` と `.mdex/mdex_index.json`。  
`--db` / `--output` 指定時はそれを優先します。

## Command Selection Rules

短縮版の判断表です。分岐の詳細は `AGENT.md` を参照してください。

| situation | use | contract |
|---|---|---|
| start a task | `mdex start` | 入口を決める |
| start 後に実行可能な次アクションを広く取りたい | `mdex start` -> `mdex context --actionable` | 典型シーケンス（詳細は `AGENT.md`） |
| entrance candidate is already known | `mdex context --actionable` | `start` を省略して時短 |
| inspect from a node | `mdex first` / `mdex related` | 特定文書から読む順と周辺文脈を見る |
| open a node body | `mdex open <node-id>` | indexed node id のみ。絶対パスと `..` は拒否 |
| changed files already exist | `mdex impact` | 影響範囲を changed files 起点で見る |
| close a task | `mdex finish --dry-run` | 出口を先に確認する |
| apply a summary | `mdex finish --summary-file ... --scan` | summary が実在するときだけ反映する |
| update `updated` metadata | `mdex stamp <node-id>` | indexed node id のみ。scan_roots の包含外は拒否 |

## Assumptions

入力ノート規約の正本は `docs/convention.md` です。

- frontmatter の `type` / `status` / `updated` を推奨
- 前提は `depends_on`、関連は `relates_to`
- 先頭 summary があるほど `start` / `context` / `finish` が安定

## Non-goals

- 全文検索の代替
- source code understanding の完全代替
- 規約の薄い repo での高精度保証
- 人間向け閲覧 UX の最適化

## 再現サンプル（fixtures/quality_repo）

詳細サンプルは `docs/examples.md`。ここでは契約確認に必要な最小例のみ記載します。

```bash
mdex scan --root tests/fixtures/quality_repo --db .mdex/quality_example.db --output .mdex/quality_example.json
mdex start "root decision" --db .mdex/quality_example.db --limit 5
mdex impact design/root.md --db .mdex/quality_example.db
mdex finish --task "root fix" --db .mdex/quality_example.db --dry-run
```

期待される出力（簡略）:

```json
{
  "nodes": 6,
  "edges": {
    "total": 8,
    "resolved": 6,
    "unresolved": 2,
    "resolution_rate": 75.0
  }
}
```

```json
{
  "task": "root decision",
  "index_status": {
    "fresh": true
  },
  "entrypoint_reason": "ranked_entrypoint_available",
  "recommended_read_order": [
    { "id": "spec/b.md" },
    { "id": "decision/a.md" },
    { "id": "design/root.md" }
  ],
  "recommended_next_actions": [
    "open spec/b.md",
    "open decision/a.md",
    "search code for root decision"
  ],
  "recommended_next_actions_v2": [
    { "command": "mdex", "args": ["open", "spec/b.md"], "reason": "read the recommended node first" }
  ],
  "actionable_digest": {
    "intent": "root decision",
    "relevant_docs": [
      {
        "id": "spec/b.md",
        "title": "Spec B",
        "type": "spec",
        "status": "active",
        "reason": "direct depends_on"
      },
      {
        "id": "decision/a.md",
        "title": "Decision A",
        "type": "decision",
        "status": "active",
        "reason": "high lexical or graph score"
      }
    ],
    "relevant_task_history": [],
    "likely_code_entrypoints": [],
    "known_guardrails": [
      {
        "id": "decision/a.md",
        "title": "Decision A",
        "type": "decision",
        "status": "active",
        "reason": "mentions constraint"
      }
    ],
    "suggested_rg": [
      {
        "command": "rg",
        "args": ["-n", "root|decision", "spec", "decision", "design"],
        "pattern": "root|decision",
        "paths": ["spec", "decision", "design"],
        "reason": "expand from mdex entrypoint candidates into exact source matches"
      }
    ],
    "context_gaps": [
      "no indexed code entrypoint found; use suggested rg to bridge into source code"
    ]
  }
}
```

```json
{
  "inputs": ["design/root.md"],
  "read_first": [
    { "id": "design/root.md" }
  ],
  "related_tasks": [
    { "id": "tasks/pending/T20260101000001.md" }
  ],
  "decision_records": [
    { "id": "decision/a.md" }
  ]
}
```

`changed_files: []` / `enrich_candidates: []` は「該当なしで正常終了」の意味です。

```json
{
  "status": "success",
  "task": "root fix",
  "dry_run": true,
  "noop": true,
  "noop_reason": "dry-run completed with no changed files and no enrich candidates",
  "changed_files": [],
  "enrich_candidates": [],
  "requires_manual_targeting": false
}
```

## 全出力は JSON

### Output Contract

成功と失敗の判別ルール: **成功 = stdout JSON（空配列でも成功） / 失敗 = stderr JSON + exit != 0**。

| command | primary keys |
|---|---|
| `scan` | `nodes`, `edges.total`, `edges.resolved`, `edges.unresolved`, `edges.resolution_rate` |
| `start` | `task`, `index_status`, `entrypoint_reason`, `recommended_read_order`, `recommended_next_actions`, `recommended_next_actions_v2`, `actionable_digest`, `confidence` |
| `context` | `query`, `recommended_read_order`, `recommended_next_actions`, `recommended_next_actions_v2`, `actionable_digest`, `deferred_nodes`, `confidence` |
| `doctor` | `status`, `summary`, `checks`, `recommended_next_actions` |
| `impact` | `inputs`, `read_first`, `related_tasks`, `decision_records`, `stale_watch` |
| `finish` | `status`, `task`, `dry_run`, `noop`, `noop_reason`, `changed_files`, `enrich_candidates`, `requires_manual_targeting` |
| db resolution error | `code`, `error`, `resolution_attempts` |

`finish --dry-run` の成功判定:

- `dry_run: true` は preview 実行（DB 更新なし）
- `status: "success"` かつ `noop: true` は「正常な no-op 完了」
- `changed_files`, `enrich_candidates` が空でも成功
- `requires_manual_targeting: true` のときは `mdex enrich <node-id> --summary-file <path>` を明示ターゲットで実行

```json
{
  "contract_schema": "https://github.com/syaripin-i8i/mdex/schemas/error.schema.json",
  "contract_version": "0.3.0",
  "code": "db_not_found",
  "error": "db not found",
  "resolution_attempts": []
}
```

人間向け整形が必要な場合だけ `--format table` を使用してください。

### Schema Contracts

機械可読契約は `schemas/` を正本とします。

- `schemas/scan.schema.json`
- `schemas/start.schema.json`
- `schemas/context.schema.json`
- `schemas/doctor.schema.json`
- `schemas/impact.schema.json`
- `schemas/finish.schema.json`
- `schemas/error.schema.json`

schema 版運用は `docs/schema_versioning.md` を参照してください。
Agent integration guidance, including safe argv execution for structured actions and `suggested_rg.args`, is in `docs/agent_integration.md`.

## DB Resolution

`--db` 省略時は次の優先順で解決します。

1. CLI 引数 `--db`
2. 環境変数 `MDEX_DB`
3. `.mdex/config.json` の `db`
4. `repo/.mdex/mdex_index.db`
5. `repo/mdex_index.db`

## Public Scan Config

公開向け既定 config は `control/scan_config.json`。

- `scan_roots` は `"."`（repo root 前提）
- `output_file` は `.mdex/mdex_index.json`
- `.mdex/**` は scan 対象外
- fixtures / evals / logs / dumps / archive は通常の repo index から除外し、必要時に直接読むか専用 index を使う

```bash
mdex scan --root . --config control/scan_config.json
```

詳しい方針は `docs/context_hygiene.md` を参照してください。

## Source of Truth

read order と source of truth は同義ではありません。  
上から読む順は `For Agents`、正本はこの表で固定します。

| scope | source |
|---|---|
| workflow contract | `README.md` |
| execution heuristics | `AGENT.md` |
| first adoption path | `docs/getting_started.md` |
| existing repo adoption | `docs/adoption_guide.md` |
| before/after examples | `docs/examples_before_after.md` |
| architecture / persistence / schema | `docs/design.md` |
| input note contract | `docs/convention.md` |
| context hygiene policy | `docs/context_hygiene.md` |
| agent integration | `docs/agent_integration.md` |
| update / versioning policy | `docs/update_policy.md` |
| schema versioning policy | `docs/schema_versioning.md` |

`docs/archive/phase_a_agent_flow.md` は historical planning doc であり、入口契約の正本ではありません。

## Project Operations

- Security policy: [SECURITY.md](SECURITY.md)
- Contributing guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Support matrix: [docs/support_matrix.md](docs/support_matrix.md)
- Release process: [docs/release_process.md](docs/release_process.md)

## Setup

Current GitHub source install:

```bash
python -m pip install git+https://github.com/syaripin-i8i/mdex.git
```

Local checkout install:

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"
```

After public PyPI publication:

```bash
python -m pip install mdex-cli
```

ロック依存で開発環境を再現する場合:

```bash
python -m pip install --upgrade pip
python .github/scripts/install_from_pylock.py --lock pylock.toml --editable .
```

`pylock.toml` 更新:

```bash
python -m pip lock -e ".[dev]" -o pylock.toml
python .github/scripts/export_release_hashes.py --lock pylock.toml --output .github/locks/pypi_release_hashes.json
```

matrix (`ubuntu/macos/windows x 3.10/3.11/3.12/3.13/3.14`) で hash install を維持するため、`pylock.toml` 更新時は  
`.github/locks/pypi_release_hashes.json` も同時更新してください。

Python support policy is documented in `docs/support_matrix.md`.

## Privacy Note

`.mdex/mdex_index.json` と `.mdex/mdex_index.db` には scan 対象ファイル由来の summary が含まれます。  
機微情報を含むファイルは `control/scan_config.json` の `exclude_patterns` で除外してください。

`scan` は local/secret 寄りのファイル（例: `.env*`, `*.local.md`, `*.local.json`, `*.local.jsonl`, `secrets.*`, `credentials.*`）を
デフォルトで除外します。特殊用途で `use_default_exclude_patterns: false` を指定して取り込む場合でも、
local/secret らしいファイルが index に入ると `warnings` に表示されます。
再 scan 時、現在の index に存在しない node の agent override は SQLite から削除されます。
`mdex doctor` は scan warnings、JSON/SQLite の生成時刻ズレ、orphan override、legacy artifact、
`old/`・`archive/`・fixtures/evals/logs/dumps などの review path が index に入っている状態を検出します。

## Artifact Hygiene

公開 repo ではランタイム生成物を追跡しません。

- `.mdex/`
- `outputs/`
- `tmp/`
- `*.db`, `*.sqlite`, `*.sqlite3`

## Quick Verification

```bash
mdex scan --root tests/fixtures/quality_repo --config control/scan_config.json
mdex doctor --db .mdex/mdex_index.db
mdex start "root decision" --db .mdex/mdex_index.db --limit 5
mdex impact design/root.md --db .mdex/mdex_index.db
mdex finish --task "root fix" --db .mdex/mdex_index.db --dry-run
python -m pytest -q
```

## License

`mdex` is licensed under Apache-2.0.

- License text: [LICENSE](LICENSE)
- Attribution notices: [NOTICE](NOTICE)
