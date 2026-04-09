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

## エージェントの作業サイクル

mdex は「書く → 索引化 → 読む → 改善」のサイクルで使います。

```
書く        mdex new（将来）/ 規約に沿って Markdown を作成
  ↓
索引化      mdex scan
  ↓
読む        mdex context → 最小必要ファイルを特定
            mdex open / related / first → 詳細探索
  ↓
改善        mdex enrich → summary を AI 消費向けに更新
            mdex scan   → 索引に反映
```

書き方の規約は `docs/convention.md` を参照。

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

- `context`: 問いに対する優先読解ノード集合を返す入口
- `enrich`: 読後に summary を改善して索引品質を上げる更新器

### Phase 4（記録支援）: 未着手

- `new`: 規約に沿った Markdown テンプレートを生成
- `stamp`: frontmatter の `updated` を現在日時に更新

AI 実運用ではコンテクストを3層で扱う。  
入力コンテクスト（ユーザーの依頼文）、推論対象コンテクスト（今回読むべき文書）、運用コンテクスト（過去判断や運用制約）を分離し、`mdex` は主に推論対象コンテクストの圧縮と選別を担う。

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

## context / enrich 仕様

- `context`:
  - 入力クエリからキーワード抽出（非AI）
  - `title` / `summary` / `tags` の一致とグラフ近傍で候補を選別
  - `--budget` をソフト上限として返却集合を制御
  - デフォルトは軽量メタのみ、`--include-content` で本文を同梱
- `enrich`:
  - ノード本文を読み、外部モデルで 2〜3文 summary を生成
  - DB の `nodes.summary` を更新
  - APIキー未設定時はスキップ（失敗扱いにしない）
  - `--path` で絶対パスから node-id を逆引き可能

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
mdex context "<query>" --db <sqlite> [--budget <n>] [--limit <n>] [--include-content]
mdex enrich <node-id> --db <sqlite> [--force]
mdex enrich --path <absolute-path> --db <sqlite> [--force]
```

成功時は stdout JSON、エラー時は stderr JSON を返す。
