# mdex
[![CI](https://github.com/syaripin-i8i/mdex/actions/workflows/ci.yml/badge.svg)](https://github.com/syaripin-i8i/mdex/actions/workflows/ci.yml)

**`mdex` は AI エージェント向けの protocol-first CLI です。**

## 導入前に

`mdex` は public preview です。次の条件がそろう repo での利用を想定しています。

- AI コーディングエージェントを実際の開発タスクで継続利用している
- README、設計書、runbook、ADR など、現行の判断根拠となる文書がある
- main index を小さく保つため、scan 対象・除外・少量の metadata を管理できる
- 推薦結果を実タスクで検証し、誤った入口をそのまま採用しない

次を期待する場合は適合しません。

- 文書がほとんどない repo へのゼロ設定導入
- 全文検索、RAG、knowledge base、人間向け文書閲覧の代替
- corpus 全体を入れるだけで安定した推薦が得られること
- index と source-authority 文書を継続的に管理しない運用

導入判断は、まず 1 repo・3〜5 件の実タスクで行ってください。
0.x minor release では契約が明示的に調整される場合があります。pilot 中は version を固定し、更新前に
`CHANGELOG.md` を確認してください。

- 標準フロー: `scan -> start -> (context | first | related | impact) -> finish --dry-run`
- 成功は `stdout`（schema JSON / utility JSON / table / 本文）、失敗は `stderr` JSON（`exit != 0`）
- field 名は prose より強い契約（別名を導入しない）
- primary keys は「Output Contract」表を参照

## For Agents

- first read order: `README.md -> AGENT.md -> docs/design.md -> docs/convention.md`
- read order と source of truth は同義ではない（正本は本 README の Source of Truth 表）
- `start` と `context --actionable` の詳細な分岐は `AGENT.md` を正本とする
- agents should prefer `recommended_next_actions_v2`; `recommended_next_actions` is deprecated but kept for 0.2.x compatibility
- use `--digest minimal` on `start` / `context --actionable` to reduce context use when the full `actionable_digest` is not needed
- schema-backed success payloads require `contract_schema` / `contract_version`; utility JSON is intentionally unwrapped, while every error payload is schema-backed and also includes machine-readable `code`
- opt-in local telemetry is available with `MDEX_TELEMETRY=1` or `.mdex/config.json` `telemetry: true`; it appends redacted events to `.mdex/telemetry.jsonl`

## For New Adopters

- 10分で試す: `docs/getting_started.md`
- 既存 repo へ入れる: `docs/adoption_guide.md`
- 失敗例と改善例を見る: `docs/examples_before_after.md`
- main index に入れるものを決める: `docs/context_hygiene.md`

## Where mdex Fits

`mdex` は「最初に何を読むべきか」を決めるための薄い index です。

| tool | best at | mdex relationship |
|---|---|---|
| `ripgrep` / full-text search | exact string search across source | `mdex` can recommend where to search, but does not replace it |
| codegraph tools | symbol and dependency structure | `mdex` points to docs and decisions; codegraph explains code topology |
| embedding/RAG systems | broad semantic recall over large corpora | `mdex` favors small, deterministic, contract-shaped context |
| knowledge graphs | rich typed relationships | `mdex` keeps lightweight links: `depends_on` / `relates_to` from frontmatter and `links_to` from body `[[wikilinks]]` |

Use `mdex` for first-pass judgment and workflow contracts. Use the other tools for deep code search, broad recall, or detailed graph analysis.
In other words: `mdex` is the compass before `rg`, not a replacement for `rg`.

## Protocol

| phase | standard command | contract |
|---|---|---|
| before work | `mdex scan`, then `mdex start` | 索引を更新してから入口を決める |
| during work | `mdex context --actionable` / `mdex first` / `mdex related` | 必要な深掘りだけ追加する |
| when changed files exist | `mdex impact` | changed files 起点で関連文書を分類する |
| after work | `mdex finish --dry-run` | 更新候補と後処理を確認する |
| apply summary | `mdex finish --summary-file <path> --scan` | summary が実在するときだけ反映する |

```bash
mdex scan --root <dir> --config control/scan_config.json
mdex start "<task>" --db <db>
mdex context "<task>" --db <db> --actionable
mdex first <node-id> --db <db> --limit 5
mdex related <node-id> --db <db> --limit 5
mdex impact <changed-file-or-node> --db <db>
mdex finish --task "<task>" --db <db> --dry-run
mdex finish --task "<task>" --db <db> --summary-file ./summary.txt --scan
```

`scan` の既定出力は `.mdex/mdex_index.db` と `.mdex/mdex_index.json`。  
`--db` / `--output` 指定時はそれを優先します。
設定ファイルまたは既定値から解決する生成先は `.mdex/` 内に限定されます。
`finish --scan` は直前の通常 `mdex scan` が保存した manifest を検証するため、旧DBは先に再scanしてください。

## Command Selection Rules

短縮版の判断表です。分岐の詳細は `AGENT.md` を参照してください。

| situation | use | contract |
|---|---|---|
| start a task | `mdex start` | 入口を決める |
| start 後に実行可能な次アクションを広く取りたい | `mdex start` -> `mdex context --actionable` | 典型シーケンス（詳細は `AGENT.md`） |
| entrance candidate is already known | `mdex context --actionable` | `start` を省略して時短 |
| inspect from a node | `mdex first` / `mdex related` | 特定文書から読む順と周辺文脈を見る |
| open a node body | `mdex open <node-id>` | indexed node id のみ。絶対パスと `..` は拒否 |
| changed files already exist | `mdex impact` | 影響範囲を changed files 起点で見る |
| close a task | `mdex finish --dry-run` | 出口を先に確認する |
| apply a summary | `mdex finish --summary-file ... --scan` | summary が実在するときだけ反映する |
| update `updated` metadata | `mdex stamp <node-id>` | indexed node id のみ。scan_roots の包含外は拒否 |

## Assumptions

入力ノート規約の正本は `docs/convention.md` です。

- frontmatter の `type` / `status` / `updated` を推奨
- 前提は `depends_on`、関連は `relates_to`
- 本文の `[[target]]` は scan 時に `links_to` として抽出される
- 先頭 summary があるほど `start` / `context` / `finish` が安定

## Links

`mdex` の link model は軽量です。

- `depends_on`: frontmatter の前提リンク
- `relates_to`: frontmatter の関連リンク
- `links_to`: 本文 `[[wikilink]]`、Markdown link、明示 frontmatter `links_to`

`[[target]]` は scan 時に index 内の node id へ解決されます。解決できない target は edge として残り、
`query --node <id>` または `query <id>` の `outgoing.links_to` で `resolved: false`, `missing: true` として見えます。
コーパス全体の未解決 target は `mdex orphans --missing` で、target ごとの `referenced_by` として確認できます。
`related <node-id>` は解決済み edge だけを使い、`incoming:links_to` / `outgoing:links_to` の理由を返します。

## Non-goals

- 全文検索の代替
- source code understanding の完全代替
- 規約の薄い repo での高精度保証
- 人間向け閲覧 UX の最適化

## 再現サンプル（fixtures/quality_repo）

詳細サンプルは `docs/examples.md`。ここでは契約確認に必要な最小例のみ記載します。

```bash
mdex scan --root tests/fixtures/quality_repo --db .mdex/quality_example.db --output .mdex/quality_example.json
mdex start "root decision" --db .mdex/quality_example.db --limit 5
mdex impact design/root.md --db .mdex/quality_example.db
mdex finish --task "root fix" --db .mdex/quality_example.db --dry-run
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
  "task": "root decision",
  "index_status": {
    "fresh": true
  },
  "entrypoint_reason": "ranked_entrypoint_available",
  "recommended_read_order": [
    { "id": "spec/b.md" },
    { "id": "decision/a.md" },
    { "id": "design/root.md" }
  ],
  "recommended_next_actions": [
    "open spec/b.md",
    "open decision/a.md",
    "search code for root decision"
  ],
  "recommended_next_actions_v2": [
    { "command": "mdex", "args": ["open", "spec/b.md"], "reason": "read the recommended node first" }
  ],
  "actionable_digest": {
    "intent": "root decision",
    "relevant_docs": [
      {
        "id": "spec/b.md",
        "title": "Spec B",
        "type": "spec",
        "status": "active",
        "reason": "direct depends_on"
      },
      {
        "id": "decision/a.md",
        "title": "Decision A",
        "type": "decision",
        "status": "active",
        "reason": "high lexical or graph score"
      }
    ],
    "relevant_task_history": [],
    "likely_code_entrypoints": [],
    "known_guardrails": [
      {
        "id": "decision/a.md",
        "title": "Decision A",
        "type": "decision",
        "status": "active",
        "reason": "mentions constraint"
      }
    ],
    "suggested_rg": [
      {
        "command": "rg",
        "args": ["-n", "root|decision", "spec", "decision", "design"],
        "pattern": "root|decision",
        "paths": ["spec", "decision", "design"],
        "reason": "expand from mdex entrypoint candidates into exact source matches"
      }
    ],
    "context_gaps": [
      "no indexed code entrypoint found; use suggested rg to bridge into source code"
    ]
  }
}
```

```json
{
  "inputs": [
    {
      "path": "design/root.md",
      "exists": true,
      "indexed": true
    }
  ],
  "warnings": [],
  "read_first": [
    { "id": "design/root.md" }
  ],
  "related_tasks": [
    { "id": "tasks/pending/T20260101000001.md" }
  ],
  "decision_records": [
    { "id": "decision/a.md" }
  ]
}
```

`changed_files: []` / `enrich_candidates: []` は「該当なしで正常終了」の意味です。

```json
{
  "status": "success",
  "task": "root fix",
  "dry_run": true,
  "noop": true,
  "noop_reason": "dry-run completed with no changed files and no enrich candidates",
  "changed_files": [],
  "enrich_candidates": [],
  "requires_manual_targeting": false
}
```

## CLI 出力境界

### Output Contract

成功と失敗の判別ルール: **成功 = コマンドごとの stdout 形式（空配列でも成功） / 失敗 = schema-backed stderr JSON + exit != 0**。

| category | commands | contract |
|---|---|---|
| schema-backed JSON object | `scan` / `scan-artifacts`, `doctor`, `status`, `start`, `context`, `impact`, `finish` | `contract_schema` / `contract_version` 必須。`scan-artifacts` は `scan.schema.json` を共有 |
| utility JSON（schema なし） | `list`, `find`, `orphans`, `stale` の生配列、`query`, `first`, `related`, `enrich`, `new task`, `new decision`, `stamp` の生 object | 既存の軽量形式を維持し、contract metadata で wrap しない |
| table | `list`, `find`, `orphans`, `stale` の `--format table` | 人間向け tab-separated rows。JSON ではない |
| source text | `open` | indexed node の本文。JSON ではない |

| command | success stdout | primary keys / content |
|---|---|---|
| `scan` | schema-backed object | `nodes`, `edges.total`, `edges.resolved`, `edges.unresolved`, `edges.resolution_rate` |
| `scan-artifacts` | schema-backed object (`scan.schema.json`) | `nodes`, `output.db`, `output.json`, `index_kind`, `roots` |
| `doctor` | schema-backed object | `health`, `status`, `summary`, `checks`, `recommended_next_actions` |
| `status` | schema-backed object | `health`, `status`, `summary`, `indexes`, `recommended_next_actions` |
| `list` | utility array / table | node objects, or table rows with `--format table` |
| `open` | source text | node body text |
| `query` | utility object | `node`, `outgoing`, `incoming`, `stats` |
| `find` | utility array / table | matching node objects, or table rows with `--format table`。検索済み 0 件時の stdout は json で `[]`／table で空出力のまま、stderr に `{"zero_hits": ...}` を 1 行出力（exit 0） |
| `orphans` | utility array / table | orphan nodes; with `--missing`, unresolved `links_to` targets with `referenced_by` |
| `stale` | utility array / table | stale node summary rows, or table rows with `--format table` |
| `first` | utility object | `node`, `prerequisites` |
| `related` | utility object | `node`, `related` |
| `start` | schema-backed object | `task`, `health`, `evidence_identity`, `index_status`, `entrypoint_reason`, `recommended_read_order`, `recommended_next_actions`, `recommended_next_actions_v2`, `actionable_digest`, `confidence` |
| `context` | schema-backed object | `query`, `health`, `evidence_identity`, `recommended_read_order`, `recommended_next_actions`, `recommended_next_actions_v2`, `actionable_digest`, `deferred_nodes`, `confidence`, `zero_hits` |
| `impact` | schema-backed object | `inputs`, `warnings`, `read_first`, `related_tasks`, `decision_records`, `stale_watch` |
| `finish` | schema-backed object | `status`, `task`, `dry_run`, `noop`, `noop_reason`, `changed_files`, `enrich_candidates`, `requires_manual_targeting` |
| `enrich` | utility object | `status`, `node_id`, `summary_source` |
| `new task` / `new decision` | utility object | `status`, `path`, `node_id`, `kind`, `title` |
| `stamp` | utility object | `status`, `node_id`, `path`, `updated` |
| all errors | schema-backed stderr object | `code`, `error` |

`finish --dry-run` の成功判定:

- `dry_run: true` は preview 実行（DB 更新なし）
- `status: "success"` かつ `noop: true` は「正常な no-op 完了」
- `changed_files`, `enrich_candidates` が空でも成功
- `requires_manual_targeting: true` のときは `mdex enrich <node-id> --summary-file <path>` を明示ターゲットで実行

```json
{
  "contract_schema": "https://github.com/syaripin-i8i/mdex/schemas/error.schema.json",
  "contract_version": "0.6.0",
  "code": "db_not_found",
  "error": "db not found",
  "resolution_attempts": []
}
```

自動化は上表の形式をコマンド単位で選択してください。`contract_schema` の有無だけで utility JSON、table、本文を推測しないでください。人間向け整形が必要な場合だけ `--format table` を使用します。

#### Unified index health

`doctor` / `status` / `context` / `start` の `health` は `mdex.health.evaluate_index_health` の同じ結果です。`schemas/health.schema.json` が正本で、必須 field は `status`, `reusable`, `reason`, `scan_id`, `config_hash`, `source_state`, `generated`, `age_hours` です。

- `status`: `healthy | warning | stale | unavailable`
- `reusable`: ranked evidenceを現在のsourceへ再利用できる場合だけ`true`
- source validityをageより先に評価するため、24時間以内でもfingerprint不一致は`stale / false / source_fingerprint_mismatch`
- sourceが一致してもage閾値超過は`stale / false / index_age_exceeded`
- manifest欠損・invalid、source検証不能、DB欠損は`unavailable`で楽観判定しない
- linked worktreeから借用したDBは`worktree_borrowed_index`で再利用不可

安定reason codeは `index_reusable`, `source_fingerprint_mismatch`, `config_hash_mismatch`, `config_file_identity_mismatch`, `scan_input_missing`, `source_state_verification_failed`, `index_age_exceeded`, `worktree_borrowed_index`, `missing_or_invalid_generated_timestamp`, `scan_manifest_missing`, `scan_manifest_invalid`, `index_db_missing`, `index_metadata_unavailable` です。multi-index aggregateは `multi_index_reusable`, `multi_index_not_reusable`, `multi_index_unavailable` を使い、`blocking_reasons`にlane別reasonを保持します。

`index_status` と `evidence_identity` は後方互換fieldとして残りますが、いずれも`health`から生成されます。通常モードのexit codeは変更していません。`health.reusable == false` の`start`は`mdex scan`を推奨し、read orderを`evidence_use: unverified_non_reusable`として返します。

#### Zero-hit disclosure（`zero_hits`）

`find` / `context` が「検索した上で 0 件」のとき、payload に `zero_hits` を開示します。**0 件はメタデータ索引（title / tags / summary / search_terms）の範囲しか束縛せず、文書が存在しない証明ではありません**。field 名は cdex の `zero_hits` と共通語彙です（cdex `51c9ffa`、批准整合 `676a0eb` / decisions/0003・0004）。

- `lanes_searched`: 照合したレーンの自己申告（mdex は `["metadata"]`）
- `lanes_inactive`: 常在 map。探索していないレーンとその理由（`{"body_text": "documented_non_goal"}` — 本文全文は Non-goals に明記の仕様）。空 `{}` は全既知レーン探索済みの意。理由 token は拡張可能な集合であり、消費者は未知 token を拒否しないこと
- `caveat`: 0 件が束縛する範囲の明示
- `remediation`: 説明文（実行可能 command の正本は `recommended_next_actions_v2` / `suggested_rg` の構造化 argv 面）。標準は `rg` と frontmatter tags（`docs/convention.md`）で自己完結し、cdex は「利用可能な場合」の任意ヒント

チャネルはコマンドの出力契約に従います: `context` は payload key（schema-backed）、`find` は stdout の既存契約（json は `[]`、table は空出力）を維持したまま stderr に `{"zero_hits": ...}` を 1 行出力します（exit 0）。成否判定は exit code が正本であり、機械処理で stdout と stderr を merge しないこと。失敗契約（stderr JSON + exit != 0）とは exit code と key（`zero_hits` vs `error` + `code`）で判別します。blank query、budget による全 drop、index DB 欠落を含む未探索 index がある multi-index は「検索した上での 0 件」ではないため `zero_hits` を主張しません。

### Schema Contracts

機械可読契約は `schemas/` を正本とします。schema-backed CLI output:

- `schemas/scan.schema.json`
- `schemas/health.schema.json`
- `schemas/start.schema.json`
- `schemas/context.schema.json`
- `schemas/doctor.schema.json`
- `schemas/status.schema.json`
- `schemas/impact.schema.json`
- `schemas/finish.schema.json`
- `schemas/error.schema.json`

CLI input:

- `schemas/scan_config.schema.json`

Local telemetry（CLI stdout/stderr とは別契約）:

- `schemas/telemetry_event.schema.json`

`contract_schema` は stable logical identifier で、`contract_version` と対で解釈します。公開済みリリースの不変スキーマは `https://raw.githubusercontent.com/syaripin-i8i/mdex/v{contract_version}/schemas/{schema_filename}` から取得します。未リリースの入力スキーマは checkout またはインストール済み package を正本とし、存在しない release tag URL に固定しません。

schema 版運用は `docs/schema_versioning.md` を参照してください。
Agent integration guidance, including safe argv execution for structured actions and `suggested_rg.args`, is in `docs/agent_integration.md`.

## DB Resolution

`--db` 省略時は次の優先順で解決します。

1. CLI 引数 `--db`
2. 環境変数 `MDEX_DB`
3. `.mdex/config.json` の `db`
4. `repo/.mdex/mdex_index.db`
5. `repo/mdex_index.db`
6. repo root が linked git worktree（`.git` が file）の場合のみ、3–5 と同じ相対候補を main checkout root 配下で再試行（`resolution_attempts` に `worktree_common_root` として記録）。この fallback は読み取り専用: DB 新規作成先には使わず、書き込み系コマンド（`enrich` / `stamp` / `finish`）には適用されない

multi-index alias（`--include repo,task,artifacts` 等の task / memory / artifacts DB）も同じ規則に従います: worktree 側の相対候補が欠けている場合のみ、実在する main checkout 側 DB を読み取り専用で借用し、`multi_index.indexes.<alias>.source` に `config:<alias>+worktree_common_root` / `default:<alias>+worktree_common_root` として記録します（`borrowed: true` と worktree 側 `local_path` を併記）。借用 DB は scan の生成先・推奨対象にはなりません。

task aliasは`.mdex/config.json`の`indexes.task.db`を最優先し、未設定時は正式resolverが`.mdex/task_history.db`を使います。互換目的で実在する`.mdex/task_index.db`もresolver内だけでlegacy候補として発見し、sourceを`legacy:task`と明示します。各コマンドがtask DB名を個別推測することはありません。

## Public Scan Config

公開向け既定 config は `control/scan_config.json`。

- `scan_roots` は `"."`（repo root 前提）
- `output_file` は `.mdex/mdex_index.json`
- `.mdex/**` は scan 対象外
- virtualenv / build cache は既定で scan 対象外
- この repo の完了済み `tasks/**` は main index から外し、task-history index に分離
- fixtures / evals / logs / dumps / archive は通常の repo index から除外し、必要時に直接読むか専用 index を使う

`doctor_policy` で `allowlist_patterns`, `generated_path_patterns`, `text_extensions`, `max_text_document_tokens`, `max_node_tokens`, `max_index_tokens`, `max_index_files` を設定できます。doctorはuntracked/generated path、oversized text、単一nodeおよびindex全体のtoken/file budgetを別checkとして返します。Git状態を取得できない場合は`git_state_unavailable` warningになり、healthyとは見なしません。ただしdoctorのhygiene warningは`health.reusable`を自動的にfalseにしません。

```bash
mdex scan --root . --config control/scan_config.json
```

詳しい方針は `docs/context_hygiene.md` を参照してください。
Task-history index の作成方法は `docs/task_index.md` を参照してください。

## Source of Truth

read order と source of truth は同義ではありません。  
上から読む順は `For Agents`、正本はこの表で固定します。

| scope | source |
|---|---|
| workflow contract | `README.md` |
| execution heuristics | `AGENT.md` |
| first adoption path | `docs/getting_started.md` |
| existing repo adoption | `docs/adoption_guide.md` |
| before/after examples | `docs/examples_before_after.md` |
| architecture / persistence / schema | `docs/design.md` |
| input note contract | `docs/convention.md` |
| context hygiene policy | `docs/context_hygiene.md` |
| task-history index | `docs/task_index.md` |
| agent integration | `docs/agent_integration.md` |
| update / versioning policy | `docs/update_policy.md` |
| schema versioning policy | `docs/schema_versioning.md` |

`docs/archive/phase_a_agent_flow.md` は historical planning doc であり、入口契約の正本ではありません。

## Project Operations

- Security policy: [SECURITY.md](SECURITY.md)
- Contributing guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Support matrix: [docs/support_matrix.md](docs/support_matrix.md)
- Release process: [docs/release_process.md](docs/release_process.md)

## Setup

```bash
python -m pip install "mdex-cli==0.6.0"
```

Released source install:

```bash
python -m pip install git+https://github.com/syaripin-i8i/mdex.git@v0.6.0
```

Local checkout install:

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"
```

ロック依存で開発環境を再現する場合:

```bash
python -m pip install --upgrade pip
python .github/scripts/install_from_pylock.py --lock pylock.toml --editable .
```

`pylock.toml` 更新:

```bash
python -m pip lock -e ".[dev]" -o pylock.toml
python .github/scripts/export_release_hashes.py --lock pylock.toml --output .github/locks/pypi_release_hashes.json
```

matrix (`ubuntu/macos/windows x 3.10/3.11/3.12/3.13/3.14`) で hash install を維持するため、`pylock.toml` 更新時は  
`.github/locks/pypi_release_hashes.json` も同時更新してください。

Python support policy is documented in `docs/support_matrix.md`.

## Privacy Note

`.mdex/mdex_index.json` と `.mdex/mdex_index.db` には scan 対象ファイル由来の summary が含まれます。  
機微情報を含むファイルは `control/scan_config.json` の `exclude_patterns` で除外してください。

Telemetry is local and opt-in only. When enabled with `MDEX_TELEMETRY=1` or `.mdex/config.json` `telemetry: true`,
`mdex` appends redacted command events to `.mdex/telemetry.jsonl`. It does not send data over the network, and events do
not include raw task/query strings or absolute repo paths.

`scan` は local/secret 寄りのファイル（例: `.env*`, `*.local.md`, `*.local.json`, `*.local.jsonl`, `secrets.*`, `credentials.*`）を
デフォルトで除外します。特殊用途で `use_default_exclude_patterns: false` を指定して取り込む場合でも、
local/secret らしいファイルが index に入ると `warnings` に表示されます。
再 scan 時、現在の index に存在しない node の agent override は SQLite から削除されます。
`mdex doctor` は scan warnings、JSON/SQLite の生成時刻ズレ、orphan override、legacy artifact、
未解決 `links_to` target、`old/`・`archive/`・fixtures/evals/logs/dumps などの review path が index に入っている状態を検出します。

## Artifact Hygiene

公開 repo ではランタイム生成物を追跡しません。

- `.mdex/`
- `dist/`
- `outputs/`
- `tmp/`
- `*.db`, `*.sqlite`, `*.sqlite3`
- `*.db.lock`, `*.sqlite.lock`, `*.sqlite3.lock`, `*.json.lock`

`outputs/` は main repo index からは除外したままにしてください。
生成済み観測を検索したい場合は、別 lane として artifact index を作ります。

```bash
mdex scan-artifacts --root outputs --db .mdex/artifacts.db
mdex context "2026-07-08 audit attribution" --include repo,artifacts --actionable
```

artifact 結果は `metadata.kind`, `metadata.generated_at`, `freshness.age_days`, `freshness.stale` を含みます。
古い観測は削除されず、stale として明示されます。
`--include repo,artifacts` で artifact DB が不在または古い場合、`recommended_next_actions_v2` に
`mdex scan-artifacts --db .mdex/artifacts.db` が入ります。DB が存在する場合は
`multi_index.indexes.artifacts.artifacts_index_age` で artifact index 自体の freshness を確認できます。
`scan-artifacts` は scan 中に消えたファイル、壊れた JSON、サイズ上限超過を fatal error ではなく `warnings` に落とします。
`warning_summary` で警告件数を確認できます。
ライブ状態の `runtime_state/**` は artifact lane からデフォルト除外されます。
repo 外の root を使う場合は `.mdex/config.json` の object root で `id_prefix` を指定し、
`expose_source_root: false` にするとローカル絶対パスを出力に含めません。

## Quick Verification

```bash
mdex scan --root tests/fixtures/quality_repo --config tests/fixtures/quality_scan_config.json
mdex doctor --db .mdex/mdex_index.db
mdex start "root decision" --db .mdex/mdex_index.db --limit 5
mdex impact design/root.md --db .mdex/mdex_index.db
mdex finish --task "root fix" --db .mdex/mdex_index.db --dry-run
python -m pytest -q
```

## License

`mdex` is licensed under Apache-2.0.

- License text: [LICENSE](LICENSE)
- Attribution notices: [NOTICE](NOTICE)
