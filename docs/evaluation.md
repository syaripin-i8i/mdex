---
type: reference
project: mdex
status: active
updated: 2026-07-10
---

# Golden relevance and latency evaluation

This evaluation is the adoption gate for further Discovery Lane proposals. It is deliberately small: it protects the behavior demonstrated by the existing six-document quality fixture, but it is not evidence of broad production relevance.

## Independent judgments

`evals/quality_retrieval_gold.json` contains ten manually curated cases:

- two `context` queries that exercise keyword ranking, resolved-graph expansion, and budget selection together;
- four `find` queries;
- two `first` anchors;
- two `related` anchors.

Every case marks every eligible fixture node as either relevant or irrelevant. The evaluator rejects missing, overlapping, duplicate, or out-of-fixture judgments. The judgments are maintained by reading the fixture documents; they must never be generated from current ranking output.

The gold set must contain at least one case for each supported operation (`context`, `find`, `first`, and `related`). Evaluation JSON rejects non-standard `NaN` and infinity values, and baseline quality and latency numbers are range-checked before comparison or update.

The `context` judgments describe the best compact set for the stated budget, not every document that is topically adjacent. Each case is deliberately sized so a lexical entrypoint and a graph-expanded neighbor fit while lower-priority or larger candidates are deferred. `start` is not measured separately because it delegates retrieval and budget selection to `select_context`, then wraps that same result with workflow/index-status guidance. Duplicating the cases through `start` would count identical ranking behavior twice.

Context evaluation resolves `context_scoring` and synonyms from the validated quality scan config through the same resolver used by CLI execution. The resolved values and source are reused unchanged for the quality pass, warmups, and every timed call.

All cases use `k=3`. The report contains per-case results plus macro averages across all cases and each operation:

- `Recall@k = relevant retrieved / all relevant`;
- `Precision@k = relevant retrieved / k`, including when fewer than `k` candidates are returned;
- `MRR = 1 / rank of the first relevant result`, or zero when none is retrieved.

The committed quality baseline permits no metric decrease. Independent minimums also make the acceptance bar explicit: overall Recall@3 and MRR must remain 1.0, overall Precision@3 must remain at least 0.60, and operation-specific minimums are stored in the baseline. The comparison always reports `current`, `baseline`, `delta`, and `regression` for every aggregate, operation, and case metric, including passing metrics.

## Latency policy

Latency covers retrieval calls only; fixture scan and SQLite construction happen before timing. The runner performs warmups, then reports p50/p95 for all calls and for each operation.

Wall-clock timings vary across machines, so the default comparison is report-only and cannot fail CI. A pinned benchmark environment may pass `--enforce-latency`; even then, a regression requires both conditions:

- current p95 is more than 3x the committed p95; and
- current p95 is more than 5 ms slower.

This two-part guardrail avoids failing on large ratios between sub-millisecond measurements. The baseline is a regression reference, not an SLA.

## Commands

Run the review gate from the repository root:

```bash
python tools/evaluate_quality.py
```

Use a longer report-only sample when investigating ranker changes:

```bash
python tools/evaluate_quality.py --warmup 10 --repeats 100
```

Only a stable, pinned benchmark job should enforce latency:

```bash
python tools/evaluate_quality.py --warmup 10 --repeats 100 --enforce-latency
```

After an intentional fixture, judgment, or accepted ranker change, review case-level results first and then explicitly refresh the baseline:

```bash
python tools/evaluate_quality.py --warmup 10 --repeats 100 --update-baseline
git diff -- evals/quality_retrieval_baseline.json
```

Never update the baseline merely to make a regression pass. Changed judgments require a separate human review of the fixture and rationale.

## Index hygiene

The gold set and baseline live under `evals/`. `control/scan_config.json` excludes `**/evals/**`, so evaluation expectations and measurements cannot become retrieval candidates in the main repository index. The runner creates its SQLite database in a temporary directory and removes it after the run.

The corpus identity covers the fixture, scan config, and gold set. Text line endings are normalized from CRLF or CR to LF before hashing so the same reviewed corpus has the same identity on every supported platform.
