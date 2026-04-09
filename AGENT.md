# mdex — Codex Agent Instructions

## What This Repo Is

`mdex` は Markdown ファイル群の索引化と最小探索を行うツールです。  
狙いは全文検索の代替ではなく、文書間の関係を使って「次に何を読むか」を絞ることです。

**このツールは AI エージェント（Claude Code・Codex など）のために作られています。**  
**人間向けの使いやすさは考慮しない。全ての設計判断はエージェントの使いやすさを優先する。**

## Core Principles

1. CLI は薄く保ち、ロジックは runtime モジュールへ閉じる。
2. **全コマンドの出力は JSON デフォルト。** タブ区切りなど人間向け形式は `--format table` でのみ提供する。
3. **エラーも JSON で返す。** `{"error": "...", "detail": "..."}` の形式を守る。
4. SQLite を正本とし、JSON はデバッグ出力に限定する。
5. 未解決リンクは resolved と混在させず、`resolved=false` として扱う。
6. **`enrich` のサマリは AI 消費向けに書く。** 「別のエージェントがこのファイルを読むべきか判断するための情報」を含める。何が書かれているか・どんな制約や判断を含むか・いつ参照すべきか。

## Runtime Responsibilities

```
runtime/
  scanner.py    ← ディレクトリ走査・.md ファイル列挙
  parser.py     ← frontmatter / metadata / link / summary 抽出
  builder.py    ← ノード・エッジ生成（参照解決・resolved 判定）
  indexer.py    ← JSON / SQLite 書き出し
  store.py      ← SQLite 読み出し API
  resolver.py   ← 探索ロジック（related 候補抽出）
  reader.py     ← ノード本文取得
  cli.py        ← argparse エントリ
```

## Phase Status

### Phase 1（索引化）: 完了

- `scan` で JSON + SQLite を生成
- `edges` は `resolved` を保持
- `list` / `query` は SQLite ベース

### Phase 2（探索）: 完了

- `find` / `related` / `first` / `orphans` を実装済み
- `first` は前提列（depends_on 系）を返し、`related` は連想的な近傍候補を返す

### Phase 3（AI 補助）

- `context` / `enrich` を実装済み
- `context` は作業前に必要ノードを絞る入口、`enrich` は読後に summary を更新して探索精度を上げる

## Schemas

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

### Edge

```json
{
  "from": "a.md",
  "to": "b.md",
  "type": "links_to|depends_on|relates_to",
  "resolved": true
}
```

未解決ターゲットの場合:

```json
{
  "from": "a.md",
  "to": "missing.md",
  "type": "links_to",
  "resolved": false
}
```

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

## Verification

1. `python -m py_compile runtime/*.py`
2. `mdex scan --root docs --output mdex_index.json --db mdex_index.db`
3. `mdex list --db mdex_index.db --format json`
4. `mdex find design --db mdex_index.db`
5. `mdex context "design decision" --db mdex_index.db --limit 5`
6. `mdex query --db mdex_index.db --node design.md`
7. `mdex first design.md --db mdex_index.db`
8. `mdex related design.md --db mdex_index.db`
9. ファイルを読んだ後: `mdex enrich <node-id> --db mdex_index.db`
