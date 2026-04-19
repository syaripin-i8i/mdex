# mdex
[![CI](https://github.com/syaripin-i8i/mdex/actions/workflows/ci.yml/badge.svg)](https://github.com/syaripin-i8i/mdex/actions/workflows/ci.yml)

**`mdex` は AI エージェントのための protocol-first CLI です。人間向けの閲覧 UX は優先しません。**  
**想定する公開先は「誰でも触る OSS」ではなく、分かる相手が AI に読ませて使う repo です。**
CI は `ubuntu-latest` を primary、`macos-latest` / `windows-latest` を best-effort として matrix 検証します。

## For Agents

- standard flow: `scan -> start -> (context | first | related | impact) -> finish --dry-run`
- apply summaries only with `finish --summary-file <path> --scan`
- success JSON goes to stdout; error JSON goes to stderr
- read next: `README.md` -> `AGENT.md` -> `docs/design.md` -> `docs/convention.md`
- read order is not the same thing as source of truth; canonical scope is defined below

## Protocol

これは紹介文ではなく、AI が最初に従う標準手順です。

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

`scan` の既定出力は `.mdex/mdex_index.db` と `.mdex/mdex_index.json` です。  
`--db` / `--output` を指定すると上書きできます。

## Assumptions

- `mdex` は規約のある Markdown / JSON 運用で最も強く効きます
- frontmatter の `type`, `status`, `updated` を推奨します
- `depends_on` と `relates_to` を使うと `start` / `context` / `impact` の解像度が上がります
- 先頭 summary があると `start` / `context` / `finish` が安定します
- 新規ノートは `mdex new`、`updated` 更新は `mdex stamp` を優先してください
- 規約が薄くても `scan` / `query` は動きますが、`start` / `impact` / `finish` の精度は下がります

> mdex is only as good as your note discipline.  
> For stable results, follow `docs/convention.md`.

## Non-goals

- 全文検索の代替ではありません
- source code understanding の完全代替ではありません
- ノート群を魔法のように整理するツールではありません
- 人間向け閲覧 UX を主目的にしません
- 規約の薄い repo で高精度を保証しません
- 汎用ナレッジベースではなく、agent workflow を圧縮するための CLI です

## 保護者向けメモ

`mdex` は、使うほど自動で賢くなる自己進化ツールではありません。  
担保するのは、AI エージェントの作業開始と作業終了の導線を一定のプロトコルで揃えることです。

このツールが担保するもの:

- `scan -> start -> ... -> finish` の標準手順
- JSON ベースの入出力契約
- 読む順序、関連文書、更新候補の算出
- 入力規約が整っているほど精度が上がる、という性質の明示

このツールが担保しないもの:

- 記録文化そのものの改善
- summary や frontmatter を人間/AI が継続して整備すること
- `depends_on` / `relates_to` の運用定着
- 使うほど自動で知識整理が進むこと
- 規約の薄い repo で高精度を保証すること

言い換えると、`mdex` は良い記録運用がある環境で、その資産を AI が使いやすくするための CLI です。  
「勝手に強くなる」ように見える場合があるとしても、それは `mdex` 自体が進化したのではなく、利用側の記録資産が蓄積され、その結果として `start` / `impact` / `finish` の効きが上がっただけです。

したがって、`mdex` の責務は次に限定します。

- 良い入力が来たときに安定して返す
- 入力規約への依存を隠さない
- 規約が薄いときは過剰な期待を持たせない
- agent workflow の入口と出口を壊さない

`mdex` はワークフローを整える道具であって、運用文化そのものを代替するものではありません。

## Command Selection Rules

短縮版の判断表です。実行時の詳細な判断規則は `AGENT.md` を正本としてください。

| situation | use | contract |
|---|---|---|
| start a task | `mdex start` | 入口を決める |
| need a wider actionable entrance | `mdex context --actionable` | `start` を直接深掘りする |
| inspect from a node | `mdex first` / `mdex related` | 特定文書から読む順と周辺文脈を見る |
| open a node body | `mdex open <node-id>` | indexed node id のみ。絶対パスと `..` は拒否 |
| changed files already exist | `mdex impact` | 影響範囲を changed files 起点で見る |
| close a task | `mdex finish --dry-run` | 出口を先に確認する |
| apply a summary | `mdex finish --summary-file ... --scan` | summary が実在するときだけ反映する |
| update `updated` metadata | `mdex stamp <node-id>` | indexed node id のみ。scan_roots の包含外は拒否 |

## 再現サンプル（fixtures/quality_repo）

この節は protocol の補助資料です。`tests/fixtures/quality_repo` で `scan -> start -> impact -> finish` を再現できます。

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
  "recommended_read_order": [
    { "id": "spec/b.md" },
    { "id": "decision/a.md" },
    { "id": "design/root.md" }
  ],
  "recommended_next_actions": [
    "open spec/b.md",
    "open decision/a.md",
    "search code for root decision"
  ]
}
```

```json
{
  "inputs": ["design/root.md"],
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

```json
{
  "task": "root fix",
  "dry_run": true,
  "changed_files": [],
  "enrich_candidates": [],
  "requires_manual_targeting": false
}
```

## 全出力は JSON

### Output Contract

すべての成功出力は stdout JSON、失敗出力は stderr JSON です。  
field 名は prose より強い契約として扱ってください。別名は導入しません。

| command | primary keys |
|---|---|
| `scan` | `nodes`, `edges.total`, `edges.resolved`, `edges.unresolved`, `edges.resolution_rate` |
| `start` | `task`, `recommended_read_order`, `recommended_next_actions`, `confidence` |
| `impact` | `inputs`, `read_first`, `related_tasks`, `decision_records`, `stale_watch` |
| `finish` | `task`, `dry_run`, `changed_files`, `enrich_candidates`, `requires_manual_targeting` |
| db resolution error | `error`, `resolution_attempts` |

```json
{ "error": "db not found", "resolution_attempts": [] }
```

人間向け整形が必要な場合だけ `--format table` を使ってください。

### Schema Contracts

機械可読な契約は `schemas/` に固定しています。

- `schemas/scan.schema.json`
- `schemas/start.schema.json`
- `schemas/context.schema.json`
- `schemas/impact.schema.json`
- `schemas/finish.schema.json`
- `schemas/error.schema.json`

版運用は `docs/schema_versioning.md` を参照してください。

## DB Resolution

`--db` 省略時は以下の優先順で自動解決されます。

1. CLI 引数 `--db`
2. 環境変数 `MDEX_DB`
3. `.mdex/config.json` の `db`
4. `repo/.mdex/mdex_index.db`
5. `repo/mdex_index.db`

## Public Scan Config

公開向け既定 config は `control/scan_config.json` です。

- `scan_roots` は `"."`（repo root 前提）
- `output_file` は `.mdex/mdex_index.json`
- `.mdex/**` は scan 対象から除外

実行例:

```bash
mdex scan --root . --config control/scan_config.json
```

`.mdex/` 配下に JSON/SQLite がまとまって出力されます。

## Source of Truth

read order と source of truth は同義ではありません。  
上から読む順は `For Agents`、どの文書を正本として扱うかはこの節で固定します。

| scope | source |
|---|---|
| workflow contract | `README.md` |
| execution heuristics | `AGENT.md` |
| architecture / persistence / schema | `docs/design.md` |
| input note contract | `docs/convention.md` |
| schema versioning policy | `docs/schema_versioning.md` |

`docs/archive/phase_a_agent_flow.md` は historical planning doc であり、入口契約の正本ではありません。

## License

`mdex` is licensed under Apache-2.0.

- License text: [LICENSE](LICENSE)
- Attribution notices: [NOTICE](NOTICE)

## Project Operations

- Security policy: [SECURITY.md](SECURITY.md)
- Contributing guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Support matrix: [docs/support_matrix.md](docs/support_matrix.md)
- Release process: [docs/release_process.md](docs/release_process.md)

## Setup

```bash
python -m pip install mdex-cli
python -m pip install -e .
python -m pip install -e ".[dev]"
```

依存: Python 3.10+, PyYAML

`pip install mdex-cli` は PyPI 公開後の利用を想定した手順です。`pip install -e .` は開発用のローカル編集モードです。

ロック依存で開発環境を再現する場合:

```bash
python -m pip install --upgrade pip
python .github/scripts/install_from_pylock.py --lock pylock.toml --editable .
```

`pylock.toml` 更新:

```bash
python -m pip lock -e ".[dev]" -o pylock.toml
```

## Privacy Note

`scan` の生成物（`.mdex/mdex_index.json`, `.mdex/mdex_index.db`）には、scan 対象ファイルから抽出された summary が含まれます。  
chat log や secret を含むファイルを scan 対象に入れると、その内容が index 側にも要約として残る可能性があります。  
公開前に index を必ず確認し、必要に応じて `control/scan_config.json` の `exclude_patterns` で機微ディレクトリを除外してください。  
この repo では `.gitignore` で `.mdex/` を除外し、生成物の追跡を避けています。

## Artifact Hygiene

公開 repo ではランタイム生成物を追跡しません。

- `.mdex/`
- `outputs/`
- `tmp/`
- `*.db`, `*.sqlite`, `*.sqlite3`

## Quick Verification

```bash
mdex scan --root tests/fixtures/quality_repo --config control/scan_config.json
mdex start "root decision" --db .mdex/mdex_index.db --limit 5
mdex impact design/root.md --db .mdex/mdex_index.db
mdex finish --task "root fix" --db .mdex/mdex_index.db --dry-run
python -m pytest -q
```
