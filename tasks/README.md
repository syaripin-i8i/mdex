---
type: reference
project: mdex
status: active
updated: 2026-04-28
---

# Repo-Local Tasks

`mdex` のプロジェクトタスクは 2026-04-24 から repo 内 `tasks/` で管理する。

## Current Policy

タスクは `tasks/` 直下へ積み上げ、フォルダ移動ではなく frontmatter の `status` で管理する。

推奨 status:

- `pending`: 未開始
- `active`: 進行中
- `done`: 完了
- `blocked`: 保留・要判断

## Migration Note

`tasks/pending/` と `tasks/done/` は歴史的レイアウトとして残っている。既存ファイルを整理目的だけで移動しない。  
`tasks/done/` の歴史的タスクは `C:\Codex\infra\tasks\` から移設した。本文中の絶対パスや完了手順は、当時の運用パスを指している場合がある。

## Authoring Note

- 新規タスクは frontmatter を付け、`type: task`, `project: mdex`, `status: pending`, `updated` を入れる
- 新規タスクファイルは `tasks/TYYYYMMDDHHMMSS.md` のように `tasks/` 直下へ作る
- 完了時は同じファイルの `status` を `done` に更新し、Result / Verification / PR などを追記する
- 公開 PR にタスク記録を必ず載せる必要はない。PR 本文はレビューに必要な変更要約と検証に絞る
- 一時メモは `*.tmp.md` または `.tmp/` 配下に置くと Git 追跡対象外になる
