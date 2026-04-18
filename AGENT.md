# mdex Agent Rules

`AGENT.md` は実行時の判断規則だけを置くファイルです。  
`README.md` は workflow contract の短縮版で、このファイルが execution heuristics の正本です。  
役割説明や詳細仕様は `README.md` / `docs/design.md` / `docs/convention.md` を読んでください。

## If-Then Rules

| if | then | why |
|---|---|---|
| repo を初めて触る | `mdex scan` の後に必ず `mdex start` | 入口を推測しない |
| 索引の新しさが怪しい | 先に `mdex scan` | 読む順序の誤判定を減らす |
| 作業を始める | `mdex start "<task>"` | `recommended_read_order` と `recommended_next_actions` を得る |
| `start` より広い入口が欲しい | `mdex context "<task>" --actionable` | 入口情報を直接深掘りする |
| 特定文書から読む順を決めたい | `mdex first <node-id>` | node 起点の read order を得る |
| 関連文書を掘りたい | `mdex related <node-id>` | 近接文脈を確認する |
| changed files がある | `mdex impact <path...>` または `mdex impact --changed-files-from-git` | `read_first` / `related_tasks` / `decision_records` を得る |
| タスクを閉じる | `mdex finish --task "<task>" --dry-run` | 更新候補を先に確認する |
| summary を適用する | `mdex finish --task "<task>" --summary-file <path> --scan` | apply と再 scan を一連で行う |
| task / decision を新規作成する | `mdex new task|decision` | frontmatter を手で作らない |
| `updated` だけ直したい | `mdex stamp <node-id or path>` | metadata 更新だけに留める |

## Priority When Unsure

1. `mdex scan`
2. `mdex start`
3. changed files があるなら `mdex impact`
4. 終了前に `mdex finish --dry-run`

## Prohibitions / Discouraged

- `mdex` を全文検索やコード理解の完全代替として扱わない
- prose 出力を期待しない。JSON field を読む
- `finish --summary-file` を summary なしで実行しない
- 通常の反映で `enrich` を優先しない。まず `finish` を使う
- `recommended_read_order` などの契約名に別名を付けない

## Contract Reminders

- 成功出力は stdout JSON、失敗出力は stderr JSON
- `finish --dry-run` は DB を更新しない
- `--db` 省略時の優先順は `README.md` / `docs/design.md` の記載に従う

## Verification

```bash
python -m pytest -q
```
