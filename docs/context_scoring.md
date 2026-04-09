---
type: reference
project: mdex
status: active
updated: 2026-04-09
---

# context スコアリング設計メモ

- 目的:
  - `mdex context` の「なぜこのノードが上位か」を JSON で追跡できるようにする。
- 主な寄与:
  - `keyword.title`: クエリ語が title に含まれる寄与。
  - `keyword.summary`: クエリ語が summary に含まれる寄与。
  - `keyword.tags`: クエリ語が tags に一致する寄与。
  - `type_status.type_bonus`: 種別ボーナス（design/decision を優先）。
  - `type_status.status_bonus`: 状態ボーナス（active/draft を優先、archived を減点）。
  - `recency`: 更新日時の新しさによる寄与。
  - `graph_boost`: seed 候補から 1-hop の resolved edge による寄与。
- 集計:
  - `total = keyword.total + type_status.total + recency + graph_boost`
- 予算制御:
  - `token_cost.estimated_tokens` は選別後の budget ゲートに使用。
  - `token_cost.soft_cap` は `budget * 1.2`（ソフト上限）。
- 注意:
  - `token_cost` は順位スコアには直接加算しない。
  - 最終選抜は score 順 + budget 条件で行う。
