---
type: design
project: mdex
status: active
updated: 2026-04-07
---

# mdex 設計書

## 一言で言うと

`mdex` は、Markdown ファイル群を対象にした軽量知識探索基盤。

Yura で実証済みの `first_files_to_read` / `related_checkpoints` の考え方を、
特定用途のチェックポイント群ではなく、ローカル Markdown 全体へ汎用化する。

コアは「全文を先に読ませない」こと。
まず軽量インデックスを参照し、必要なノードだけを段階的に開くことで、
AI セッションのコンテキスト消費を抑えつつ、関連知識への到達性を高める。

初期段階では、明示リンク・frontmatter・種別・更新情報を中心とした探索器として実装し、
要約生成や類似推定は後段の拡張とする。

---

## なぜ作るか

### 問題

- RAG や全文検索は「似た文書を広く返す」方向に引きやすく、コンテキスト消費が大きい
- 文書間の依存や判断の流れを扱いにくい
- 「何を先に読むか」という探索順序を持てない
- 人間がその都度、前提や背景を補足する必要がある

### 欲しいもの

単なる検索ではなく、**AI が必要な断片だけを順に辿れる構造**。

### 既存ツールとの棲み分け

| ツール | 対象 | 得意なこと |
|--------|------|-----------|
| codegraph MCP | コード AST | 関数依存・影響範囲・呼び出し経路 |
| codex-shared-memory | チェックポイント・タスク | セッション記憶・作業継続 |
| **mdex** | Markdown ファイル群 | 設計書・決定記録・タスク群の探索 |

---

## モジュール構成

```
runtime/
  scanner.py    ← ディレクトリ走査・.md ファイル列挙
  parser.py     ← frontmatter / [[wikilink]] / heading / summary 抽出
  indexer.py    ← ノード・エッジ生成 → JSON / SQLite 書き出し
  resolver.py   ← 関連ノード探索（隣接・逆参照・孤立検出）
  reader.py     ← 必要ノード本文の取得
  cli.py        ← 手元確認用入口（argparse）
```

### 設計原則

1. 各モジュールは独立した純粋関数ベース。外部ステートを持たない。
2. CLI はモジュールを組み合わせるだけ。ロジックを持たない。
3. MCP 化するときは CLI の代わりに MCP ハンドラを被せるだけで済む設計を保つ。
4. Phase 1-2 は AI 呼び出しなし。索引・探索のみ。
5. SQLite は Phase 1 から使う（JSON と両方出力。JSON は確認用、SQLite が正）。

---

## フェーズ計画

### Phase 1（索引化）

目標: Markdown を走査してインデックスを作れる状態にする。

- `scanner`: .md ファイル列挙
- `parser`: frontmatter / `[[link]]` / heading / summary 抽出
- `indexer`: ノード・エッジ → JSON + SQLite
- `cli`: `scan` / `list` / `open` コマンド

完了条件:
- `python runtime/cli.py scan --root <dir>` が動く
- `mdex_index.db` に nodes / edges テーブルが生成される
- `python runtime/cli.py list` がノード一覧を出力する
- `python runtime/cli.py open <id>` が本文を出力する

### Phase 2（探索）

目標: 「次に何を読むか」を構造から導ける状態にする。

- `resolver`: 入口候補生成（`first_files_to_read` 相当）
- `resolver`: 隣接候補生成（`related_checkpoints` 相当）
- 逆参照・孤立ノード検出
- `cli`: `find` / `related` / `first` コマンド追加

将来のコマンドイメージ:
```
python runtime/cli.py find     <query>
python runtime/cli.py related  <node-id>
python runtime/cli.py first    "認証まわり今どうなってる？"
```

### Phase 3（AI 補助）—— 境界線

ここから先は索引器ではなく AI 補助探索基盤になる。
Phase 2 が安定し、実際の探索ユースケースが見えてから設計する。

- 要約自動生成
- 類似ノード推定
- 問いベース探索順序最適化
- セッション文脈に応じた推薦

### Phase 4（表示）

- 軽量 Web UI
- ノード/エッジ可視化
- AI セッションへの受け渡し支援

---

## スキーマ

### ノード

```json
{
  "id": "relative/path/to/file.md",
  "title": "...",
  "type": "decision|task|design|log|spec|reference|unknown",
  "project": "...",
  "status": "active|done|draft|archived|unknown",
  "summary": "最初の非見出し段落（最大3文・200文字以内）",
  "tags": [],
  "updated": "ISO date（frontmatter > mtime の優先順）",
  "links_to": ["other/file.md"],
  "depends_on": ["other/file.md"],
  "relates_to": ["other/file.md"]
}
```

### エッジ

```json
{
  "from": "file.md",
  "to": "file.md",
  "type": "links_to|depends_on|relates_to|decides|implements|blocks|mentions"
}
```

### エッジの生成元

| フロント | エッジ型 |
|---------|---------|
| `[[wikilink]]` / `[text](file.md)` | `links_to` |
| frontmatter `depends_on` リスト | `depends_on` |
| frontmatter `relates_to` リスト | `relates_to` |

### type 判定ロジック

1. frontmatter の `type` フィールドがあればそれを使う
2. なければディレクトリ名で `control/scan_config.json` の `node_type_map` と照合
3. どちらもマッチしなければ `unknown`

---

## Markdown ノードの推奨書き方

AI 生成前提なので、文章美より探索性を優先する。

```markdown
---
type: decision
project: yura
status: active
relates_to:
  - "[[parser-layer]]"
  - "[[semantic-parse-schema]]"
depends_on:
  - "[[critique-detection]]"
updated: 2026-04-07
---

# タイトル

## 要点
- ...

## 判断
- ...

## 次に見る
- [[関連ノード]]
```

---

## MCP 化について

**今は前提にしない。**

Phase 1-2 は CLI / library として実装する。
ただし内部 API（各モジュールの関数境界）は最初から分けておく。
将来 MCP にするときは CLI の代わりに MCP ハンドラを被せるだけで済む。

MCP を先に意識すると、ツール境界・API 粒度・セッション管理の設計が増えて
本質の探索器づくりが遅くなる。

---

## 既存タスク（Phase 1）

| # | ID | 内容 | 状態 |
|---|---|---|---|
| 1 | T20260406190559 | `parser.py` — frontmatter + `[[wikilink]]` 抽出 | pending |
| 2 | T20260406190612 | `indexer.py` — ノード/エッジ JSON + SQLite | pending |
| 3 | T20260406190626 | `cli.py` — scan / list / open | pending |

実行順は 1 → 2 → 3 の順で。各タスクは前のタスクの完了後に着手する。

---

## リポジトリ

- GitHub: https://github.com/syaripin-i8i/mdex （private）
- ローカル: `C:\Codex\mdex\`
- ブランチ: `master`

## 次に見る

- [[AGENT]]
- [[proposal]]
