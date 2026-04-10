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
Markdown / JSON として外部化された運用コンテクスト
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

Markdown や AI 向け JSON として外部化し、mdex で索引化することで、AI の作業が**継続・累積・共有**可能になります。

---

## エージェントの使い方

### タスク着手時の標準フロー

```bash
# 1) まず索引を作る / 更新する（seed index）
mdex scan --root <dir> --db mdex_index.db --config control/scan_config.json

# 2) タスク着手前に、読むべき .md を絞り込む（最初のステップ）
mdex context "感情モデルのバグを直したい" --db mdex_index.db --budget 4000
mdex context "..." --db mdex_index.db --include-content

# 3) 入口を探す
mdex find "感情"         --db mdex_index.db --limit 20

# 4) 縦（前提）と横（近傍）を辿る
mdex first foo.md        --db mdex_index.db --limit 10
mdex related foo.md      --db mdex_index.db --limit 10

# 5) 入出力エッジを確認する
mdex query --node foo.md --db mdex_index.db

# 品質確認（任意）
mdex orphans             --db mdex_index.db
mdex stale               --db mdex_index.db --days 30
```

### ファイルを読んだ後（enrich write-back）

```bash
# 作業 AI / 人間が作った summary を DB に反映する（要約生成は外部で行う）
mdex enrich runtime/emotion.md --db mdex_index.db --summary "この文書が何を決め、いつ参照すべきかの要約..."
mdex enrich --path "<repo-root>/yura/runtime/emotion.py" --db mdex_index.db --summary-file ./summary.txt
```

**`--db` は必ず明示すること**

```bash
# NG: --db を省略すると mdex_index.db（ローカルDB）に書かれる
mdex enrich foo.md --summary "..."

# OK: 常に対象DBを明示する
mdex enrich foo.md --db C:\Codex\mdex\mdex_codex_index.db --summary "..."
```

**サマリの書き方**

別のエージェントがこのファイルを読むべきか判断するための情報を書く。
- 何が書いてあるか（内容の要点）
- どんな制約・判断・ルールを含むか
- いつ参照すべきか

```text
悪い例: 「このファイルはキャラクター設定を含む。」
良い例: 「ゆらの口調・禁止表現・ペルソナを定義。発話品質に関わる作業の前に参照する。」
```

**既存サマリの上書き**

```bash
# agent summary が既に存在する場合は --force を付ける
mdex enrich foo.md --db C:\Codex\mdex\mdex_codex_index.db --summary "..." --force
```

`enrich` の更新は `node_overrides` に即時反映されるため、`find` / `first` / `related` / `context` で再利用するだけなら再 `scan` は不要です。索引対象ファイルを更新したときだけ `scan` で seed index を再構築してください。

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
- `enrich` は `node_overrides` に summary を反映する write-back 更新コマンドです。
- `context` の `score_breakdown` は調整対象の設計情報を含みます。

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
| `stale`   | 品質監視 | `summary_source=seed` かつ更新経過日数 | enrich 候補を見つける |
| `context` | 複合 | キーワード + グラフ + 予算 | タスク着手前に読むべき .md を絞り込む主要入口。まずこれを呼ぶ |
| `enrich`  | 更新 | 明示入力された summary | ドキュメントを読んだあと summary を更新する write-back。索引品質を上げる |

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
  scanner.py   .md / .json / .jsonl 列挙
  parser.py    Markdown / JSON の metadata / link / summary 抽出
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

mdex は「seed index 更新（scan）」と「agent write-back（enrich）」を分けて運用します。

```
書く            規約に沿って Markdown や AI 向け JSON を残す  →  docs/convention.md
  ↓
seed 更新       mdex scan（Markdown / JSON から索引を再構築）
  ↓
読む            mdex context → find / first / related / query
  ↓
write-back 更新 mdex enrich --summary/--summary-file（node_overrides に即時反映）
  ↓
再 scan（任意） Markdown 本文を更新した場合のみ実行
```

エージェントは作業前に `mdex context` でドキュメントを絞り、読んだあとに `mdex enrich` でサマリを更新する。このサイクルを回すほど索引の精度が上がる。

索引を作っただけでは半分です。**エージェントが何をどう残すか**が索引の品質を決めます。

---

## 設計ドキュメント

- 詳細設計・フェーズ状況: `docs/design.md`
- **記録規約（エージェント必読）**: `docs/convention.md`
- エージェント向け運用ルール: `AGENT.md`
