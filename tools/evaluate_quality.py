#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mdex.evaluation import (  # noqa: E402
    EvaluationError,
    baseline_from_report,
    evaluate_fixture,
    write_json_atomic,
)


DEFAULT_FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "quality_repo"
DEFAULT_SCAN_CONFIG = PROJECT_ROOT / "tests" / "fixtures" / "quality_scan_config.json"
DEFAULT_GOLD_SET = PROJECT_ROOT / "evals" / "quality_retrieval_gold.json"
DEFAULT_BASELINE = PROJECT_ROOT / "evals" / "quality_retrieval_baseline.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate mdex retrieval quality and latency against manual golden judgments."
    )
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURE_ROOT)
    parser.add_argument("--scan-config", type=Path, default=DEFAULT_SCAN_CONFIG)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_SET)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--repeats", type=int, default=25)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument(
        "--enforce-latency",
        action="store_true",
        help="Fail when both committed latency guardrails are exceeded; use only in a pinned environment.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Replace the baseline with the current measurement after intentional, reviewed changes.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.update_baseline and args.enforce_latency:
        print(
            json.dumps(
                {"status": "error", "error": "--update-baseline and --enforce-latency are mutually exclusive"},
                ensure_ascii=False,
                allow_nan=False,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        report = evaluate_fixture(
            args.fixture_root,
            args.scan_config,
            args.gold,
            baseline_path=None if args.update_baseline else args.baseline,
            repeats=args.repeats,
            warmup=args.warmup,
            enforce_latency=bool(args.enforce_latency),
        )
        if args.update_baseline:
            baseline = baseline_from_report(report)
            write_json_atomic(baseline, args.baseline)
            payload: dict[str, object] = {
                "status": "baseline_updated",
                "baseline": str(args.baseline),
                "measurement": report,
            }
        else:
            payload = report
    except EvaluationError as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                allow_nan=False,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )
    if args.update_baseline:
        return 0
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
