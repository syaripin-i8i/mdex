---
type: design
project: mdex
status: active
updated: 2026-04-13
tags:
  - phase-a
  - workflow
  - agent
relates_to:
  - docs/design.md
  - docs/context_scoring.md
---

# Phase A AI導線 詳細設計

> Historical planning doc (archived). Current state and canonical contracts are in `docs/design.md`.

`mdex` を「呼べば役立つ補助ツール」から、「作業開始と作業終了で自然に通る入口/出口」へ寄せるための詳細設計。
この文書は 2026-04-13 時点では実装計画であり、実装本体は未着手。

## 目的

- `--db` を毎回覚えなくても `mdex` が使える状態にする。
- `mdex start` を AI の最初の一手にし、読む順序と次アクションを返せるようにする。
- `mdex finish` を AI の最後の一手にし、`enrich` 候補と必要な後処理をまとめて返せるようにする。
- コード変更起点でも `mdex` が効くように、changed files と文書ノードを結び付ける。
- 既存の `context` / `enrich` / `find` / `first` / `related` の責務は保ち、互換性を壊し過ぎない。

## 非目標

- LLM による自動要約生成
- `context` の大規模な再学習・再スコアリング
- GUI や人間向けテーブル UX の強化
- 全文検索エンジン化

## 成功条件

- `mdex context "..."` と `mdex finish --task "..."` が `--db` なしで動く。
- `mdex start "..."` が単なる候補一覧ではなく、読む順序と次アクションを JSON で返す。
- `mdex finish --dry-run` が changed files と enrich 候補を返す。
- `mdex impact --changed-files-from-git` が設計書/タスク/決定記録を分類して返す。
- `README.md` / `AGENT.md` / `docs/design.md` の標準フローが `start` / `finish` 中心へ移る。

## 変更全体像

Phase A では CLI を薄いまま保つため、責務を以下のように分ける。

```text
runtime/
  cli.py         既存。共通 DB 解決フックと新コマンド追加
  dbresolve.py   新規。DB 自動解決、repo root 探索、.mdex/config.json 読み込み
  context.py     既存。legacy 出力を維持しつつ actionable 出力を追加
  start.py       新規。context / first / related を束ねて開始用 JSON を作る
  gittools.py    新規。git changed files 収集
  impact.py      新規。changed file path から関連ドキュメントを寄せる
  finish.py      新規。終了時レポート、enrich 候補選別、scan 実行制御
  scaffold.py    新規。new / stamp テンプレートと frontmatter 更新
```

`store.py` は引き続き SQLite 読み書きの責務に寄せ、ファイルシステム探索や環境変数解決は `dbresolve.py` に分離する。
これにより `store.py` は純粋な storage API のまま維持できる。

## 共通 DB 自動解決

### 解決方針

読み取り系コマンドと更新系コマンドは、共通で `resolve_db_path()` を通す。

優先順位:

1. CLI 引数 `--db`
2. 環境変数 `MDEX_DB`
3. カレントディレクトリまたは親ディレクトリの `.mdex/config.json`
4. repo root の既定候補
   - `.mdex/mdex_index.db`
   - `mdex_index.db`
5. どれも使えない場合に JSON エラー

### repo root 検出

- まず `cwd` から親方向へ辿り、最初に `.mdex/config.json` が見つかったディレクトリを repo root とみなす。
- `.mdex/config.json` が見つからない場合は、最初に `.git` を持つディレクトリを repo root とみなす。
- どちらも無い場合は `cwd` を repo root とみなす。

### `.mdex/config.json` の最小仕様

Phase A で使うキー:

```json
{
  "db": ".mdex/mdex_index.db",
  "scan_root": ".",
  "scan_config": "control/scan_config.json",
  "task_dir": "tasks/pending",
  "decision_dir": "decision"
}
```

仕様:

- config ファイルは常に `repo_root/.mdex/config.json` に置く。
- 相対パスは `repo_root` 基準で解決する。
- 未知のキーは無視する。
- `db` は read 系では存在確認あり、write 系では親ディレクトリが作成可能なら採用してよい。

### read/write モード差分

- `must_exist=True`
  - `list` / `open` / `query` / `find` / `orphans` / `stale` / `related` / `first` / `context` / `start` / `finish` / `impact`
- `must_exist=False`
  - `scan` の出力先 DB

補足:

- CLI で `--db` を明示した場合は最優先で採用し、read 系で存在しなければ即エラーとする。
- `MDEX_DB` と config の候補は、存在しなければ次候補へフォールバックしてよい。
- エラー時は `resolution_attempts` を返し、どこを見に行ったかを JSON に残す。

### エラー JSON

```json
{
  "error": "db not found",
  "resolution_attempts": [
    { "source": "env", "path": "<repo-root>/missing.db", "exists": false },
    { "source": "config", "path": "<repo-root>/.mdex/mdex_index.db", "exists": false },
    { "source": "repo_default", "path": "<repo-root>/mdex_index.db", "exists": false }
  ],
  "hint": "run mdex scan --root <repo-root> --db <repo-root>/.mdex/mdex_index.db"
}
```

## `context` の action-oriented 拡張

### 方針

- 既存 `select_context()` のスコアリング本体は可能な限り維持する。
- 互換性維持のため、既存の `nodes` 出力は残す。
- 新形式は `--actionable` フラグで有効化し、`mdex start` は常にこの形式を使う。

### 追加フィールド

```json
{
  "query": "emotion bug",
  "nodes": [],
  "recommended_read_order": [
    {
      "id": "design/emotion.md",
      "title": "Emotion Design",
      "priority": 1,
      "source": "context",
      "reason": "high lexical match in title/summary"
    }
  ],
  "recommended_next_actions": [
    "open design/emotion.md",
    "open runtime/emotion.md",
    "search code for emotion bug"
  ],
  "deferred_nodes": [
    {
      "id": "logs/old-session.md",
      "reason": "related but low priority for first pass"
    }
  ],
  "confidence": 0.78,
  "why_this_set": [
    "top nodes contain direct query hits",
    "prerequisite documents are pulled ahead",
    "done/archived nodes were deprioritized"
  ],
  "total_tokens": 812,
  "budget": 4000
}
```

### read order 生成ルール

1. 既存 `context` 上位ノードを基点にする。
2. 上位 1-3 ノードに対して `first()` を呼び、前提ノードを最大 2 件ずつ前方へ差し込む。
3. すでに選ばれたノードは重複除去する。
4. `related()` の上位ノードは、初回読解に不要なら `deferred_nodes` 側へ落とす。
5. `status=done` / `archived` は直接一致が強い場合のみ残す。

### confidence の算出

Phase A では簡易式に留める。

- `direct_match_ratio`: 選抜ノード中、keyword score が 0 より大きい割合
- `graph_supported_ratio`: 選抜ノード中、`first` または graph boost で補強された割合
- `fresh_ratio`: 選抜ノード中、recency score が 0 より大きい割合

計算式:

```text
confidence = min(1.0, 0.25 + direct_match_ratio * 0.4 + graph_supported_ratio * 0.2 + fresh_ratio * 0.15)
```

小数第 2 位で丸める。

## `mdex start`

### 役割

`mdex start "<task>"` は、作業開始時に叩く単一入口とする。
内部では `context --actionable` を主軸にし、必要な範囲で `first` / `related` を束ねる。

### CLI

```bash
mdex start "<task>" [--db <sqlite>] [--budget <n>] [--limit <n>] [--include-content]
```

### 処理順

1. DB を自動解決する。
2. DB が見つからない場合は `scan` を促す JSON エラーを返す。
3. `context(..., actionable=True)` を実行する。
4. 上位ノードについて前提/近傍を補正し、`recommended_read_order` を確定する。
5. `recommended_next_actions` を 3-5 件生成する。

### next actions 生成ルール

- 先頭 2 件までは `open <node-id>` を出す。
- 読み順上位に `design` / `decision` があれば、その後に対応するコード探索を 1 件だけ出す。
- クエリから 2 語以上の有効キーワードが取れた場合は `search code for <joined keywords>` を出す。
- 低信頼時のみ `run mdex find "<query>"` を 1 件追加してよい。

### 出力 JSON

```json
{
  "task": "emotion bug fix",
  "db": {
    "path": "<repo-root>/.mdex/mdex_index.db",
    "source": "repo_default"
  },
  "index_status": {
    "ready": true,
    "generated": "2026-04-11T04:00:00+00:00"
  },
  "recommended_read_order": [],
  "recommended_next_actions": [],
  "deferred_nodes": [],
  "confidence": 0.78,
  "why_this_set": [],
  "total_tokens": 812,
  "budget": 4000
}
```

## git changed files 収集

### 共有化方針

`finish` と `impact` の両方で使うため、`runtime/gittools.py` に切り出す。

### 収集対象

- `git diff --name-only --cached`
- `git diff --name-only`
- `git ls-files --others --exclude-standard`

仕様:

- POSIX 形式へ正規化する。
- 重複は keep-order で削除する。
- 基準ディレクトリは git top-level。
- `--changed-files-from-git` が指定されたのに git repo ではない場合は JSON エラー。
- `finish` のデフォルト動作では、git repo なら自動収集、git repo でなければ空配列で継続してよい。

## `mdex impact`

### 役割

コード変更ファイル起点で、先に読むべき文書群を返す。
`finish` もこの評価結果を再利用し、enrich 対象候補の根拠にする。

### CLI

```bash
mdex impact <path> [<path> ...] [--db <sqlite>] [--limit <n>]
mdex impact --changed-files-from-git [--db <sqlite>] [--limit <n>]
```

### スコアリング

初期実装は単純な path / link / frontmatter 一致でよい。

加点順:

1. 文書本文または JSON から変更ファイルの絶対/相対パス参照が取れる
2. 変更ファイルと文書の stem が一致する
3. ディレクトリセグメントが一致する
4. 該当文書と `depends_on` / `relates_to` / `links_to` でつながる決定記録・タスクがある
5. stale ノードである

### 出力 JSON

```json
{
  "inputs": ["runtime/emotion.py"],
  "read_first": [
    { "id": "design/emotion.md", "reason": "same stem + shared directory segment", "score": 6.5 }
  ],
  "related_tasks": [
    { "id": "tasks/pending/T20260101000001.md", "reason": "linked from impacted design", "score": 3.1 }
  ],
  "decision_records": [
    { "id": "decision/emotion-arch.md", "reason": "depends_on neighbor", "score": 2.8 }
  ],
  "stale_watch": [
    { "id": "runtime/emotion.md", "reason": "seed summary is stale but path matches", "score": 2.3 }
  ]
}
```

## `mdex finish`

### 役割

終了時に「何を見たか」「何を更新すべきか」「scan が必要か」を一つの JSON へまとめる。
Phase A では保守的に動かし、自動要約生成や曖昧な多重更新は行わない。

### CLI

```bash
mdex finish --task "<task>" [--db <sqlite>] [--changed-files-from-git] [--scan] [--summary-file <path>] [--dry-run]
```

### 動作モード

- `--dry-run`
  - DB 書き込みも `scan` も行わず、候補と提案だけ返す。
- `--summary-file` なし
  - enrich 対象候補を返す計画モード。
- `--summary-file` あり
  - まず候補を選別し、Primary 候補が 1 件に絞れる場合だけ `enrich` を実行する。
  - Primary が 2 件以上ある場合は `requires_manual_targeting=true` として更新しない。
- `--scan`
  - dry-run でない場合のみ、最後に `scan` を再実行する。

### enrich の Primary 判定

以下のいずれかを満たす場合のみ Primary とみなす。

- changed file path 参照が文書内で exact match
- impact score 1 位が 2 位の 1.5 倍以上
- changed file stem と文書 stem が一致し、かつ `type` が `design` または `reference`

### 出力 JSON

```json
{
  "task": "emotion fix",
  "dry_run": true,
  "db": {
    "path": "<repo-root>/.mdex/mdex_index.db",
    "source": "repo_default"
  },
  "changed_files": [
    { "path": "runtime/emotion.py", "source": "git" }
  ],
  "enrich_candidates": [
    {
      "id": "design/emotion.md",
      "kind": "primary",
      "reason": "same stem + direct path reference"
    },
    {
      "id": "runtime/emotion.md",
      "kind": "secondary",
      "reason": "stale summary but close to changed file"
    }
  ],
  "applied_enrichments": [],
  "scan": {
    "requested": false,
    "ran": false
  },
  "recommended_next_actions": [
    "review design/emotion.md before closing the task",
    "prepare summary text for design/emotion.md"
  ],
  "requires_manual_targeting": false
}
```

## `mdex new` / `mdex stamp`

### `new`

CLI:

```bash
mdex new task "<title>"
mdex new decision "<title>"
```

生成規則:

- `task`
  - 出力先は config の `task_dir`、無ければ `tasks/pending`
  - ファイル名は `T<UTC timestamp>.md`
  - frontmatter は `type: task`, `status: pending`, `updated: <today>`
- `decision`
  - 出力先は config の `decision_dir`、無ければ既存ディレクトリ優先順で `decision` → `decisions`
  - ファイル名は slug 化した `<slug>.md`
  - frontmatter は `type: decision`, `status: active`, `updated: <today>`

テンプレート本文は `docs/convention.md` の最小構成に合わせる。

### `stamp`

CLI:

```bash
mdex stamp <node-id or path>
```

仕様:

- node-id が渡された場合は DB と `scan_root` から絶対パスへ戻す。
- path が渡された場合はそのまま解決する。
- 既存 frontmatter に `updated` があれば置換する。
- frontmatter に `updated` が無ければ closing boundary の直前へ追加する。
- frontmatter 自体が無い場合は先頭へ最小 frontmatter を追加する。

## ドキュメントと command assets

実装後に以下を更新する。

- `README.md`
  - 標準フローを `scan → start → 作業 → finish` に更新
- `AGENT.md`
  - 長い手順をやめ、「最初に `start`」「最後に `finish`」へ整理
- `docs/design.md`
  - Phase 3/4 の状態を実装状況に合わせて更新
- `docs/convention.md`
  - `enrich` / `scan` の手順を `finish` ベースへ寄せる
- command assets
  - `scripts/mdex_start.py`
  - `scripts/mdex_finish.py`
  - `.claude/commands/start-task.md`
  - `.claude/commands/finish-task.md`

## テスト設計

追加テストは以下を最低ラインとする。

- DB 自動解決
  - `--db` なしで repo root の `mdex_index.db` を解決できる
  - `MDEX_DB` を使える
  - `.mdex/config.json` の `db` を使える
  - 解決失敗時に JSON エラーと `resolution_attempts` が返る
- `start`
  - `quality_repo` で valid JSON を返す
  - `recommended_read_order` が空でない
  - `recommended_next_actions` が 1 件以上ある
- `context --actionable`
  - 既存 `nodes` を維持したまま追加フィールドが返る
- `finish --dry-run`
  - git changed files 候補を返す
  - enrich 候補を返す
  - dry-run では DB を書き換えない
- `impact`
  - path 直指定と `--changed-files-from-git` の両方を確認
- `new` / `stamp`
  - 規約に沿ったテンプレートが生成される
  - `updated` が更新される
- 回帰
  - 既存 `context` / `enrich` を壊していない
  - `quality_repo` の `scan → first → related → enrich` が維持される

## 実装順とタスク対応

1. `T20260412190917`: DB 自動解決基盤
2. `T20260412190918`: `start` と action-oriented `context`
3. `T20260412190919`: `finish --dry-run` と git changed files 収集
4. `T20260412190920`: `finish` 本体と `impact`
5. `T20260412190921`: `new` / `stamp`
6. `T20260412190922`: command assets・ドキュメント更新・回帰確認

## 判断メモ

- `start` / `finish` は「検索結果」ではなく「次の行動が決まる JSON」を返す。
- 自動化は保守的に始め、曖昧な summary 更新は Phase A では避ける。
- `context` の既存スコアリングは活かし、入口/出口の UX だけ先に変える。
- まずは AI が自然に叩けることを優先し、アルゴリズムの美しさは後段で詰める。
