# mdex

`mdex` は、Markdown ファイル群から軽量な知識グラフ（nodes/edges）を作るためのツールです。  
全文検索よりも「どの文書を先に読むか」を支援する用途を重視しています。

## できること

- Markdown 走査（`.md` 列挙、除外パターン対応）
- frontmatter / 見出し / 要約 / タグ抽出
- `[[wikilink]]` / `[text](file.md)` / task内ID参照（`TYYYYMMDDHHMMSS`）のエッジ化
- JSON + SQLite インデックス出力
- CLI で `scan`, `list`, `open`, `query` を実行

## 必要環境

- Python 3.10+
- `PyYAML`

```bash
python -m pip install pyyaml
```

## クイックスタート

リポジトリルート（`C:\Codex\mdex`）で実行します。

```bash
python runtime/cli.py scan --root docs/
python runtime/cli.py list
python runtime/cli.py open docs/proposal.md
```

`scan` 実行後に以下が生成されます。

- `mdex_index.json`
- `mdex_index.db`

## C:\Codex 全量スキャン例

```bash
python runtime/cli.py scan --root <repo-root> --output <repo-root>/mdex_codex_index.json --db <repo-root>/mdex_codex_index.db
python runtime/cli.py list --index <repo-root>/mdex_codex_index.json --project mdex
python runtime/cli.py query --index <repo-root>/mdex_codex_index.json --node infra/tasks/done/T20260330022925.md --depth 1
python runtime/cli.py open infra/tasks/done/T20260409035436.md --index <repo-root>/mdex_codex_index.json
```

## CLI 概要

- `scan --root <dir> [--output <json>] [--db <sqlite>] [--config <json>]`
- `list [--index <json>] [--type <type>] [--project <name>] [--status <status>]`
- `open <node-id> [--root <dir>] [--index <json>]`
- `query --node <id> [--index <json>] [--depth <n>]` （後方互換用）

## 実装モジュール

- `runtime/scanner.py`: `.md` 列挙
- `runtime/parser.py`: frontmatter/metadata/link抽出
- `runtime/builder.py`: ノード・エッジ生成とリンク解決
- `runtime/indexer.py`: JSON/SQLite 書き出し
- `runtime/reader.py`: node-id から本文取得
- `runtime/cli.py`: コマンド入口

## 設計ドキュメント

- 詳細設計: `docs/design.md`
- エージェント運用: `AGENT.md`
