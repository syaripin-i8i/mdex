# mdex

**このツールは AI エージェント（Claude Code・Codex など）のために作られています。人間向けの対話 UX は優先しません。**

---

## 何を解くツールか

`mdex` は Markdown / JSON の運用コンテクストを索引化し、AI が「今読むべき文書」を最短で決めるための補助 CLI です。  
全文検索の代替ではなく、作業開始・作業終了の導線を揃えることを主目的にしています。

```
記録（Markdown / JSON）
  ↓ mdex scan
SQLite 索引
  ↓ mdex start
読む順序と次アクション
  ↓ 実装
  ↓ mdex finish
更新候補・後処理を整理
```

---

## タスク着手〜終了の標準フロー

```bash
# 1) 索引を作る / 更新する
mdex scan --root <dir> --db mdex_index.db --config control/scan_config.json

# 2) 作業開始時（入口）
mdex start "emotion bug fix" --db mdex_index.db

# 3) 必要なら深掘り
mdex context "emotion bug fix" --db mdex_index.db --actionable
mdex first design/emotion.md --db mdex_index.db --limit 5
mdex related design/emotion.md --db mdex_index.db --limit 5

# 4) 作業終了時（出口）
mdex finish --task "emotion bug fix" --db mdex_index.db --dry-run

# 5) summary を適用したいとき
mdex finish --task "emotion bug fix" --db mdex_index.db --summary-file ./summary.txt --scan
```

`--db` を省略した場合は以下の優先順で自動解決されます。

1. CLI 引数 `--db`
2. 環境変数 `MDEX_DB`
3. `.mdex/config.json` の `db`
4. `repo/.mdex/mdex_index.db`
5. `repo/mdex_index.db`

---

## 主なコマンド

| コマンド | 役割 |
|---|---|
| `scan` | 索引を生成/更新 |
| `start` | 開始時に読む順序と次アクションを返す |
| `context --actionable` | `start` 相当の詳細情報を直接取得 |
| `impact` | changed files 起点で関連ドキュメントを分類 |
| `finish` | 終了時の更新候補・後処理をまとめる |
| `enrich` | summary を node_overrides に反映 |
| `new` | task / decision テンプレートを生成 |
| `stamp` | frontmatter の `updated` を更新 |

---

## 再現サンプル（fixtures/quality_repo）

`tests/fixtures/quality_repo` で `scan → start → impact → finish` を再現できます。

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

成功時は stdout JSON、エラー時は stderr JSON を返します。

```json
{ "error": "db not found", "resolution_attempts": [] }
```

人間向け出力が必要な場合のみ `--format table` を使ってください。

---

## セットアップ

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"
```

依存: Python 3.10+, PyYAML

---

## モジュール構成

```
runtime/
  cli.py         コマンド入口
  dbresolve.py   DB 自動解決と repo/config 解決
  scanner.py     .md / .json / .jsonl 列挙
  parser.py      frontmatter / link / summary 抽出
  builder.py     ノード・エッジ生成
  indexer.py     JSON / SQLite 出力
  store.py       SQLite 読み書き API
  resolver.py    related / first
  context.py     context 選別（actionable 出力対応）
  start.py       start 出力構築
  gittools.py    git changed files 収集
  impact.py      changed files 起点の文書分類
  finish.py      finish 出口処理
  scaffold.py    new / stamp
  enrich.py      summary 更新
  reader.py      node-id から本文取得
  tokens.py      トークン見積もり
```

---

## 作業サイクル

```
記録する       docs/convention.md に沿って Markdown / JSON を残す
  ↓
索引化         mdex scan
  ↓
開始           mdex start
  ↓
実装           必要に応じて context / first / related / query
  ↓
終了           mdex finish --dry-run
  ↓
反映           mdex finish --summary-file ... --scan（必要時）
```

---

## 設計ドキュメント

- 詳細設計（Phase A）: `docs/phase_a_agent_flow.md`
- 全体設計: `docs/design.md`
- 記録規約: `docs/convention.md`
- エージェント向け運用: `AGENT.md`
