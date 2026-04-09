# mdex

**このツールは AI エージェント（Claude Code・Codex など）のために作られています。人間向けではありません。**

---

## 何を解くツールか

AI エージェントが作業するとき、コンテクストは3層に分かれます。

| 層 | 定義 |
|----|------|
| **入力コンテクスト** | モデル呼び出し時に実際に渡される情報量 |
| **推論対象コンテクスト** | 今回のタスクに本来必要な情報の範囲。入力より小さいのが理想 |
| **運用コンテクスト** | 設計判断・過去の作業記録・制約・注意事項など、コード外に保持される継続的知識 |

問題は、**運用コンテクストをどれだけ書き残しても、索引がなければ必要な時に引き出せない**ことです。  
エージェントはファイルを全部読むか、重要な情報を見落とすかの二択に迫られます。

mdex はこの問題を解きます。

```
Markdown として外部化された運用コンテクスト
    ↓  mdex scan でグラフ + SQLite に索引化
    ↓  mdex context でタスクに必要な最小セットを選別
推論対象コンテクスト（読むべきファイルと順序）
    ↓  エージェントが本文を読んで作業
入力コンテクストを必要最小限に保つ
```

作業の質が上がり、見落としが減り、手戻りが減ります。トークンコストの削減はその結果です。

---

## なぜ「AI の中だけで完結」させてはいけないか

設計判断・作業記録・制約をAI の会話履歴だけに置くと：

- セッションをまたいで消える
- 別のエージェントが参照できない
- 累積されず、毎回ゼロから始まる

Markdown として外部化し、mdex で索引化することで、AI の作業が**継続・累積・共有**可能になります。

---

## エージェントの使い方

### タスク着手前

```bash
# 何を読むべきかを問い合わせる
mdex context "感情モデルのバグを直したい" --db mdex_index.db --budget 4000
```

```json
{
  "query": "感情モデルのバグを直したい",
  "nodes": [
    { "id": "runtime/emotion.md", "priority": 1, "reason": "直接対象", "estimated_tokens": 820 },
    { "id": "AGENT.md",           "priority": 2, "reason": "制約・注意事項", "estimated_tokens": 430 }
  ],
  "total_tokens": 1250,
  "budget": 4000
}
```

本文も一括取得したい場合：

```bash
mdex context "..." --db mdex_index.db --include-content
```

### ファイルを読んだ後

```bash
# 作業 AI / 人間が作った summary を DB に反映する（要約生成は外部で行う）
mdex enrich runtime/emotion.md --db mdex_index.db --summary "この文書が何を決め、いつ参照すべきかの要約..."
mdex enrich --path "<repo-root>/yura/runtime/emotion.py" --db mdex_index.db --summary-file ./summary.txt
```

`enrich` で更新した summary は `node_overrides` に保存されるため、次回 `mdex scan` 後も保持されます。

### 本流コマンド（Phase 2 / 安定化中）

```bash
# 構造検索
mdex find "感情"         --db mdex_index.db   # キーワード検索
mdex related foo.md      --db mdex_index.db   # 横方向の関連ノード
mdex first foo.md        --db mdex_index.db   # 縦方向の前提ノード列（depends_on 逆辿り）
mdex orphans             --db mdex_index.db   # 孤立ノード（索引品質確認）
mdex query --node foo.md --db mdex_index.db   # 入出力エッジの方向付き表示
```

### experimental（Phase 3 / 評価中）

```bash
mdex context "感情モデルのバグを直したい" --db mdex_index.db --budget 4000
mdex enrich foo.md --db mdex_index.db --summary "この文書が何を決め、いつ参照すべきか..."
mdex enrich --path "<repo-root>/yura/docs/foo.md" --db mdex_index.db --summary-file ./summary.txt
```

### 索引の構築・更新

```bash
mdex scan --root <dir> --db mdex_index.db --config control/scan_config.json
```

## 再現サンプル（fixtures/quality_repo）

`tests/fixtures/quality_repo` を使うと、scan → first → related → enrich をそのまま再現できます。

```bash
mdex scan --root tests/fixtures/quality_repo --db .tmp_quality.db
mdex first design/root.md --db .tmp_quality.db --limit 2
mdex related design/root.md --db .tmp_quality.db --limit 3
mdex enrich design/root.md --db .tmp_quality.db --summary "Root Design の要点を短く更新した summary"
```

期待される出力（簡略）:

```json
{
  "nodes": 6,
  "edges": {
    "total": 8,
    "resolved": 6,
    "unresolved": 2,
    "resolution_rate": 75.0
  }
}
```

```json
{
  "prerequisites": [
    { "id": "spec/b.md", "distance": 2, "reason": "transitive depends_on (depth 2)" },
    { "id": "decision/a.md", "distance": 1, "reason": "direct depends_on" }
  ]
}
```

```json
{
  "related": [
    { "id": "decision/a.md" },
    { "id": "spec/b.md" },
    { "id": "tasks/pending/T20260101000001.md" }
  ]
}
```

```json
{
  "status": "enriched",
  "node_id": "design/root.md",
  "summary_source": "agent",
  "skipped": false
}
```

### 公開前の最小メトリクス（quality_repo）

前提は `tests/fixtures/quality_repo` と `control/scan_config.json` です（コマンドは上の再現サンプルを使用）。

1. `scan` 後の edge 品質は `total=8` / `resolved=6` / `unresolved=2` / `resolution_rate=75.0`。
2. `first design/root.md --limit 2` の前提順は `spec/b.md` → `decision/a.md`。
3. `enrich design/root.md ...` の後に再度 `scan` しても、`design/root.md` の `summary_source` は `agent` のまま保持される。

注記:
- `enrich` は experimental（Phase 3 / 評価中）です。
- `context` も experimental で、`score_breakdown` は調整対象の設計情報を含みます。

---

## 全出力は JSON

すべてのコマンドは JSON を返します。エラーも JSON です。

```json
{ "error": "node not found", "node_id": "missing.md" }
```

人間が読みやすいテキスト形式が必要な場合のみ `--format table` を使ってください。

---

## コマンド責務（境界）

| コマンド | 方向 | 根拠 | 用途 |
|---------|------|------|------|
| `find`    | 入口 | キーワード一致 | まずどこから読むかを探す |
| `related` | 横（近傍） | エッジ重みスコア + タグ/型一致 | 関連して読むべきものを探す |
| `first`   | 縦（前提） | `depends_on` 逆辿り | このノードを理解する前に読むべきものを得る |
| `context` | 複合 | キーワード + グラフ + 予算 | experimental。作業用コンテキストを切り出す |
| `enrich`  | 更新 | 明示入力された summary | experimental。summary 更新口として記録品質を上げる |

---

## セットアップ

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"   # テスト込み
```

依存: Python 3.10+, PyYAML

---

## モジュール構成

```
runtime/
  scanner.py   .md 列挙
  parser.py    frontmatter / metadata / link / summary 抽出
  builder.py   ノード・エッジ生成（参照解決・resolved 判定）
  indexer.py   JSON / SQLite 書き出し
  store.py     SQLite 読み出し API
  resolver.py  related / first ロジック
  context.py   context コマンドのコア（キーワード + グラフ選別）
  enrich.py    summary 更新口（要約生成は外部で実施）
  tokens.py    トークン見積もり
  reader.py    node-id から本文取得
  cli.py       コマンド入口
```

---

## 作業サイクル

mdex は「書く → 索引化 → 読む → 改善」のサイクルで機能します。

```
書く    規約に沿って Markdown を残す  →  docs/convention.md
  ↓
索引化  mdex scan
  ↓
読む    mdex context → 必要なファイルを特定して読む
  ↓
改善    mdex enrich --summary/--summary-file → summary を更新して次のエージェントに繋ぐ
        mdex scan   → 索引に反映
```

索引を作っただけでは半分です。**エージェントが何をどう残すか**が索引の品質を決めます。

---

## 設計ドキュメント

- 詳細設計・フェーズ状況: `docs/design.md`
- **記録規約（エージェント必読）**: `docs/convention.md`
- エージェント向け運用ルール: `AGENT.md`
