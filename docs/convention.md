---
type: spec
project: mdex
status: active
updated: 2026-04-22
---

# mdex 記録規約

AI エージェントが残す Markdown / JSON を `mdex` で安定して索引化するための入力契約。

## Scope

- この文書は **input note contract** の正本
- `start` / `context` / `impact` / `finish` の精度はこの規約への準拠度に依存
- workflow 手順は `README.md`、分岐判断は `AGENT.md` が正本

## Responsibility Boundary

### 書いてよい内容

- frontmatter の必須/推奨キー
- `depends_on` / `relates_to` の使い分け
- summary の書式

### 書いてはいけない内容

- workflow の標準手順 (`scan -> start -> ... -> finish`)  
  `README.md` を参照
- `start` と `context --actionable` の判断ロジック  
  `AGENT.md` を参照

## 基本ルール

1. frontmatter に `type` / `status` / `updated` を置く
2. 前提は `depends_on`、関連は `relates_to` に分離
3. 本文先頭に 1〜2 文 summary を置く
4. 可能な限り `mdex new` と `mdex stamp` を使う

## frontmatter 最小構成

```yaml
---
type: decision
project: <project>
status: active
updated: 2026-04-19
depends_on:
  - docs/design.md
relates_to:
  - tasks/pending/T20260412190917.md
---
```

## `depends_on` と `relates_to`

- `depends_on`: この文書が成立するための前提（読まないと誤読しうる）
- `relates_to`: 関連はあるが前提ではない近傍文書

## summary の書き方

- 何を決めた/定義した文書か
- どの作業で参照すべきか
- 主な制約・前提は何か

## type

| type | 用途 |
|---|---|
| `decision` | 方針決定・理由 |
| `design` | 設計仕様 |
| `spec` | インターフェース/形式仕様 |
| `task` | 実装タスク |
| `log` | セッション記録 |
| `reference` | 参照情報 |

## status

| status | 意味 |
|---|---|
| `active` | 有効 |
| `draft` | 作成中 |
| `pending` | 開始前タスク |
| `done` | 完了 |
| `archived` | 参照のみ |

## コマンド対応

- 作成: `mdex new task|decision`
- 日付更新: `mdex stamp <node-id or path>`
- 終了整理: `mdex finish --dry-run`
- 反映: `mdex finish --summary-file ... --scan`
