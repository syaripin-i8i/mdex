---
type: spec
project: mdex
status: active
updated: 2026-04-22
---

# mdex Update Policy (0.x)

## Scope

この文書は `mdex` リポジトリの 0.x 期における更新方針の正本です。  
`docs/schema_versioning.md` は schema 版運用の詳細、ここは repo 全体の更新姿勢を定義します。

## Stance

中はバンバン変える。外の契約はむやみに壊さない。

## 気軽に変えてよい

- `README.md` の説明改善、導線整理、誤解しやすい表現の修正
- `docs/` の構成整理、重複削減、参照リンクの最適化
- 実装内部のリファクタ（外部契約を壊さない範囲）
- テスト追加・テスト品質改善
- エラーメッセージ改善（既存 JSON key を維持する範囲）
- AI エージェント向けの実行導線改善

## 慎重に変える

- stdout/stderr の JSON 契約
- JSON の field 名と意味
- `start` / `impact` / `finish` の primary keys
- `schemas/*.schema.json` の互換性
- 標準コマンドフロー（`scan -> start -> ... -> finish`）の基本形

## 契約変更時の手順

1. `CHANGELOG.md` に契約変更を明記する
2. schema 変更がある場合は `docs/schema_versioning.md` に従う
3. 既存利用者への影響が大きい場合は deprecation 経由で段階移行する

## 0.x と 1.0 以降

- 0.x: 検証と改善を優先し、内部実装は素早く更新してよい
- 1.0+: 外部契約をより厳密に安定化し、破壊的変更は強い理由と移行導線を必須とする
