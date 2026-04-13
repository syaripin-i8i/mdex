# Finish Task

作業終了時は `finish` を実行して更新候補と後処理を整理する。

```bash
# まず dry-run
mdex finish --task "<task>" [--db <db>] --dry-run

# summary を適用する場合
mdex finish --task "<task>" [--db <db>] --summary-file <path> --scan
```

最低限確認するキー:

- `changed_files`
- `enrich_candidates`
- `applied_enrichments`
- `requires_manual_targeting`
