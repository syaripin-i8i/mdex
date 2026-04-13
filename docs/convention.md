---
type: spec
project: mdex
status: active
updated: 2026-04-13
---

# mdex 記録規約

AI エージェントが残す Markdown / JSON を `mdex` で安定して索引化するための規約。

## 基本ルール

1. `type` / `status` / `updated` を frontmatter に入れる
2. 前提は `depends_on`、関連は `relates_to` に分ける
3. 先頭段落で 1〜2 文の summary を書く
4. 可能な限り `mdex new` と `mdex stamp` を使う

## 推奨ワークフロー

```bash
# 新規作成
mdex new task "Phase A DB resolver"
mdex new decision "adopt start/finish workflow"

# 変更したら updated を更新
mdex stamp tasks/pending/T20260412190917.md

# 作業完了時
mdex finish --task "Phase A DB resolver" --dry-run
mdex finish --task "Phase A DB resolver" --summary-file ./summary.txt --scan
```

## frontmatter 最小構成

```yaml
---
type: decision
project: mdex
status: active
updated: 2026-04-13
tags: [phase-a, workflow]
depends_on:
  - docs/design.md
relates_to:
  - tasks/pending/T20260412190917.md
---
```

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
| `pending` | 未着手タスク |
| `done` | 完了 |
| `archived` | 参照のみ |

## ディレクトリ推奨

```text
project/
  decision/
  design/
  spec/
  tasks/
    pending/
    done/
  logs/
```

## summary の書き方

他エージェントが「読む価値」を判定できるように書く。

- 何を決めた/定義した文書か
- どの作業で参照すべきか
- どんな制約があるか

## テンプレート

### decision

```markdown
---
type: decision
project: <project>
status: active
updated: <today>
---

<この決定の要点を1〜2文で>

## 決定内容

## 理由

## 却下した代替案

## 影響範囲
```

### task

```markdown
---
type: task
project: <project>
status: pending
updated: <today>
relates_to:
  - <関連設計>
---

<このタスクの目的を1〜2文で>

## 実施内容

## 結果

## 残課題
```

## コマンド対応

- 作成: `mdex new task|decision`
- 日付更新: `mdex stamp <node-id or path>`
- 終了整理: `mdex finish --dry-run`
- 反映: `mdex finish --summary-file ... --scan`
