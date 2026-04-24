---
type: reference
project: mdex
status: active
updated: 2026-04-24
---

# context スコアリング設計メモ

`mdex context` / `mdex start` の順位づけは `context_scoring` 設定で上書きできます。

## 設定ソースの優先順位

1. runtime config (`.mdex/config.json`) の `context_scoring`
2. scan config (`control/scan_config.json` など) の `context_scoring`
3. コード内デフォルト

実際に採用された設定ソースは各 node の `score_breakdown.config_source`
（`runtime_config` / `scan_config` / `defaults`）で追跡できます。

## 主な寄与

- `keyword.title`: クエリ語が title に含まれる寄与
- `keyword.summary`: クエリ語が summary に含まれる寄与
- `keyword.tags`: クエリ語が tags に一致する寄与
- `type_status.type_bonus`: 種別ボーナス（design/decision を優先）
- `type_status.status_bonus`: 状態ボーナス（active/draft を優先、archived を減点）
- `recency`: 更新日時の新しさによる寄与（`recency_weight` で重み付け）
- `graph_boost`: seed 候補から 1-hop の resolved edge による寄与

## スコア集計と予算制御

- `total = keyword.total + type_status.total + recency + graph_boost`
- `token_cost.estimated_tokens` は budget ゲートに使用
- `token_cost.soft_cap = budget * soft_budget_multiplier`

`token_cost` は順位スコアには直接加算しません。最終選抜は score 順 + budget 条件です。

## config 例

```json
{
  "context_scoring": {
    "keyword": {
      "title": 3.0,
      "summary": 1.5,
      "tags": 2.2
    },
    "type_bonus": {
      "design": 1.2,
      "decision": 1.2
    },
    "status_bonus": {
      "active": 0.8,
      "done": -0.5
    },
    "graph_boost_by_edge_type": {
      "depends_on": 0.6,
      "links_to": 0.35,
      "relates_to": 0.2
    },
    "graph_default_boost": 0.15,
    "recency_weight": 1.0,
    "primary_keyword_search_multiplier": 5,
    "secondary_keyword_search_multiplier": 2,
    "primary_keyword_search_floor": 20,
    "secondary_keyword_search_floor": 10,
    "soft_budget_multiplier": 1.2
  }
}
```
