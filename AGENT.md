# mdex — Agent Operation Guide

## Goal

`mdex` は「AI が最初に何を読むか / 最後に何を更新すべきか」を決めるための CLI です。  
迷ったら次の 3 コマンドだけ覚えてください。

```bash
mdex scan   # 索引更新
mdex start  # 作業開始
mdex finish # 作業終了
```

## Default Flow

1. 開始前に `scan`
2. 最初に `start`
3. 必要に応じて `context` / `first` / `related` / `query`
4. 最後に `finish --dry-run`
5. summary を反映するときだけ `finish --summary-file ... --scan`

## DB Resolution

`--db` 省略時は以下の優先順で自動解決します。

1. `--db`
2. `MDEX_DB`
3. `.mdex/config.json` の `db`
4. `.mdex/mdex_index.db`
5. `mdex_index.db`

## Command Roles

- `start`: 入口。読む順序と次アクションを返す
- `context --actionable`: `start` 相当を直接確認
- `impact`: changed files 起点の分類
- `finish`: 出口。enrich 候補と後処理を返す
- `enrich`: summary を直接更新（限定用途）
- `new`: task / decision のテンプレート生成
- `stamp`: `updated` 更新

## Output Contract

- すべて JSON（成功: stdout / エラー: stderr）
- エラーにも理由を入れる
- `finish --dry-run` は DB を更新しない

## Phase Status (2026-04-13)

- Phase 1: scan / list / query
- Phase 2: find / first / related / orphans / stale
- Phase A: dbresolve / start / finish / impact / new / stamp

## Verification

```bash
python -m pytest -q
```
