---
type: reference
project: mdex
status: active
updated: 2026-05-01
---

# mdex Examples

`README.md` の再現サンプルを補足する詳細例です。

## quality_repo walkthrough

```bash
mdex scan --root tests/fixtures/quality_repo --db .mdex/quality_example.db --output .mdex/quality_example.json
mdex start "root decision" --db .mdex/quality_example.db --limit 5
mdex context "root decision" --db .mdex/quality_example.db --actionable
mdex impact design/root.md --db .mdex/quality_example.db
mdex finish --task "root fix" --db .mdex/quality_example.db --dry-run
```

## finish dry-run interpretation

- `changed_files: []` は「変更検出なし」
- `enrich_candidates: []` は「更新候補なし」
- `requires_manual_targeting: false` は「手動ターゲット指定は不要」

上記は **失敗ではなく成功**。成功/失敗判定は `stdout/stderr` と exit code を基準に判断します。

## actionable_digest to rg

`mdex` は `rg` の代替ではなく、`rg` の前に入口を絞るために使います。

```bash
mdex context "reply guardrail runtime" --db .mdex/mdex_index.db --actionable
```

`actionable_digest` の見る順番:

1. `relevant_docs` で設計・運用ルールの入口を読む
2. `known_guardrails` で制約、注意、禁止事項を確認する
3. `likely_code_entrypoints` があれば実装・テスト候補を読む
4. `suggested_rg` の `command` と `args` で exact evidence を取りに行く
5. `context_gaps` が残っていれば index を広げるより先に `rg` や直接 read で補う

`suggested_rg.command` は `"rg"`、実引数は `args` が正本です。PowerShell など shell ごとの quote 差を避けるため、エージェントは文字列を再パースせず `args` 配列を使います。

```json
{
  "actionable_digest": {
    "intent": "reply guardrail runtime",
    "relevant_docs": [
      {
        "id": "docs/reply_policy.md",
        "title": "Reply Policy",
        "type": "design",
        "status": "active",
        "reason": "high lexical or graph score"
      }
    ],
    "relevant_task_history": [],
    "known_guardrails": [
      {
        "id": "docs/reply_policy.md",
        "title": "Reply Policy",
        "type": "design",
        "status": "active",
        "reason": "mentions 制約/注意/禁止"
      }
    ],
    "likely_code_entrypoints": [
      {
        "id": "runtime/reply_runtime.py",
        "title": "reply_runtime",
        "type": "unknown",
        "status": "unknown",
        "reason": "mentioned code entrypoint"
      },
      {
        "id": "tests/test_reply_guard.py",
        "title": "test_reply_guard",
        "type": "unknown",
        "status": "unknown",
        "reason": "mentioned test entrypoint"
      }
    ],
    "suggested_rg": [
      {
        "command": "rg",
        "args": ["-n", "reply|guardrail|runtime", "runtime", "tests", "docs"],
        "pattern": "reply|guardrail|runtime",
        "paths": ["runtime", "tests", "docs"],
        "reason": "expand from mdex entrypoint candidates into exact source matches"
      }
    ],
    "context_gaps": []
  }
}
```
