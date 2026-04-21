---
type: design
project: mdex
status: draft
updated: 2026-04-20
---

# mdex を「気づきをもたらすツール」に寄せる提案 (Discovery Lane)

## ステータス

未決定 (draft)。採用するか迷っている段階のメモ。0.1.x では入れず、将来の方向性メモとして保存する。

## 現状確認

mdex はすでに `start / context / first / related / impact / orphans / stale` を持ち、必要十分な読書に絞る導線はできている。README と設計書の主軸は workflow の圧縮と入口/出口の定型化にある。

つまり今の mdex は「読む量を減らす」のは得意だが、「読まなかったせいで失う気づき」を積極的に補う設計には振り切っていない。

## 方向性

**最小読書のツール** から **最小読書を維持しつつ、低コストな寄り道を強制的に発生させるツール** へ進化させる。

mdex は今後、「正解の入口を返すツール」だけでなく、「見落としそうな周辺を 1〜3 件だけ意図的に混ぜるツール」になるべき。

人間の気づきは、たいてい

- 本命ではないが近い文書
- 名前は違うが構造が似た文書
- 最近変わったが今回の入口から漏れた文書
- 孤立しているのに重要そうな文書

から生まれる。だから mdex に足すべきなのは、全文読みに戻ることではなく、**探索ノイズを少量だけ設計的に注入すること**。

## 提案サマリ

### README / design 向け短文 (日本語)

> `mdex` は AI のコンテクストを縮めるだけのツールではない。
> 本命の読書経路を小さく保ったまま、見落としやすい周辺文書・古い近傍・危険な孤立・変更隣接ノードを少量だけ提示し、気づきの機会を作る探索支援ツールを目指す。

### 英語版

> `mdex` is not only for shrinking agent context.
> It should also inject a small, explicit discovery lane: nearby-but-not-obvious documents, stale neighbors, orphaned notes worth checking, and change-adjacent records that would otherwise be missed.

## 具体案

### 1. `start` に discovery lane を追加

現状の `start` は `recommended_read_order` と `recommended_next_actions` が主軸。ここに別レーンで `discovery_candidates` を追加する。

例:

- 本命の読む順 3 件
- 寄り道候補 2 件
- なぜ寄り道候補なのかの reason

reason は最低でも次のどれか:

- `shared_dependencies`
- `shared_links`
- `stale_but_related`
- `orphan_nearby`
- `recently_updated_neighbor`
- `same_type_same_project`

これで AI は本命だけでなく、1 歩だけ外れた周辺を読む理由を得られる。

### 2. `impact` を「影響範囲」から「違和感検出」へ広げる

現状の `impact` は `read_first`, `related_tasks`, `decision_records`, `stale_watch` を返す。ここに以下を追加:

- `unusual_neighbors`
- `isolated_changes`
- `missing_decision_links`
- `unreflected_specs`

changed files に対して、

- 思ったよりリンクが少ない
- 変更されたのに decision が見当たらない
- 近縁ノードが古いまま
- 同テーマの別ノートが置き去り

を返す。ただの関連列挙ではなく「変なのでは？」を返すモード。

### 3. `related` を類似度だけでなく「異質な近傍」にする

現状の `related` は近接文脈の確認。ここに二系統を明示:

- `related --mode close`: 近い文書
- `related --mode surprising`: 直接は近くないが、依存・型・語彙・更新履歴のどれかで引っかかる文書

人間の気づきは後者からよく出るので、近いものと意外に近いものを分けるのは価値がある。

### 4. `orphans` を消極的機能で終わらせない

設計書上 `orphans` は存在する。孤立ノード一覧だけだと「ふーん」で終わりやすい。

そこで `orphans` は

- 孤立している
- でも type/status/updated 的に重要そう
- 今回タスクと語彙的または project 的に近い

ものを上位に出す。単なる孤立ではなく「危険な孤立」を出す。

### 5. `stale` を「古い」から「古いのに近い」へ

`stale` も完了済み。気づきに効くのはただ古いノートではなく

- 今回の task に近い
- changed files に近い
- read_order の上位ノードに近い

のに更新されていないノート。

stale 系は単独コマンドでもよいが、むしろ `start` / `impact` / `finish --dry-run` の返り値に混ぜたほうが効く。

### 6. `finish --dry-run` に「気づき監査」を入れる

現状の finish は出口確認。ここに最も相性がいいのが discovery。

`finish --dry-run` で追加したいのは

- `suspiciously_unupdated`
- `likely_missing_links`
- `unreviewed_neighbors`
- `decision_gap_candidates`

つまり「作業は終わりそうだけど、本当にこのまま閉じていい？」を返す。入口ではまだわからなかったズレが、変更後なら見える。

## 実装優先順位

### Phase 1: 低コストで効く

- `start.discovery_candidates`
- `impact.unusual_neighbors`
- `finish.suspicion_signals`

既存出力に列を足す発想。導入しやすい。

### Phase 2: mdex らしさが立つ

- `related --mode surprising`
- `orphans` の危険度スコア化
- `stale` の task/impact 近傍優先

### Phase 3: 本当に「気づく」感じが出る

- reason code の充実
- どの discovery 候補が実際に開かれたかの観測
- 開かれた候補から次回スコアを少し調整

ここまで行くと、ようやく「気づきを支援するツール」と言いやすい。

## 契約面への影響 (注意)

discovery lane の追加は **出力の contract 拡張** になる。`docs/update_policy.md` (T20260419142026) の「慎重に変える」側に属する。実施する場合は:

- CHANGELOG 必須
- schema versioning に従って minor bump
- 既存 primary keys は壊さず additive に追加

## 辛口まとめ

今の mdex は「効率のいい索引・導線ツール」。ここから「気づきももたらすツール」に上がるには、正しい入口を返すだけでは足りず、入口から少し外れた有益な寄り道を返す必要がある。

追加すべき価値は検索精度の向上ではなく、**意図的な逸脱の設計**。

この方向は mdex の思想と相性が良い。全文読みに戻らず、でも発見を捨てないから。

## 関連タスク

- T20260419141737: README の AI 向け契約面積最適化
- T20260419142026: docs/update_policy.md 起こし
- T20260419142613: AGENT.md / README / convention.md 責務分担再設計

discovery lane を入れるなら上記 3 件と同時期に検討するのが効率的（ドキュメント契約面に触るため）。
