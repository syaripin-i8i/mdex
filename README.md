# mdex

**`mdex` は AI エージェントのための protocol-first CLI です。人間向けの閲覧 UX は優先しません。**  
**想定する公開先は「誰でも触る OSS」ではなく、分かる相手が AI に読ませて使う repo です。**

## For Agents

- standard flow: `scan -> start -> (context | first | related | impact) -> finish --dry-run`
- apply summaries only with `finish --summary-file <path> --scan`
- success JSON goes to stdout; error JSON goes to stderr
- read next: `README.md` -> `AGENT.md` -> `docs/design.md` -> `docs/convention.md`

## Protocol

これは紹介文ではなく、AI が最初に従う標準手順です。

| phase | standard command | contract |
|---|---|---|
| before work | `mdex scan`, then `mdex start` | 索引を更新してから入口を決める |
| during work | `mdex context --actionable` / `mdex first` / `mdex related` | 必要な深掘りだけ追加する |
| when changed files exist | `mdex impact` | changed files 起点で関連文書を分類する |
| after work | `mdex finish --dry-run` | 更新候補と後処理を確認する |
| apply summary | `mdex finish --summary-file <path> --scan` | summary が実在するときだけ反映する |

```bash
mdex scan --root <dir> --db <db> --config control/scan_config.json
mdex start "<task>" --db <db>
mdex context "<task>" --db <db> --actionable
mdex first <node-id> --db <db> --limit 5
mdex related <node-id> --db <db> --limit 5
mdex impact <changed-file-or-node> --db <db>
mdex finish --task "<task>" --db <db> --dry-run
mdex finish --task "<task>" --db <db> --summary-file ./summary.txt --scan
```

## Assumptions

- `mdex` は規約のある Markdown / JSON 運用で最も強く効きます
- frontmatter の `type`, `status`, `updated` を推奨します
- `depends_on` と `relates_to` を使うと `start` / `context` / `impact` の解像度が上がります
- 先頭 summary があると `start` / `context` / `finish` が安定します
- 新規ノートは `mdex new`、`updated` 更新は `mdex stamp` を優先してください

> mdex is only as good as your note discipline.  
> For stable results, follow `docs/convention.md`.

## Non-goals

- 全文検索の代替ではありません
- source code understanding の完全代替ではありません
- ノート群を魔法のように整理するツールではありません
- 人間向け閲覧 UX を主目的にしません
- 規約の薄い repo で高精度を保証しません
- 汎用ナレッジベースではなく、agent workflow を圧縮するための CLI です

## Command Selection Rules

| situation | use | default reading |
|---|---|---|
| start a task | `mdex start` | 入口を決める |
| need a wider actionable entrance | `mdex context --actionable` | `start` 相当を直接確認する |
| inspect read order from a node | `mdex first` | 特定文書から読む順を決める |
| inspect nearby docs | `mdex related` | 関連文書を探す |
| changed files already exist | `mdex impact` | 影響範囲を changed files 起点で見る |
| close a task | `mdex finish --dry-run` | 出口を先に確認する |
| apply a summary | `mdex finish --summary-file ... --scan` | summary が実在するときだけ反映する |
| create a task or decision note | `mdex new` | frontmatter を手書きしない |
| update only `updated` | `mdex stamp` | metadata 更新だけに留める |

## 再現サンプル（fixtures/quality_repo）

この節は protocol の補助資料です。`tests/fixtures/quality_repo` で `scan -> start -> impact -> finish` を再現できます。

```bash
mdex scan --root tests/fixtures/quality_repo --db .tmp_quality.db
mdex start "root decision" --db .tmp_quality.db --limit 5
mdex impact design/root.md --db .tmp_quality.db
mdex finish --task "root fix" --db .tmp_quality.db --dry-run
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
  "recommended_read_order": [
    { "id": "spec/b.md" },
    { "id": "decision/a.md" },
    { "id": "design/root.md" }
  ],
  "recommended_next_actions": [
    "open spec/b.md",
    "open decision/a.md",
    "search code for root decision"
  ]
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

```json
{
  "task": "root fix",
  "dry_run": true,
  "changed_files": [],
  "enrich_candidates": [],
  "requires_manual_targeting": false
}
```

## 全出力は JSON

### Output Contract

すべての成功出力は stdout JSON、失敗出力は stderr JSON です。  
field 名は prose より強い契約として扱ってください。別名は導入しません。

| command | primary keys |
|---|---|
| `scan` | `nodes`, `edges.total`, `edges.resolved`, `edges.unresolved`, `edges.resolution_rate` |
| `start` | `task`, `recommended_read_order`, `recommended_next_actions`, `confidence` |
| `impact` | `inputs`, `read_first`, `related_tasks`, `decision_records`, `stale_watch` |
| `finish` | `task`, `dry_run`, `changed_files`, `enrich_candidates`, `requires_manual_targeting` |
| db resolution error | `error`, `resolution_attempts` |

```json
{ "error": "db not found", "resolution_attempts": [] }
```

人間向け整形が必要な場合だけ `--format table` を使ってください。

## DB Resolution

`--db` 省略時は以下の優先順で自動解決されます。

1. CLI 引数 `--db`
2. 環境変数 `MDEX_DB`
3. `.mdex/config.json` の `db`
4. `repo/.mdex/mdex_index.db`
5. `repo/mdex_index.db`

## Source of Truth

| scope | source |
|---|---|
| workflow contract | `README.md` |
| execution heuristics | `AGENT.md` |
| architecture / persistence / schema | `docs/design.md` |
| input note contract | `docs/convention.md` |

`docs/phase_a_agent_flow.md` は背景設計の詳細であり、入口契約の正本ではありません。

## Setup

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"
```

依存: Python 3.10+, PyYAML

## Quick Verification

```bash
mdex scan --root tests/fixtures/quality_repo --db .tmp_quality.db
mdex start "root decision" --db .tmp_quality.db --limit 5
mdex impact design/root.md --db .tmp_quality.db
mdex finish --task "root fix" --db .tmp_quality.db --dry-run
python -m pytest -q
```
