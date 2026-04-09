# mdex

`mdex` は、Markdown ファイル群を対象にした軽量インデクサ兼探索器です。  
全文検索ではなく「次に何を読むべきか」を支援する用途を重視しています。

## できること

- Markdown 走査（`.md` 列挙、除外パターン対応）
- frontmatter / 見出し / 要約 / タグ抽出
- `[[wikilink]]` / `[text](file.md)` / task ID 参照のエッジ化
- JSON（デバッグ） + SQLite（正本）インデックス出力
- `query` で方向付きの入出力関係を表示（`incoming/outgoing`）
- `related` で次に読む候補をルールベース推薦

## セットアップ

- Python 3.10+
- `PyYAML`

```bash
python -m pip install -e .
```

開発用テスト込み:

```bash
python -m pip install -e ".[dev]"
```

## クイックスタート

```bash
mdex scan --root docs/ --output mdex_index.json --db mdex_index.db
mdex list --db mdex_index.db
mdex query --db mdex_index.db --node design.md
mdex related design.md --db mdex_index.db
mdex open design.md --db mdex_index.db
```

## 主なコマンド

- `scan --root <dir> [--output <json>] [--db <sqlite>] [--config <json>]`
- `list --db <sqlite> [--type <type>] [--project <name>] [--status <status>]`
- `open <node-id> --db <sqlite>`
- `query --db <sqlite> --node <node-id>`
- `related <node-id> --db <sqlite> [--limit <n>]`

`list` / `query` / `related` は SQLite を主経路に使います。  
`--index` は JSON フォールバック（後方互換）です。

## 実装モジュール責務

- `runtime/scanner.py`: `.md` 列挙
- `runtime/parser.py`: frontmatter/metadata/link/summary 抽出
- `runtime/builder.py`: ノード・エッジ生成（参照解決、resolved 判定）
- `runtime/indexer.py`: JSON/SQLite 書き出し
- `runtime/store.py`: SQLite 読み出し API
- `runtime/resolver.py`: 探索ロジック（`related`）
- `runtime/reader.py`: node-id から本文取得
- `runtime/cli.py`: コマンド入口

## 設計ドキュメント

- 詳細設計: `docs/design.md`
- エージェント運用: `AGENT.md`
