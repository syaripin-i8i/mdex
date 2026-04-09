---
type: design
project: mdex
status: active
updated: 2026-04-09
---

# mdex 設計書

## 一言で言うと

`mdex` は Markdown 群をノード/エッジとして索引化し、  
「次に何を読むか」を判断するための最小探索器。

全文検索を置き換えるのではなく、先読み順序を作ることが主目的。

## 設計原則

1. CLI は薄く、コアロジックは runtime モジュールへ閉じる。
2. SQLite を正本とし、JSON はデバッグ出力として扱う。
3. 未解決参照は `resolved=false` で明示し、解決済みと混ぜない。
4. Phase 1-2 は AI 依存なし。

## モジュール責務

```
runtime/
  scanner.py    ← .md 列挙
  parser.py     ← frontmatter / metadata / link / summary 抽出
  builder.py    ← ノード・エッジ生成（リンク解決、resolved 判定）
  indexer.py    ← JSON / SQLite 書き出し
  store.py      ← SQLite 読み出し API
  resolver.py   ← related 候補生成
  reader.py     ← node-id から本文取得
  cli.py        ← コマンド入口
```

## フェーズ

### Phase 1（索引化）: 完了

- `scan` で JSON + SQLite 出力
- edge に `resolved` を保持
- `list` / `query` は SQLite 参照

### Phase 2（探索）: 完了

- `find`: title/summary/tags 検索の入口
- `orphans`: resolved edge 観点の孤立ノード検出
- `related`: 連想的な近傍候補（関連探索）
- `first`: depends_on を基にした前提読み順（prerequisite reader）

### Phase 3（AI 補助）

- 未着手

## スキーマ

### Node

```json
{
  "id": "relative/path/to/file.md",
  "title": "...",
  "type": "decision|task|design|log|spec|reference|unknown",
  "project": "...",
  "status": "active|done|draft|archived|unknown",
  "summary": "...",
  "tags": [],
  "updated": "ISO date",
  "links_to": ["resolved-target.md"],
  "depends_on": ["resolved-target.md"],
  "relates_to": ["resolved-target.md"]
}
```

`links_to` / `depends_on` / `relates_to` は解決済みターゲットのみ保持する。

### Edge

```json
{
  "from": "a.md",
  "to": "b.md",
  "type": "links_to|depends_on|relates_to",
  "resolved": true
}
```

未解決リンク:

```json
{
  "from": "a.md",
  "to": "missing.md",
  "type": "links_to",
  "resolved": false
}
```

## query 出力仕様（方向保持）

`query` は向きと型を保持して返す。

```json
{
  "node": { "...": "..." },
  "outgoing": {
    "links_to": [{"id": "b.md", "resolved": true}],
    "depends_on": [],
    "relates_to": []
  },
  "incoming": {
    "links_to": [],
    "depends_on": [],
    "relates_to": []
  }
}
```

## related 仕様（最小探索器）

`resolver.related_nodes(node_id, db_path, limit)` は resolved edge のみ対象にスコアリングする。

- `depends_on`（outgoing）を最優先
- `links_to`（outgoing）を中優先
- `relates_to` を補助
- incoming も小さく加点
- タグ一致・type 一致も補助加点

`first` と `related` は責務を分離する。`first` は「理解前に読む前提列」、`related` は「理解後に広げる連想近傍」を返す。

## CLI

```bash
mdex scan --root <dir> [--output <json>] [--db <sqlite>] [--config <json>]
mdex list --db <sqlite> [--type <type>] [--project <project>] [--status <status>] [--format <table|json>]
mdex open <node-id> --db <sqlite>
mdex query --db <sqlite> --node <node-id>
mdex find <query> --db <sqlite> [--limit <n>]
mdex orphans --db <sqlite>
mdex related <node-id> --db <sqlite> [--limit <n>]
mdex first <node-id> --db <sqlite> [--limit <n>]
```

`--index` は JSON 互換経路としてのみ保持する。
