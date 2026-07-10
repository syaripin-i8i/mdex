from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import mdex.evaluation as evaluation_module
from mdex.evaluation import (
    EvaluationError,
    baseline_from_report,
    case_metrics,
    compare_with_baseline,
    corpus_sha256,
    evaluate_fixture,
    load_baseline,
    load_gold_set,
    write_json_atomic,
)
from mdex.scanner import list_indexable_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "quality_repo"
SCAN_CONFIG = PROJECT_ROOT / "tests" / "fixtures" / "quality_scan_config.json"
GOLD_SET = PROJECT_ROOT / "evals" / "quality_retrieval_gold.json"
BASELINE = PROJECT_ROOT / "evals" / "quality_retrieval_baseline.json"


def _uncompared_report() -> dict[str, object]:
    return evaluate_fixture(
        FIXTURE_ROOT,
        SCAN_CONFIG,
        GOLD_SET,
        repeats=2,
        warmup=0,
    )


def test_case_metrics_use_standard_k_denominator() -> None:
    metrics = case_metrics(["relevant", "wrong"], ["relevant", "missing"], 3)

    assert metrics == {
        "recall_at_k": 0.5,
        "precision_at_k": pytest.approx(1 / 3),
        "mrr": 1.0,
    }

    duplicate_metrics = case_metrics(["relevant", "relevant"], ["relevant", "missing"], 3)
    assert duplicate_metrics["recall_at_k"] == 0.5
    assert duplicate_metrics["precision_at_k"] == pytest.approx(1 / 3)


def test_quality_fixture_has_manual_exhaustive_gold_and_expected_metrics() -> None:
    report = _uncompared_report()

    assert report["status"] == "uncompared"
    assert report["case_count"] == 10
    assert report["k"] == 3
    quality = report["quality"]
    assert isinstance(quality, dict)
    assert quality["recall_at_k"] == 1.0
    assert quality["precision_at_k"] == 0.633333
    assert quality["mrr"] == 1.0
    assert quality["by_operation"] == {
        "context": {"recall_at_k": 1.0, "precision_at_k": 0.666667, "mrr": 1.0},
        "find": {"recall_at_k": 1.0, "precision_at_k": 0.416667, "mrr": 1.0},
        "first": {"recall_at_k": 1.0, "precision_at_k": 0.666667, "mrr": 1.0},
        "related": {"recall_at_k": 1.0, "precision_at_k": 1.0, "mrr": 1.0},
    }
    latency = report["latency_ms"]
    assert isinstance(latency, dict)
    assert latency["scope"] == "retrieval_only_index_build_excluded"
    assert latency["samples"] == 20
    assert float(latency["p50"]) >= 0.0
    assert float(latency["p95"]) >= float(latency["p50"])


def test_evaluator_rejects_incomplete_manual_judgments(tmp_path: Path) -> None:
    payload = json.loads(GOLD_SET.read_text(encoding="utf-8"))
    payload["cases"][0]["irrelevant_ids"].pop()
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationError, match="judgments are not exhaustive"):
        evaluate_fixture(FIXTURE_ROOT, SCAN_CONFIG, incomplete, repeats=1, warmup=0)


def test_gold_set_requires_every_supported_operation(tmp_path: Path) -> None:
    payload = json.loads(GOLD_SET.read_text(encoding="utf-8"))
    payload["cases"] = [case for case in payload["cases"] if case["operation"] != "related"]
    incomplete = tmp_path / "missing-related.json"
    incomplete.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationError, match="missing: related"):
        load_gold_set(incomplete)


def test_evaluator_rejects_scan_config_typo_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_config = tmp_path / "quality_scan_config.json"
    invalid_config.write_text('{"include_extensons": [".md"]}', encoding="utf-8")
    monkeypatch.setattr(
        evaluation_module,
        "build_index",
        lambda *_args, **_kwargs: pytest.fail("build must not run for invalid config"),
    )

    with pytest.raises(EvaluationError, match="invalid scan config"):
        evaluate_fixture(FIXTURE_ROOT, invalid_config, GOLD_SET, repeats=1, warmup=0)


def test_evaluator_rejects_nonfinite_scan_config_number(tmp_path: Path) -> None:
    invalid_config = tmp_path / "quality_scan_config.json"
    invalid_config.write_text(
        '{"context_scoring": {"keyword": {"title": 1e999}}}',
        encoding="utf-8",
    )

    with pytest.raises(EvaluationError, match="must be finite"):
        evaluate_fixture(FIXTURE_ROOT, invalid_config, GOLD_SET, repeats=1, warmup=0)


def test_context_evaluation_uses_scan_scoring_for_quality_warmup_and_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_report = _uncompared_report()
    config = json.loads(SCAN_CONFIG.read_text(encoding="utf-8"))
    config["context_scoring"] = {
        "graph_boost_by_edge_type": {
            "depends_on": 20.0,
            "links_to": 20.0,
            "relates_to": 20.0,
        }
    }
    custom_config = tmp_path / "quality_scan_config.json"
    custom_config.write_text(json.dumps(config), encoding="utf-8")

    original_select_context = evaluation_module.select_context
    calls: list[tuple[dict[str, object], str]] = []

    def tracked_select_context(*args: object, **kwargs: object) -> dict[str, object]:
        scoring = kwargs.get("scoring_config")
        assert isinstance(scoring, dict)
        source = kwargs.get("scoring_config_source")
        assert isinstance(source, str)
        calls.append((scoring, source))
        return original_select_context(*args, **kwargs)

    monkeypatch.setattr(evaluation_module, "select_context", tracked_select_context)
    custom_report = evaluate_fixture(
        FIXTURE_ROOT,
        custom_config,
        GOLD_SET,
        repeats=2,
        warmup=1,
    )

    default_cases = {case["id"]: case for case in default_report["cases"]}
    custom_cases = {case["id"]: case for case in custom_report["cases"]}
    assert default_cases["context-root-graph-budget"]["retrieved_ids"] == [
        "design/root.md",
        "spec/b.md",
    ]
    assert custom_cases["context-root-graph-budget"]["retrieved_ids"] == [
        "spec/b.md",
        "tasks/pending/T20260101000001.md",
        "decision/a.md",
    ]
    assert custom_report["context_scoring_source"] == "scan_config"
    assert len(calls) == 2 * (1 + 1 + 2)
    assert all(source == "scan_config" for _scoring, source in calls)
    assert all(
        scoring["graph_boost_by_edge_type"]["depends_on"] == 20.0
        for scoring, _source in calls
    )


def test_quality_baseline_detects_metric_regression() -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)
    regressed = copy.deepcopy(report)
    regressed["quality"]["precision_at_k"] = 0.5

    comparison = compare_with_baseline(
        regressed,
        baseline,
        corpus_hash=str(report["corpus_sha256"]),
    )

    assert comparison["status"] == "fail"
    assert comparison["quality"]["status"] == "fail"
    assert comparison["quality"]["regressions"] == [
        {"metric": "precision_at_k", "current": 0.5, "baseline": 0.633333}
    ]
    observation = next(
        item
        for item in comparison["quality"]["observations"]
        if item["metric"] == "precision_at_k"
    )
    assert observation == {
        "metric": "precision_at_k",
        "current": 0.5,
        "baseline": 0.633333,
        "delta": -0.133333,
        "regression": True,
    }


def test_quality_baseline_detects_case_regression_without_aggregate_change() -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)
    regressed = copy.deepcopy(report)
    regressed["cases"][0]["precision_at_k"] = 0.333333

    comparison = compare_with_baseline(
        regressed,
        baseline,
        corpus_hash=str(report["corpus_sha256"]),
    )

    assert comparison["status"] == "fail"
    assert comparison["quality"]["regressions"] == [
        {
            "metric": "case:context-root-graph-budget.precision_at_k",
            "current": 0.333333,
            "baseline": 0.666667,
        }
    ]


def test_quality_baseline_rejects_stale_corpus_identity() -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)

    with pytest.raises(EvaluationError, match="baseline is stale"):
        compare_with_baseline(report, baseline, corpus_hash="different")


def test_quality_comparison_reports_every_metric_when_passing() -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)

    comparison = compare_with_baseline(
        report,
        baseline,
        corpus_hash=str(report["corpus_sha256"]),
    )

    observations = comparison["quality"]["observations"]
    assert len(observations) == 45
    assert all(set(item) == {"metric", "current", "baseline", "delta", "regression"} for item in observations)
    assert all(item["delta"] == 0.0 and item["regression"] is False for item in observations)
    assert {item["metric"] for item in observations} >= {
        "precision_at_k",
        "context.precision_at_k",
        "case:context-root-graph-budget.precision_at_k",
    }


def test_nan_allowed_drop_cannot_hide_quality_regression() -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)
    baseline["quality_policy"]["allowed_baseline_drop"] = math.nan
    report["quality"]["precision_at_k"] = 0.0

    with pytest.raises(EvaluationError, match="allowed_baseline_drop must be a finite number"):
        compare_with_baseline(
            report,
            baseline,
            corpus_hash=str(report["corpus_sha256"]),
        )


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, -0.01, 1.01])
def test_quality_values_must_be_finite_unit_interval(invalid: float) -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)
    baseline["quality"]["recall_at_k"] = invalid

    with pytest.raises(EvaluationError, match="quality metric recall_at_k"):
        compare_with_baseline(
            report,
            baseline,
            corpus_hash=str(report["corpus_sha256"]),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda baseline: baseline["latency_ms"].update({"p95": math.nan}), "latency_ms.p95"),
        (
            lambda baseline: baseline["latency_policy"].update({"regression_ratio": math.inf}),
            "latency regression_ratio",
        ),
        (
            lambda baseline: baseline["latency_policy"].update({"minimum_delta_ms": -1.0}),
            "latency minimum_delta_ms",
        ),
    ],
)
def test_latency_and_policy_values_are_finite_and_nonnegative(
    mutation: object,
    message: str,
) -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)
    mutation(baseline)

    with pytest.raises(EvaluationError, match=message):
        compare_with_baseline(
            report,
            baseline,
            corpus_hash=str(report["corpus_sha256"]),
        )


def test_latency_guardrail_is_report_only_unless_explicitly_enforced() -> None:
    report = _uncompared_report()
    baseline = baseline_from_report(report)
    for payload in (report["latency_ms"],):
        payload["p95"] = 20.0
        for operation in payload["by_operation"].values():
            operation["p95"] = 20.0
    baseline["latency_ms"]["p95"] = 1.0
    for operation in baseline["latency_ms"]["by_operation"].values():
        operation["p95"] = 1.0

    observed = compare_with_baseline(
        report,
        baseline,
        corpus_hash=str(report["corpus_sha256"]),
        enforce_latency=False,
    )
    enforced = compare_with_baseline(
        report,
        baseline,
        corpus_hash=str(report["corpus_sha256"]),
        enforce_latency=True,
    )

    assert observed["status"] == "pass"
    assert observed["latency"]["status"] == "possible_regression"
    assert observed["latency"]["mode"] == "report_only"
    assert enforced["status"] == "fail"
    assert enforced["latency"]["mode"] == "enforced_guardrail"


def test_committed_baseline_matches_current_quality() -> None:
    report = evaluate_fixture(
        FIXTURE_ROOT,
        SCAN_CONFIG,
        GOLD_SET,
        baseline_path=BASELINE,
        repeats=2,
        warmup=0,
    )

    assert report["status"] == "pass"
    assert report["baseline_comparison"]["quality"]["status"] == "pass"
    assert report["baseline_comparison"]["latency"]["mode"] == "report_only"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
@pytest.mark.parametrize("loader", [load_gold_set, load_baseline])
def test_evaluation_json_loaders_reject_nonstandard_constants(
    constant: str,
    loader: object,
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.json"
    source.write_text(f'{{"version": 1, "value": {constant}}}', encoding="utf-8")

    with pytest.raises(EvaluationError, match="invalid JSON"):
        loader(source)


@pytest.mark.parametrize("newline", [b"\r\n", b"\r"])
def test_corpus_hash_normalizes_text_newlines(newline: bytes, tmp_path: Path) -> None:
    fixture_copy = tmp_path / "quality_repo"
    shutil.copytree(FIXTURE_ROOT, fixture_copy)
    for source in fixture_copy.rglob("*"):
        if source.is_file():
            source.write_bytes(source.read_bytes().replace(b"\n", newline))

    config_copy = tmp_path / "quality_scan_config.json"
    config_copy.write_bytes(SCAN_CONFIG.read_bytes().replace(b"\n", newline))
    gold_copy = tmp_path / "quality_retrieval_gold.json"
    gold_copy.write_bytes(GOLD_SET.read_bytes().replace(b"\n", newline))

    assert corpus_sha256(fixture_copy, config_copy, gold_copy) == corpus_sha256(
        FIXTURE_ROOT,
        SCAN_CONFIG,
        GOLD_SET,
    )


def test_quality_metrics_and_rankings_are_deterministic() -> None:
    first = _uncompared_report()
    second = _uncompared_report()

    assert first["corpus_sha256"] == second["corpus_sha256"]
    assert first["quality"] == second["quality"]
    assert first["cases"] == second["cases"]


def test_eval_artifacts_are_excluded_from_main_index() -> None:
    config = json.loads((PROJECT_ROOT / "control" / "scan_config.json").read_text(encoding="utf-8"))
    indexed_json = list_indexable_files(
        PROJECT_ROOT,
        include_extensions=[".json"],
        exclude_patterns=config["exclude_patterns"],
    )

    assert GOLD_SET.resolve() not in indexed_json
    assert BASELINE.resolve() not in indexed_json


def test_quality_evaluator_command_reports_json() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "tools/evaluate_quality.py",
            "--repeats",
            "2",
            "--warmup",
            "0",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["quality"]["precision_at_k"] == 0.633333
    assert payload["baseline_comparison"]["quality"]["status"] == "pass"


def test_baseline_writer_is_atomic_and_round_trips(tmp_path: Path) -> None:
    payload = baseline_from_report(_uncompared_report())
    output = tmp_path / "baseline.json"

    write_json_atomic(payload, output)

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.iterdir()) == [output]
    assert load_gold_set(GOLD_SET)["k"] == 3


def test_baseline_writer_rejects_nonfinite_json(tmp_path: Path) -> None:
    output = tmp_path / "baseline.json"

    with pytest.raises(ValueError, match="Out of range float values"):
        write_json_atomic({"invalid": math.nan}, output)

    assert not output.exists()
