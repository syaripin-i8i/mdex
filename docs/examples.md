---
type: reference
project: mdex
status: active
updated: 2026-04-22
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
