---
type: reference
project: mdex
status: active
updated: 2026-04-24
---

# Repo-Local Tasks

`mdex` のプロジェクトタスクは 2026-04-24 から repo 内 `tasks/` で管理する。

## Layout

- `tasks/pending/`: 未着手または進行中のタスク
- `tasks/done/`: 完了済みタスク

## Migration Note

`tasks/done/` の歴史的タスクは `C:\Codex\infra\tasks\` から移設した。本文中の絶対パスや完了手順は、当時の運用パスを指している場合がある。

## Authoring Note

- 新規タスクは frontmatter を付け、`type: task`, `project: mdex`, `status: pending`, `updated` を入れる
- `mdex new task` の既定出力先は `tasks/pending` なので、repo-local 運用と整合する
- 一時メモは `*.tmp.md` または `.tmp/` 配下に置くと Git 追跡対象外になる
