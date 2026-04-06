# mdex — Codex Agent Instructions

## What This Repo Is

`mdex` は Markdown ファイル群を対象にした軽量知識探索基盤。
Yura で実証済みの `first_files_to_read` / `related_checkpoints` の考え方を、
特定用途のチェックポイント群ではなく、ローカル Markdown 全体へ汎用化することを目的とする。

コアは「全文を先に読ませない」こと。
まず軽量インデックスを参照し、必要なノードだけを段階的に開くことで、
AI セッションのコンテキスト消費を抑えつつ、関連知識への到達性を高める。

```
mdex/
  runtime/
    scanner.py    ← ディレクトリ走査・.md ファイル列挙
    parser.py     ← frontmatter / wikilink / heading / タスク行 抽出
    indexer.py    ← ノード・エッジ生成 → JSON / SQLite 書き出し
    resolver.py   ← 関連ノード探索（隣接・逆参照・孤立検出）
    reader.py     ← 必要ノード本文の取得
    cli.py        ← 手元確認用入口（argparse）
  control/
    scan_config.json  ← 走査設定
  docs/               ← 設計ノート・決定記録
  AGENT.md
```

## 設計原則

1. 各モジュールは独立した純粋関数ベース。外部ステートを持たない。
2. CLI は各モジュールを組み合わせるだけ。ロジックを持たない。
3. MCP 化するときは CLI の代わりに MCP ハンドラを被せるだけで済む設計を保つ。
4. Phase 1-2 は AI 呼び出しなし。索引・探索のみ。
5. SQLite は Phase 1 から使う（JSON と両方出力。JSON は確認用、SQLite が正）。

## フェーズ境界

### Phase 1（現在）: 走査 + 索引化

- `scanner`: .md ファイル列挙
- `parser`: frontmatter / `[[link]]` / heading / summary 抽出
- `indexer`: ノード・エッジ JSON + SQLite 書き出し
- `cli`: `scan` / `list` / `open` コマンド

### Phase 2: 探索

- `resolver`: `first_files_to_read` 相当（入口候補生成）
- `resolver`: `related_checkpoints` 相当（隣接候補生成）
- 逆参照・孤立ノード検出
- `cli`: `find` / `related` コマンド追加

### Phase 3: AI 補助（境界線）

ここから先は索引器ではなく AI 補助探索基盤になる。
要約自動生成・類似推定・問いベース探索順序最適化。
Phase 2 が安定してから設計する。

---

## Node Schema

```json
{
  "id": "relative/path/to/file.md",
  "title": "...",
  "type": "decision|task|design|log|spec|reference|unknown",
  "project": "...",
  "status": "active|done|draft|archived|unknown",
  "summary": "first non-heading paragraph, max 3 sentences / 200 chars",
  "tags": [],
  "updated": "ISO date from frontmatter or mtime",
  "links_to": ["other/file.md"],
  "depends_on": ["other/file.md"],
  "relates_to": ["other/file.md"]
}
```

## Edge Schema

```json
{
  "from": "file.md",
  "to": "file.md",
  "type": "links_to|depends_on|relates_to|decides|implements|blocks|mentions"
}
```

## CLI Commands (Phase 1)

```
python runtime/cli.py scan   --root <dir> [--output <file>] [--config <json>]
python runtime/cli.py list   [--type <type>] [--project <p>] [--status <s>]
python runtime/cli.py open   <node-id>
```

Future (Phase 2):
```
python runtime/cli.py find     <query>
python runtime/cli.py related  <node-id>
python runtime/cli.py first    "<question>"
```

## Operational Facts

- Python: `<python>`
- Compile check: `python -m py_compile runtime/cli.py`
- SQLite output: `mdex_index.db` (main), JSON output: `mdex_index.json` (debug)

## Phase 1 Completion Criteria

- `python runtime/cli.py scan --root docs/` が動く
- `mdex_index.db` に nodes / edges テーブルが生成される
- `python runtime/cli.py list` がノード一覧を出力する
- `python runtime/cli.py open docs/proposal.md` が本文を出力する
- `git push origin master` 済み

## Task Execution Order

Phase 1 タスクは以下の順で実行すること:
1. T20260406190559: parser（frontmatter + wikilink 抽出）
2. T20260406190612: indexer（ノード/エッジ JSON + SQLite 生成）
3. T20260406190626: CLI（scan + list + open）

各タスクは前のタスクの完了後に着手すること。

## Verification Order

1. `git status --short`
2. `python -m py_compile runtime/parser.py runtime/indexer.py runtime/cli.py`
3. `python runtime/cli.py scan --root docs/`
4. Inspect `mdex_index.json` or `mdex_index.db`
