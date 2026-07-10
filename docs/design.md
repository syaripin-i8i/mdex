---
type: design
project: mdex
status: active
updated: 2026-07-10
---

# mdex 設計書

## Scope

- この文書は architecture / persistence / command responsibility / schema の正本です
- historical planning doc は `docs/archive/` に隔離し、現行仕様はこの文書を正本として扱います
- workflow contract は `README.md` を参照してください
- execution heuristics は `AGENT.md` を参照してください
- first adoption path は `docs/getting_started.md` を参照してください
- existing repo adoption は `docs/adoption_guide.md` を参照してください
- input note contract は `docs/convention.md` を参照してください
- context hygiene policy は `docs/context_hygiene.md` を参照してください

## 一言で言うと

`mdex` は Markdown / JSON の運用知識を SQLite に索引化し、  
AI エージェントが作業開始 (`start`) と作業終了 (`finish`) を定型化するための CLI。

## 設計原則

1. CLI は薄く、ロジックは `mdex/` へ分離する。
2. 全コマンドは JSON を返す（成功: stdout / 失敗: stderr）。例外: `find` の検索済み 0 件は stdout 契約を変えずに stderr へ `zero_hits` 開示を 1 行出す（exit 0。成否判定は exit code が正本）。
3. SQLite を正本とし、`node_overrides` を scan と分離して保持する。
4. 入口 (`start`) と出口 (`finish`) を最優先で安定化する。
5. 契約 field 名は README / AGENT / design で表記ゆれさせない。

## 永続化モデル

```
scan で再生成される seed         scan をまたいで保持
──────────────────────         ──────────────────
nodes                           node_overrides
edges
index_metadata
```

`list_nodes` / `get_node` は `node_overrides` をマージして返す。
`index_metadata.scan_manifest` は repo/root/config hash/index lane/DB/JSON/scan ID
を保持し、DB と JSON に同じ generation を対応づける。`finish --scan` はこの
manifest を fail-closed で再検証し、lock 内でも旧 scan ID を照合する。

## DB 自動解決

`dbresolve.py` が以下を共通解決する。

1. `--db`
2. `MDEX_DB`
3. `.mdex/config.json` の `db`
4. `.mdex/mdex_index.db`
5. `mdex_index.db`

失敗時は `resolution_attempts` を含む JSON エラーを返す。

## Scan Config Contract

- `scan` は `scan_roots` (array) を正式サポートします
- `scan_root` (string) は後方互換 alias として扱います
- `scan_roots` と `scan_root` が同時にある場合は `scan_roots` を優先し、warning を返します
- 複数 root で同一 `node_id` が衝突した場合は fail-closed で `scan` を失敗させます
- config/default の DB/JSON 出力は `.mdex/` 内に限定します
- DB と JSON、および相互の lock path が衝突する設定は拒否します
- DB/JSON pair 全体を canonical path 順で lock し、別DBが同じJSONを共有する場合も競合を拒否します

## モジュール責務

```
mdex/
  cli.py         コマンド入口
  contract.py    JSON contract metadata / error code
  db_ownership.py SQLite ownership / legacy schema recognition
  dbresolve.py   repo/config/db 解決
  scanner.py     対象ファイル列挙
  parser.py      frontmatter/link/summary 抽出
  builder.py     ノード・エッジ生成
  indexer.py     JSON / SQLite 出力
  locking.py     scan / enrich の DB 別排他制御
  output_paths.py scan output の境界・衝突検証
  path_identity.py path alias / root identity 検証
  scan_manifest.py scan generation / scope / config identity
  store.py       SQLite API
  resolver.py    first / related
  context.py     context 選別（actionable digest 出力あり）
  multiindex.py  repo / task / memory / artifact index 統合
  start.py       start JSON 生成
  gittools.py    git changed files 収集
  impact.py      changed file 起点の分類
  finish.py      finish の dry-run / apply / scan 制御
  artifacts.py   生成済み観測の分離 index
  observe.py     opt-in local telemetry
  scaffold.py    new / stamp
  enrich.py      summary 更新
  reader.py      node-id から本文取得
  tokens.py      トークン見積もり
```

## フェーズ状況 (2026-07-10)

### Phase 1（索引化）: 完了

- `scan` で JSON + SQLite 出力
- edge `resolved` 保持
- `list` / `query` 提供

### Phase 2（探索）: 完了

- `find` / `first` / `related` / `orphans` / `stale`
- `orphans --missing` で unresolved `links_to` target を集計

### Phase A（導線）: 完了

- `start`
- `context --actionable`
- `doctor`
- `impact`
- `finish` (`--dry-run`, `--summary-file`, `--scan`)
- `new` / `stamp`
- DB 自動解決

### Phase 3（AI最適化）: 基盤実装済み・継続評価

- explainable context scoring / discovery candidates
- actionable digest / agent prompt pack
- repo / task / memory / artifact の multi-index
- 手動の完全判定 gold set と Recall@k / Precision@k / MRR / p95 latency baseline を `evals/` と `tools/evaluate_quality.py` で運用
- 保留中の Discovery Lane 提案は golden 評価結果を見てから採否判断し、本フェーズ完了のみで自動採用しない

### UI / 可視化: 非スコープ

- mdex 本体は CLI、構造化出力、SQLite index に責務を限定する
- Web UI / graph viewer は mdex のロードマップに含めない
- 可視化が必要な場合は mdex の出力を利用する別プロジェクトとして分離する

## コマンド設計

### start

- 入力: `task`, `budget`, `limit`
- 出力: `recommended_read_order`, `recommended_next_actions`, `actionable_digest`, `confidence`

### context --actionable

- 入力: `query`, `budget`, `limit`
- 出力: `recommended_read_order`, `recommended_next_actions`, `actionable_digest`, `confidence`
- `actionable_digest` は `rg` の代替ではなく、`rg` の前に読む docs / task history / code entrypoints / guardrails / suggested rg を分けて返す

### impact

- 入力: changed file path 群または `--changed-files-from-git`
- 出力: `inputs` (`path`, `exists`, `indexed`), `warnings`, `read_first`, `related_tasks`, `decision_records`, `stale_watch`

### finish

- dry-run: 計画のみ
- apply: `--summary-file` 指定時、Primary 1 件のみ `enrich` 実行
- scan: `--scan` 指定時に最後に再 scan

### new / stamp

- `new task|decision`: 規約準拠テンプレート生成
- `stamp`: frontmatter の `updated` 更新

## 主要スキーマ

### Node

```json
{
  "id": "relative/path/to/file.md",
  "title": "...",
  "type": "decision|task|design|log|spec|reference|unknown",
  "project": "...",
  "status": "active|done|draft|archived|unknown",
  "summary": "...",
  "summary_source": "seed|agent",
  "summary_updated": "ISO date",
  "tags": [],
  "updated": "ISO date",
  "links_to": [],
  "depends_on": [],
  "relates_to": []
}
```

### Edge

```json
{
  "from": "a.md",
  "to": "b.md",
  "type": "links_to|depends_on|relates_to",
  "resolved": true
}
```

## 作業サイクル

```
記録する  -> scan -> start -> 実装 -> finish --dry-run -> finish --summary-file ... --scan
```

## 参照

- Phase A 詳細（historical planning）: `docs/archive/phase_a_agent_flow.md`
- 記録規約: `docs/convention.md`
