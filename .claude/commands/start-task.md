# Start Task

作業開始時は以下を実行し、返却 JSON の `recommended_read_order` と `recommended_next_actions` に従って着手する。

```bash
mdex start "<task>" [--db <db>] [--budget 4000] [--limit 10]
```

最低限確認するキー:

- `recommended_read_order`
- `recommended_next_actions`
- `confidence`
- `why_this_set`
