---
type: design
project: mdex
status: active
updated: 2026-04-19
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
2. 全コマンドは JSON を返す（成功: stdout / 失敗: stderr）。
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

## モジュール責務

```
mdex/
  cli.py         コマンド入口
  dbresolve.py   repo/config/db 解決
  scanner.py     対象ファイル列挙
  parser.py      frontmatter/link/summary 抽出
  builder.py     ノード・エッジ生成
  indexer.py     JSON / SQLite 出力
  store.py       SQLite API
  resolver.py    first / related
  context.py     context 選別（actionable 出力あり）
  start.py       start JSON 生成
  gittools.py    git changed files 収集
  impact.py      changed file 起点の分類
  finish.py      finish の dry-run / apply / scan 制御
  scaffold.py    new / stamp
  enrich.py      summary 更新
  reader.py      node-id から本文取得
  tokens.py      トークン見積もり
```

## フェーズ状況 (2026-04-13)

### Phase 1（索引化）: 完了

- `scan` で JSON + SQLite 出力
- edge `resolved` 保持
- `list` / `query` 提供

### Phase 2（探索）: 完了

- `find` / `first` / `related` / `orphans` / `stale`

### Phase A（導線）: 完了

- `start`
- `context --actionable`
- `doctor`
- `impact`
- `finish` (`--dry-run`, `--summary-file`, `--scan`)
- `new` / `stamp`
- DB 自動解決

## コマンド設計

### start

- 入力: `task`, `budget`, `limit`
- 出力: `recommended_read_order`, `recommended_next_actions`, `confidence`

### impact

- 入力: changed file path 群または `--changed-files-from-git`
- 出力: `read_first`, `related_tasks`, `decision_records`, `stale_watch`

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
