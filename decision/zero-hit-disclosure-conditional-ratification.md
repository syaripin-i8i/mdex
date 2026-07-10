---
type: decision
project: mdex
status: active
updated: 2026-07-10
tags:
  - zero-hits
  - contract
  - cdex
  - protocol
relates_to:
  - ../tasks/T20260710080029.md
  - ../docs/agent_integration.md
  - ../docs/schema_versioning.md
---

# Zero-hit disclosure: conditional ratification

2026-07-10、Sol による批准回答。結論は**条件付き批准 — 本記録に列挙する補正の完了時に発効**。mdex 側の補正は同日の `70f9e24`、cdex 側の shape 整合は `676a0eb` で実装済み。提案側の経緯は `tasks/T20260710080029.md`、発端は cdex docs/decisions/0003 (field intake: エージェントが 0 件を「存在しない」と誤読)。

## 決定内容

1. **共通語彙 (条件付き批准 → 発効)**: `zero_hits` の field 名・型・意味 (`lanes_searched` / `lanes_inactive` / `caveat` / `remediation`) を cdex/mdex 共通プロトコル面として確定。`lanes_inactive` は**常在 map** とし、非活性 lane が無ければ `{}`。rename は片側単独では行わず、alias/deprecation を伴う共通 protocol revision による。cdex 側も `676a0eb` で hybrid=`{}`、semantic=`{"lexical": "not_requested"}` に整合し、cdex main へ統合済み。
2. **`documented_non_goal` (批准)**: activatable な停止理由 (`embeddings_missing` / `not_requested` 等) と by-design-off を区別する stable reason token として承認。理由 token の集合は拡張可能であり、**消費者は未知 token を拒否しない**契約とする。
3. **`find` のチャネル (条件付き批准)**: stdout の既存契約 (json `[]` / table 空出力) を維持し、検索済み 0 件のみ stderr に compact JSON 1 行 + exit 0。schema object 化は行わない。**成否判定は exit code が正本、機械処理で stdout/stderr を merge しない**ことを契約化。
4. **remediation (現文言は非批准 → 補正済み)**: cdex は private かつ mdex の依存ではないため無条件案内にしない。標準 remediation は `rg` + frontmatter tags で自己完結し、`cdex search "<terms>"` は「利用可能な場合」の任意ヒント。remediation は説明文であり、実行可能 command の正本は構造化 argv 面 (`recommended_next_actions_v2` / `suggested_rg`)。
5. **contract version**: `0.5.0` で出荷し、`0.4.x` patch には含めない (schema versioning policy: optional field 追加 = MINOR。成功時 stderr 追加も公開契約上 minor)。

## 発効条件 (blocker と補正)

- **multi-index の誤主張 (blocker)**: 旧実装の `any(...)` は「一方が真の zero、他方が hit 後 budget drop」でもトップレベルに `zero_hits` を主張した。「要求した全 index が実際に検索され、全 context が zero」のときのみ付与に修正し、混在・index 欠落の回帰テストを追加。
- `--format table` の 0 件は stdout 空出力 (json は `[]`) であることの明文化と回帰テスト。
- `AGENT.md` Contract Reminders / `docs/design.md` 設計原則 2 の旧記述 (「成功 = stdout JSON」) に例外を明記。
- remediation 文言の補正 (上記 4)。
- version 0.5.0 への bump (tag ガードにより 0.4.x への混入を物理的に防止)。

## 理由

0 件の誤読は索引の非探索範囲が payload 上で不可視なことに起因する。unknown を first-class にする開示は仕様 (本文非探索 = documented non-goal) を変えずに誤読だけを塞ぐ。チャネル判断は直前に確定した出力契約 (`4b9f704`) を覆さないことを優先した。

## 却下した代替案

- `find` 出力の schema-backed object 化 (即時): 破壊的変更であり、生配列契約の決定を即座に覆すため不採用。将来の major で再検討可。
- remediation の設定による自由文差し替え: 今回は不要と判断。
- 本文全文の索引化: Non-goals のまま (再発防止は消費者側の tags 規律 = 工程で行う)。

## 影響範囲

- payload: `context` (single/multi) の optional `zero_hits`、`find` の成功時 stderr 1 行。
- 契約文書: README Output Contract、`docs/agent_integration.md`、`AGENT.md`、`docs/design.md`、`schemas/context.schema.json`、`docs/schema_versioning.md`。
- 生態系: cdex `_zero_hits_field` の `lanes_inactive` 常在化は `676a0eb` で実装し、cdex main へ統合済み。共通語彙の根拠: cdex `51c9ffa` + `676a0eb` / cdex docs/decisions/0003・0004、mdex `fdfc6a6` + `70f9e24`。
