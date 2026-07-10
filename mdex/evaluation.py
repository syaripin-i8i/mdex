from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from mdex.builder import build_index
from mdex.context import resolve_context_scoring_config, select_context
from mdex.indexer import write_sqlite
from mdex.resolver import prerequisite_order, related_nodes
from mdex.scan_config import ScanConfigError, load_scan_config
from mdex.store import list_nodes, search_nodes


EVALUATION_VERSION = 1
SUPPORTED_OPERATIONS = {"context", "find", "first", "related"}
QUALITY_METRICS = ("recall_at_k", "precision_at_k", "mrr")
TEXT_INPUT_SUFFIXES = {".json", ".jsonl", ".md"}


class EvaluationError(ValueError):
    """Raised when evaluation inputs cannot produce a trustworthy result."""


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _finite_number(
    value: Any,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvaluationError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise EvaluationError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise EvaluationError(f"{label} must be a finite number")
    if minimum is not None:
        if exclusive_minimum and number <= minimum:
            raise EvaluationError(f"{label} must be greater than {minimum}")
        if not exclusive_minimum and number < minimum:
            raise EvaluationError(f"{label} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise EvaluationError(f"{label} must be at most {maximum}")
    return number


def _quality_number(value: Any, *, label: str) -> float:
    return _finite_number(value, label=label, minimum=0.0, maximum=1.0)


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EvaluationError(f"{label} must be a non-negative integer")
    return value


def _reject_nonfinite_json_values(value: Any, *, label: str, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise EvaluationError(f"invalid JSON in {label}: non-finite number at {path}")
    if isinstance(value, dict):
        for key, nested in value.items():
            _reject_nonfinite_json_values(
                nested,
                label=label,
                path=f"{path}.{key}",
            )
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_nonfinite_json_values(
                nested,
                label=label,
                path=f"{path}[{index}]",
            )


def _load_json_object(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_constant,
        )
    except OSError as exc:
        raise EvaluationError(f"cannot read {label}: {source}") from exc
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationError(f"invalid JSON in {label}: {source}") from exc
    if not isinstance(payload, dict):
        raise EvaluationError(f"{label} must be a JSON object: {source}")
    _reject_nonfinite_json_values(payload, label=f"{label}: {source}")
    return payload


def _clean_id_list(value: Any, *, field: str, case_id: str) -> list[str]:
    if not isinstance(value, list):
        raise EvaluationError(f"case {case_id}: {field} must be an array")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise EvaluationError(f"case {case_id}: {field} entries must be non-empty strings")
        node_id = item.strip()
        if node_id in seen:
            raise EvaluationError(f"case {case_id}: duplicate {field} entry: {node_id}")
        seen.add(node_id)
        cleaned.append(node_id)
    return cleaned


def load_gold_set(path: str | Path) -> dict[str, Any]:
    payload = _load_json_object(path, label="gold set")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != EVALUATION_VERSION:
        raise EvaluationError(
            f"unsupported gold set version: {version!r}; expected {EVALUATION_VERSION}"
        )
    if payload.get("curation") != "manual_exhaustive_judgments":
        raise EvaluationError("gold set must declare manual_exhaustive_judgments curation")
    k = payload.get("k")
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise EvaluationError("gold set k must be a positive integer")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvaluationError("gold set cases must be a non-empty array")

    cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise EvaluationError("each gold case must be an object")
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise EvaluationError("each gold case needs a non-empty string id")
        case_id = case_id.strip()
        if case_id in seen_case_ids:
            raise EvaluationError(f"duplicate gold case id: {case_id}")
        seen_case_ids.add(case_id)

        operation = raw_case.get("operation")
        if operation not in SUPPORTED_OPERATIONS:
            raise EvaluationError(f"case {case_id}: unsupported operation: {operation!r}")
        value = raw_case.get("input")
        if not isinstance(value, str) or not value.strip():
            raise EvaluationError(f"case {case_id}: input must be a non-empty string")
        budget: int | None = None
        if operation == "context":
            raw_budget = raw_case.get("budget")
            if not isinstance(raw_budget, int) or isinstance(raw_budget, bool) or raw_budget <= 0:
                raise EvaluationError(f"case {case_id}: context budget must be a positive integer")
            budget = raw_budget
        elif "budget" in raw_case:
            raise EvaluationError(f"case {case_id}: budget is only valid for context")

        relevant_ids = _clean_id_list(
            raw_case.get("relevant_ids"), field="relevant_ids", case_id=case_id
        )
        irrelevant_ids = _clean_id_list(
            raw_case.get("irrelevant_ids"), field="irrelevant_ids", case_id=case_id
        )
        if not relevant_ids:
            raise EvaluationError(f"case {case_id}: relevant_ids must not be empty")
        overlap = set(relevant_ids).intersection(irrelevant_ids)
        if overlap:
            raise EvaluationError(
                f"case {case_id}: relevance judgments overlap: {', '.join(sorted(overlap))}"
            )
        cases.append(
            {
                "id": case_id,
                "operation": operation,
                "input": value.strip(),
                "budget": budget,
                "relevant_ids": relevant_ids,
                "irrelevant_ids": irrelevant_ids,
                "rationale": str(raw_case.get("rationale", "")).strip(),
            }
        )

    present_operations = {str(case["operation"]) for case in cases}
    missing_operations = sorted(SUPPORTED_OPERATIONS.difference(present_operations))
    if missing_operations:
        raise EvaluationError(
            "gold set must contain every required operation; missing: "
            + ", ".join(missing_operations)
        )

    return {"version": EVALUATION_VERSION, "k": k, "cases": cases}


def corpus_sha256(
    fixture_root: str | Path,
    scan_config_path: str | Path,
    gold_path: str | Path,
) -> str:
    root = Path(fixture_root)
    config_path = Path(scan_config_path)
    gold_set_path = Path(gold_path)
    if not root.is_dir():
        raise EvaluationError(f"fixture root is not a directory: {root}")

    digest = hashlib.sha256()

    def add_file(label: str, source: Path, *, normalize_text: bool) -> None:
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise EvaluationError(f"cannot read evaluation input: {source}") from exc
        if normalize_text:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EvaluationError(f"evaluation text input is not UTF-8: {source}") from exc
            content = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")

    add_file("scan_config", config_path, normalize_text=True)
    add_file("gold_set", gold_set_path, normalize_text=True)
    for source in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        add_file(
            f"fixture/{source.relative_to(root).as_posix()}",
            source,
            normalize_text=source.suffix.lower() in TEXT_INPUT_SUFFIXES,
        )
    return digest.hexdigest()


def _validate_exhaustive_judgments(
    cases: list[dict[str, Any]], all_node_ids: set[str]
) -> None:
    for case in cases:
        case_id = str(case["id"])
        operation = str(case["operation"])
        input_value = str(case["input"])
        if operation in {"first", "related"} and input_value not in all_node_ids:
            raise EvaluationError(f"case {case_id}: anchor node is absent from fixture: {input_value}")
        eligible = set(all_node_ids)
        if operation in {"first", "related"}:
            eligible.discard(input_value)
        judged = set(case["relevant_ids"]).union(case["irrelevant_ids"])
        missing = eligible.difference(judged)
        extra = judged.difference(eligible)
        if missing or extra:
            detail: list[str] = []
            if missing:
                detail.append(f"unjudged={','.join(sorted(missing))}")
            if extra:
                detail.append(f"outside_fixture={','.join(sorted(extra))}")
            raise EvaluationError(f"case {case_id}: judgments are not exhaustive ({'; '.join(detail)})")


def _execute_case(
    case: dict[str, Any],
    db_path: str,
    k: int,
    *,
    context_scoring_config: dict[str, Any] | None,
    context_scoring_source: str,
) -> list[str]:
    operation = str(case["operation"])
    input_value = str(case["input"])
    if operation == "context":
        budget = case.get("budget")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise EvaluationError(f"case {case.get('id', '')}: invalid context budget")
        payload = select_context(
            input_value,
            db_path,
            budget=budget,
            limit=k,
            include_content=False,
            actionable=False,
            scoring_config=context_scoring_config,
            scoring_config_source=context_scoring_source,
        )
        raw_rows = payload.get("nodes", [])
        rows = raw_rows if isinstance(raw_rows, list) else []
    elif operation == "find":
        rows = search_nodes(db_path, input_value, limit=k)
    elif operation == "first":
        rows = prerequisite_order(input_value, db_path, limit=k)
    elif operation == "related":
        rows = related_nodes(input_value, db_path, limit=k)
    else:  # load_gold_set prevents this branch; keep API fail-closed.
        raise EvaluationError(f"unsupported evaluation operation: {operation}")
    return [str(row.get("id", "")).strip() for row in rows if str(row.get("id", "")).strip()]


def case_metrics(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> dict[str, float]:
    if k <= 0:
        raise EvaluationError("k must be positive")
    relevant = set(relevant_ids)
    if not relevant:
        raise EvaluationError("relevant_ids must not be empty")
    top_k = retrieved_ids[:k]
    relevant_retrieved = len(relevant.intersection(top_k))
    reciprocal_rank = 0.0
    for rank, node_id in enumerate(top_k, start=1):
        if node_id in relevant:
            reciprocal_rank = 1.0 / rank
            break
    return {
        "recall_at_k": relevant_retrieved / len(relevant),
        "precision_at_k": relevant_retrieved / k,
        "mrr": reciprocal_rank,
    }


def _mean_metrics(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {metric: 0.0 for metric in QUALITY_METRICS}
    return {
        metric: round(sum(float(row[metric]) for row in rows) / len(rows), 6)
        for metric in QUALITY_METRICS
    }


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _latency_summary(samples_ms: list[float]) -> dict[str, Any]:
    for sample in samples_ms:
        _finite_number(sample, label="latency sample", minimum=0.0)
    return {
        "samples": len(samples_ms),
        "p50": round(_percentile(samples_ms, 0.50), 6),
        "p95": round(_percentile(samples_ms, 0.95), 6),
    }


def run_retrieval_evaluation(
    db_path: str,
    gold_set: dict[str, Any],
    *,
    repeats: int = 25,
    warmup: int = 3,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
    context_scoring_config: dict[str, Any] | None = None,
    context_scoring_source: str = "defaults",
) -> dict[str, Any]:
    if repeats <= 0:
        raise EvaluationError("repeats must be positive")
    if warmup < 0:
        raise EvaluationError("warmup must not be negative")
    cases = gold_set.get("cases")
    k = gold_set.get("k")
    if not isinstance(cases, list) or not isinstance(k, int):
        raise EvaluationError("gold set must be loaded with load_gold_set")

    all_node_ids = {
        str(node.get("id", "")).strip()
        for node in list_nodes(db_path)
        if str(node.get("id", "")).strip()
    }
    _validate_exhaustive_judgments(cases, all_node_ids)

    case_reports: list[dict[str, Any]] = []
    quality_rows: list[dict[str, float]] = []
    quality_by_operation: dict[str, list[dict[str, float]]] = defaultdict(list)
    for case in cases:
        retrieved_ids = _execute_case(
            case,
            db_path,
            k,
            context_scoring_config=context_scoring_config,
            context_scoring_source=context_scoring_source,
        )
        outside_fixture = set(retrieved_ids).difference(all_node_ids)
        if outside_fixture:
            raise EvaluationError(
                f"case {case['id']}: retrieval returned nodes outside fixture: "
                f"{','.join(sorted(outside_fixture))}"
            )
        metrics = case_metrics(retrieved_ids, list(case["relevant_ids"]), k)
        quality_rows.append(metrics)
        quality_by_operation[str(case["operation"])].append(metrics)
        case_reports.append(
            {
                "id": case["id"],
                "operation": case["operation"],
                "input": case["input"],
                **({"budget": case["budget"]} if case["operation"] == "context" else {}),
                "retrieved_ids": retrieved_ids,
                "relevant_ids": list(case["relevant_ids"]),
                **{metric: round(float(metrics[metric]), 6) for metric in QUALITY_METRICS},
            }
        )

    for _ in range(warmup):
        for case in cases:
            _execute_case(
                case,
                db_path,
                k,
                context_scoring_config=context_scoring_config,
                context_scoring_source=context_scoring_source,
            )

    latency_samples: list[float] = []
    latency_by_operation: dict[str, list[float]] = defaultdict(list)
    for _ in range(repeats):
        for case in cases:
            start_ns = clock_ns()
            _execute_case(
                case,
                db_path,
                k,
                context_scoring_config=context_scoring_config,
                context_scoring_source=context_scoring_source,
            )
            elapsed_ms = max(0.0, (clock_ns() - start_ns) / 1_000_000.0)
            latency_samples.append(elapsed_ms)
            latency_by_operation[str(case["operation"])].append(elapsed_ms)

    quality = _mean_metrics(quality_rows)
    quality["by_operation"] = {
        operation: _mean_metrics(rows)
        for operation, rows in sorted(quality_by_operation.items())
    }
    latency = _latency_summary(latency_samples)
    latency.update(
        {
            "repeats": repeats,
            "warmup": warmup,
            "scope": "retrieval_only_index_build_excluded",
            "by_operation": {
                operation: _latency_summary(samples)
                for operation, samples in sorted(latency_by_operation.items())
            },
        }
    )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "k": k,
        "case_count": len(cases),
        "context_scoring_source": context_scoring_source,
        "quality": quality,
        "latency_ms": latency,
        "cases": case_reports,
    }


def load_baseline(path: str | Path) -> dict[str, Any]:
    baseline = _load_json_object(path, label="evaluation baseline")
    version = baseline.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != EVALUATION_VERSION:
        raise EvaluationError(
            f"unsupported baseline version: {version!r}; expected {EVALUATION_VERSION}"
        )
    _validate_baseline_payload(baseline)
    return baseline


def _quality_values(payload: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        raise EvaluationError("baseline/report quality must be an object")
    for metric in QUALITY_METRICS:
        value = quality.get(metric)
        values[metric] = _quality_number(value, label=f"quality metric {metric}")
    by_operation = quality.get("by_operation", {})
    if not isinstance(by_operation, dict):
        raise EvaluationError("quality.by_operation must be an object")
    for operation, operation_values in by_operation.items():
        if operation not in SUPPORTED_OPERATIONS or not isinstance(operation_values, dict):
            raise EvaluationError(f"invalid quality operation metrics: {operation}")
        for metric in QUALITY_METRICS:
            value = operation_values.get(metric)
            values[f"{operation}.{metric}"] = _quality_number(
                value, label=f"quality metric {operation}.{metric}"
            )
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise EvaluationError("baseline/report cases must be an array")
    seen_case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationError("baseline/report cases must contain objects")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_case_ids:
            raise EvaluationError(f"invalid or duplicate baseline/report case id: {case_id!r}")
        seen_case_ids.add(case_id)
        for metric in QUALITY_METRICS:
            value = case.get(metric)
            values[f"case:{case_id}.{metric}"] = _quality_number(
                value, label=f"case quality metric {case_id}.{metric}"
            )
    return values


def _minimum_values(policy: dict[str, Any]) -> dict[str, float]:
    minimum = policy.get("minimum", {})
    by_operation = policy.get("by_operation_minimum", {})
    if not isinstance(minimum, dict) or not isinstance(by_operation, dict):
        raise EvaluationError("quality policy minimum values must be objects")
    if set(minimum) != set(QUALITY_METRICS):
        raise EvaluationError("quality policy minimum must contain every quality metric")
    values: dict[str, float] = {}
    for metric, value in minimum.items():
        if metric not in QUALITY_METRICS:
            raise EvaluationError(f"invalid quality minimum: {metric}")
        values[metric] = _quality_number(value, label=f"quality minimum {metric}")
    for operation, operation_values in by_operation.items():
        if operation not in SUPPORTED_OPERATIONS or not isinstance(operation_values, dict):
            raise EvaluationError(f"invalid operation quality minimum: {operation}")
        if set(operation_values) != set(QUALITY_METRICS):
            raise EvaluationError(
                f"operation quality minimum must contain every metric: {operation}"
            )
        for metric, value in operation_values.items():
            if metric not in QUALITY_METRICS:
                raise EvaluationError(f"invalid operation quality minimum: {operation}.{metric}")
            values[f"{operation}.{metric}"] = _quality_number(
                value, label=f"quality minimum {operation}.{metric}"
            )
    return values


def _latency_p95_values(payload: dict[str, Any]) -> dict[str, float]:
    latency = payload.get("latency_ms")
    if not isinstance(latency, dict):
        raise EvaluationError("baseline/report latency_ms must be an object")
    values: dict[str, float] = {}
    p50 = _finite_number(latency.get("p50"), label="latency_ms.p50", minimum=0.0)
    p95 = _finite_number(latency.get("p95"), label="latency_ms.p95", minimum=0.0)
    if p50 > p95:
        raise EvaluationError("latency_ms.p50 must not exceed p95")
    _nonnegative_int(latency.get("samples"), label="latency_ms.samples")
    _nonnegative_int(latency.get("repeats"), label="latency_ms.repeats")
    _nonnegative_int(latency.get("warmup"), label="latency_ms.warmup")
    values["overall.p95"] = p95
    by_operation = latency.get("by_operation", {})
    if not isinstance(by_operation, dict):
        raise EvaluationError("latency_ms.by_operation must be an object")
    for operation, operation_values in by_operation.items():
        if operation not in SUPPORTED_OPERATIONS or not isinstance(operation_values, dict):
            raise EvaluationError(f"invalid latency operation metrics: {operation}")
        operation_p50 = _finite_number(
            operation_values.get("p50"),
            label=f"latency_ms.by_operation.{operation}.p50",
            minimum=0.0,
        )
        operation_p95 = _finite_number(
            operation_values.get("p95"),
            label=f"latency_ms.by_operation.{operation}.p95",
            minimum=0.0,
        )
        if operation_p50 > operation_p95:
            raise EvaluationError(f"latency p50 must not exceed p95: {operation}")
        _nonnegative_int(
            operation_values.get("samples"),
            label=f"latency_ms.by_operation.{operation}.samples",
        )
        values[f"{operation}.p95"] = operation_p95
    return values


def _quality_policy_values(baseline: dict[str, Any]) -> tuple[float, dict[str, float]]:
    quality_policy = baseline.get("quality_policy")
    if not isinstance(quality_policy, dict):
        raise EvaluationError("baseline quality_policy must be an object")
    allowed_drop = _finite_number(
        quality_policy.get("allowed_baseline_drop", 0.0),
        label="quality allowed_baseline_drop",
        minimum=0.0,
        maximum=1.0,
    )
    return allowed_drop, _minimum_values(quality_policy)


def _latency_policy_values(baseline: dict[str, Any]) -> tuple[float, float]:
    latency_policy = baseline.get("latency_policy")
    if not isinstance(latency_policy, dict):
        raise EvaluationError("baseline latency_policy must be an object")
    if latency_policy.get("mode") != "report_only":
        raise EvaluationError("committed latency policy must use report_only mode")
    ratio_limit = _finite_number(
        latency_policy.get("regression_ratio"),
        label="latency regression_ratio",
        minimum=1.0,
        exclusive_minimum=True,
    )
    delta_limit = _finite_number(
        latency_policy.get("minimum_delta_ms"),
        label="latency minimum_delta_ms",
        minimum=0.0,
    )
    return ratio_limit, delta_limit


def _validate_baseline_payload(baseline: dict[str, Any]) -> None:
    corpus_hash = baseline.get("corpus_sha256")
    if not isinstance(corpus_hash, str) or not corpus_hash.strip():
        raise EvaluationError("baseline corpus_sha256 must be a non-empty string")
    k = baseline.get("k")
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise EvaluationError("baseline k must be a positive integer")
    case_count = baseline.get("case_count")
    cases = baseline.get("cases")
    if (
        not isinstance(case_count, int)
        or isinstance(case_count, bool)
        or case_count <= 0
        or not isinstance(cases, list)
        or case_count != len(cases)
    ):
        raise EvaluationError("baseline case_count must match its non-empty cases array")
    quality_values = _quality_values(baseline)
    latency_values = _latency_p95_values(baseline)
    _allowed_drop, minimum_values = _quality_policy_values(baseline)
    _latency_policy_values(baseline)
    quality_operations = {
        key.split(".", 1)[0]
        for key in quality_values
        if "." in key and not key.startswith("case:")
    }
    latency_operations = {
        key.split(".", 1)[0]
        for key in latency_values
        if key != "overall.p95"
    }
    if quality_operations != latency_operations:
        raise EvaluationError("baseline quality and latency operation sets must match")
    minimum_operations = {
        key.split(".", 1)[0]
        for key in minimum_values
        if "." in key
    }
    if quality_operations != SUPPORTED_OPERATIONS or minimum_operations != quality_operations:
        raise EvaluationError(
            "baseline quality, latency, and minimums must contain every supported operation"
        )


def compare_with_baseline(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    corpus_hash: str,
    enforce_latency: bool = False,
) -> dict[str, Any]:
    _validate_baseline_payload(baseline)
    if baseline.get("corpus_sha256") != corpus_hash:
        raise EvaluationError("evaluation baseline is stale for the current fixture/config/gold set")
    if baseline.get("k") != report.get("k") or baseline.get("case_count") != report.get("case_count"):
        raise EvaluationError("evaluation baseline shape does not match current gold set")

    allowed_drop_value, minimum_quality = _quality_policy_values(baseline)

    current_quality = _quality_values(report)
    baseline_quality = _quality_values(baseline)
    if set(current_quality) != set(baseline_quality):
        missing = sorted(set(baseline_quality).difference(current_quality))
        extra = sorted(set(current_quality).difference(baseline_quality))
        raise EvaluationError(
            f"quality metric shape mismatch: missing={missing or []}, extra={extra or []}"
        )
    regressions: list[dict[str, Any]] = []
    quality_observations: list[dict[str, Any]] = []
    threshold_failures: list[dict[str, Any]] = []
    for metric, baseline_value in sorted(baseline_quality.items()):
        if metric not in current_quality:
            raise EvaluationError(f"current report is missing baseline quality metric: {metric}")
        current_value = current_quality[metric]
        delta = current_value - baseline_value
        regression = current_value + 1e-12 < baseline_value - allowed_drop_value
        quality_observations.append(
            {
                "metric": metric,
                "current": current_value,
                "baseline": baseline_value,
                "delta": round(delta, 6),
                "regression": regression,
            }
        )
        if regression:
            regressions.append(
                {"metric": metric, "current": current_value, "baseline": baseline_value}
            )
    for metric, minimum_value in sorted(minimum_quality.items()):
        if metric not in current_quality:
            raise EvaluationError(f"current report is missing threshold quality metric: {metric}")
        current_value = current_quality[metric]
        if current_value + 1e-12 < minimum_value:
            threshold_failures.append(
                {"metric": metric, "current": current_value, "minimum": minimum_value}
            )
    quality_status = "pass" if not regressions and not threshold_failures else "fail"

    ratio_limit, delta_limit = _latency_policy_values(baseline)

    current_latency = _latency_p95_values(report)
    baseline_latency = _latency_p95_values(baseline)
    latency_observations: list[dict[str, Any]] = []
    possible_regressions: list[str] = []
    for metric, baseline_value in sorted(baseline_latency.items()):
        if metric not in current_latency:
            raise EvaluationError(f"current report is missing baseline latency metric: {metric}")
        current_value = current_latency[metric]
        delta = current_value - baseline_value
        ratio = current_value / baseline_value if baseline_value > 0 else math.inf
        possible = ratio > ratio_limit and delta > delta_limit
        if possible:
            possible_regressions.append(metric)
        latency_observations.append(
            {
                "metric": metric,
                "current_ms": round(current_value, 6),
                "baseline_ms": round(baseline_value, 6),
                "ratio": round(ratio, 3) if math.isfinite(ratio) else None,
                "delta_ms": round(delta, 6),
                "possible_regression": possible,
            }
        )
    latency_status = "possible_regression" if possible_regressions else "within_guardrail"
    latency_failed = bool(enforce_latency and possible_regressions)
    status = "fail" if quality_status == "fail" or latency_failed else "pass"
    return {
        "status": status,
        "quality": {
            "status": quality_status,
            "allowed_baseline_drop": allowed_drop_value,
            "observations": quality_observations,
            "regressions": regressions,
            "threshold_failures": threshold_failures,
        },
        "latency": {
            "status": latency_status,
            "mode": "enforced_guardrail" if enforce_latency else "report_only",
            "regression_ratio": ratio_limit,
            "minimum_delta_ms": delta_limit,
            "observations": latency_observations,
        },
    }


def evaluate_fixture(
    fixture_root: str | Path,
    scan_config_path: str | Path,
    gold_path: str | Path,
    *,
    baseline_path: str | Path | None = None,
    repeats: int = 25,
    warmup: int = 3,
    enforce_latency: bool = False,
) -> dict[str, Any]:
    root = Path(fixture_root)
    config_path = Path(scan_config_path)
    gold_set_path = Path(gold_path)
    try:
        config = load_scan_config(config_path)
    except ScanConfigError as exc:
        raise EvaluationError(str(exc)) from exc
    _reject_nonfinite_json_values(config, label=f"quality scan config: {config_path}")
    gold_set = load_gold_set(gold_set_path)
    digest = corpus_sha256(root, config_path, gold_set_path)
    context_scoring_config, context_scoring_source = resolve_context_scoring_config(
        scan_config=config
    )

    with tempfile.TemporaryDirectory(prefix="mdex-eval-") as temp_dir:
        db_path = Path(temp_dir) / "quality.db"
        index = build_index(str(root), config)
        write_sqlite(index, str(db_path))
        report = run_retrieval_evaluation(
            str(db_path),
            gold_set,
            repeats=repeats,
            warmup=warmup,
            context_scoring_config=context_scoring_config,
            context_scoring_source=context_scoring_source,
        )

    report["corpus_sha256"] = digest
    if baseline_path is None:
        report["status"] = "uncompared"
        return report
    baseline = load_baseline(baseline_path)
    comparison = compare_with_baseline(
        report,
        baseline,
        corpus_hash=digest,
        enforce_latency=enforce_latency,
    )
    report["baseline_comparison"] = comparison
    report["status"] = comparison["status"]
    return report


def baseline_from_report(report: dict[str, Any]) -> dict[str, Any]:
    quality = report.get("quality")
    latency = report.get("latency_ms")
    cases = report.get("cases")
    corpus_hash = report.get("corpus_sha256")
    if (
        not isinstance(quality, dict)
        or not isinstance(latency, dict)
        or not isinstance(cases, list)
        or not isinstance(corpus_hash, str)
        or not corpus_hash
    ):
        raise EvaluationError("evaluation report is incomplete")
    _quality_values(report)
    _latency_p95_values(report)
    baseline_quality = {
        metric: quality[metric]
        for metric in QUALITY_METRICS
    }
    baseline_quality["by_operation"] = quality.get("by_operation", {})
    baseline_latency = {
        "p50": latency.get("p50"),
        "p95": latency.get("p95"),
        "samples": latency.get("samples"),
        "repeats": latency.get("repeats"),
        "warmup": latency.get("warmup"),
        "scope": latency.get("scope"),
        "by_operation": latency.get("by_operation", {}),
    }
    baseline_cases = [
        {
            "id": case.get("id"),
            "operation": case.get("operation"),
            **{metric: case.get(metric) for metric in QUALITY_METRICS},
        }
        for case in cases
        if isinstance(case, dict)
    ]
    minimum_by_operation = {
        "context": {"recall_at_k": 1.0, "precision_at_k": 0.65, "mrr": 1.0},
        "find": {"recall_at_k": 1.0, "precision_at_k": 0.4, "mrr": 1.0},
        "first": {"recall_at_k": 1.0, "precision_at_k": 0.65, "mrr": 1.0},
        "related": {"recall_at_k": 1.0, "precision_at_k": 0.9, "mrr": 1.0},
    }
    reported_operations = quality.get("by_operation", {})
    if not isinstance(reported_operations, dict):
        raise EvaluationError("evaluation report quality.by_operation is incomplete")
    baseline = {
        "version": EVALUATION_VERSION,
        "corpus_sha256": corpus_hash,
        "k": report.get("k"),
        "case_count": report.get("case_count"),
        "quality": baseline_quality,
        "cases": baseline_cases,
        "latency_ms": baseline_latency,
        "quality_policy": {
            "minimum": {
                "recall_at_k": 1.0,
                "precision_at_k": 0.6,
                "mrr": 1.0,
            },
            "by_operation_minimum": {
                operation: minimum_by_operation[operation]
                for operation in sorted(reported_operations)
                if operation in minimum_by_operation
            },
            "allowed_baseline_drop": 0.0,
        },
        "latency_policy": {
            "mode": "report_only",
            "regression_ratio": 3.0,
            "minimum_delta_ms": 5.0,
        },
    }
    comparison = compare_with_baseline(
        report,
        baseline,
        corpus_hash=corpus_hash,
        enforce_latency=False,
    )
    if comparison["quality"]["status"] != "pass":
        raise EvaluationError("refusing to create a baseline below the quality minimums")
    return baseline


def write_json_atomic(payload: dict[str, Any], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
