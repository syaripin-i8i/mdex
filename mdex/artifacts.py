from __future__ import annotations

import fnmatch
import json
import re
from collections import deque
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mdex.tokens import estimate_tokens

DEFAULT_ARTIFACT_ROOTS = ("outputs",)
DEFAULT_INCLUDE_GLOBS = ("**/*.json", "**/*.jsonl", "**/*.md", "**/*.txt")
DEFAULT_EXCLUDE_GLOBS = ("**/raw_logs/**", "**/quarantine/**")
DEFAULT_STALE_AFTER_DAYS = 14
DEFAULT_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_JSONL_ROWS_READ = 20
TIMESTAMP_FIELD_CANDIDATES = (
    "generated_at",
    "created_at",
    "timestamp",
    "date",
    "run_started_at",
    "started_at",
    "updated_at",
)
STATUS_FIELD_CANDIDATES = ("status", "result", "outcome", "state")
TASK_ID_RE = re.compile(r"\b(?:[A-Z]{2,12}\.\d+|T\d{14})\b")
FILENAME_DATE_RE = re.compile(
    r"(?P<year>20\d{2})[-_]?(?P<month>\d{2})[-_]?(?P<day>\d{2})"
    r"(?:[T_\-]?(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})?)?"
)
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_posix(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _normalize_list(value: Any, default: Iterable[str]) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        result = [str(item).strip() for item in value if str(item).strip()]
        if result:
            return result
    return [str(item) for item in default]


def _config_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _config_optional_positive_int(value: Any, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "none", "null", "unlimited", "false"}:
            return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else None


def _pattern_variants(pattern: str) -> list[str]:
    normalized = _to_posix(pattern.strip())
    if not normalized:
        return []
    variants = {normalized}
    if normalized.startswith("**/"):
        variants.add(normalized[len("**/") :])
    if not normalized.startswith("**/"):
        variants.add(f"**/{normalized}")
    if normalized.endswith("/**"):
        base = normalized[: -len("/**")].rstrip("/")
        if base:
            variants.add(base)
            variants.add(f"**/{base}")
    return sorted(variants)


def _matches_any(path_value: str, patterns: Iterable[str]) -> bool:
    normalized = _to_posix(path_value)
    for pattern in patterns:
        for variant in _pattern_variants(str(pattern)):
            if fnmatch.fnmatch(normalized, variant):
                return True
    return False


def _resolve_roots(roots: Iterable[str | Path]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        path = Path(root).resolve()
        key = path.as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def _common_root(roots: list[Path]) -> Path:
    if not roots:
        return Path(".").resolve()
    if len(roots) == 1:
        return roots[0].resolve()
    import os

    return Path(os.path.commonpath([str(root) for root in roots])).resolve()


def _node_id_for_path(file_path: Path, root_path: Path) -> str:
    return _to_posix(str(file_path.resolve().relative_to(root_path.resolve())))


def _list_artifact_files(roots: list[Path], include_globs: list[str], exclude_globs: list[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for file_path in root.rglob("*"):
            if file_path.is_symlink() or not file_path.is_file():
                continue
            relative = _to_posix(str(file_path.relative_to(root)))
            if not _matches_any(relative, include_globs):
                continue
            if _matches_any(relative, exclude_globs):
                continue
            key = file_path.resolve().as_posix()
            if key in seen:
                continue
            seen.add(key)
            files.append(file_path.resolve())
    return sorted(files, key=lambda item: item.as_posix())


def _parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", raw):
            raw = f"{raw}T00:00:00+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_from_filename(path: Path) -> datetime | None:
    for match in FILENAME_DATE_RE.finditer(path.name):
        parts = match.groupdict()
        try:
            hour = int(parts.get("hour") or 0)
            minute = int(parts.get("minute") or 0)
            second = int(parts.get("second") or 0)
            return datetime(
                int(parts["year"]),
                int(parts["month"]),
                int(parts["day"]),
                hour,
                minute,
                second,
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
    return None


def _timestamp_from_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _find_timestamp(value: Any) -> datetime | None:
    if isinstance(value, list):
        for item in reversed(value):
            parsed = _find_timestamp(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, dict):
        for key in TIMESTAMP_FIELD_CANDIDATES:
            if key in value:
                parsed = _parse_datetime_value(value.get(key))
                if parsed is not None:
                    return parsed
        for nested_key in ("metadata", "mdex", "run", "result"):
            nested = value.get(nested_key)
            if isinstance(nested, dict):
                parsed = _find_timestamp(nested)
                if parsed is not None:
                    return parsed
    return None


def _infer_kind(relative_path: str, data: Any) -> str:
    haystack = relative_path.lower().replace("-", "_")
    if isinstance(data, dict):
        for key in ("kind", "type", "artifact_kind"):
            raw = data.get(key)
            if isinstance(raw, str) and raw.strip():
                normalized = raw.strip().lower().replace("-", "_")
                if normalized:
                    haystack = f"{normalized} {haystack}"
    checks = (
        ("voice_monitor", ("voice_monitor", "voicemonitor", "voice")),
        ("eval_result", ("eval_result", "eval_results", "evaluation", "eval")),
        ("attribution", ("attribution", "attributed")),
        ("investigation", ("investigation", "investigations")),
        ("audit", ("audit", "audits")),
        ("report", ("report", "reports")),
    )
    for kind, needles in checks:
        if any(needle in haystack for needle in needles):
            return kind
    return "unknown"


def _status_from_data(data: Any) -> str:
    if isinstance(data, list):
        for item in reversed(data):
            status = _status_from_data(item)
            if status != "unknown":
                return status
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    for key in STATUS_FIELD_CANDIDATES:
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
        if isinstance(raw, bool):
            return "pass" if raw else "fail"
    for nested_key in ("summary", "result", "metadata"):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            status = _status_from_data(nested)
            if status != "unknown":
                return status
    return "unknown"


def _compact_value(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _headline_from_json(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        for key in ("headline", "title", "summary", "description", "message"):
            text = _compact_value(data.get(key))
            if text:
                return text
        status = _status_from_data(data)
        metric_bits: list[str] = []
        for key, value in data.items():
            lowered = str(key).lower()
            if lowered in {"p95", "p50", "latency_p95", "score", "accuracy", "pass_rate", "total"}:
                metric_bits.append(f"{key}={_compact_value(value, 40)}")
        if status != "unknown" or metric_bits:
            prefix = f"status={status}" if status != "unknown" else "metrics"
            suffix = "; ".join(metric_bits[:4])
            return f"{prefix}: {suffix}" if suffix else prefix
    if isinstance(data, list) and data:
        return _headline_from_json(data[-1], fallback)
    return fallback


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path, max_rows: int) -> list[Any]:
    safe_max_rows = max(1, max_rows)
    rows: deque[Any] = deque(maxlen=safe_max_rows)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
    return list(rows)


def _text_headline(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = MARKDOWN_HEADING_RE.match(line)
        if match:
            return match.group(1).strip()
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return _compact_value(clean)
    return fallback


def _load_artifact_payload(path: Path, *, max_jsonl_rows_read: int) -> tuple[Any, str, list[str]]:
    suffix = path.suffix.lower()
    warnings: list[str] = []
    fallback = path.stem.replace("_", " ").replace("-", " ").strip() or path.name
    try:
        if suffix == ".json":
            data = _read_json(path)
            return data, _headline_from_json(data, fallback), warnings
        if suffix == ".jsonl":
            data = _read_jsonl(path, max_jsonl_rows_read)
            return data, _headline_from_json(data, fallback), warnings
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"text": text}, _text_headline(text, fallback), warnings
    except Exception as exc:
        warnings.append(str(exc))
        return {}, fallback, warnings


def _timestamp_for_artifact(path: Path, data: Any) -> datetime:
    return _timestamp_from_filename(path) or _find_timestamp(data) or _timestamp_from_mtime(path)


def _task_ids_from_text(*values: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        for match in TASK_ID_RE.findall(value):
            if match in seen:
                continue
            seen.add(match)
            result.append(match)
    return result


def _search_terms(node_id: str, kind: str, status: str, headline: str, task_ids: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in [node_id, Path(node_id).name, kind, status, headline, *task_ids]:
        for token in re.findall(r"[A-Za-z0-9_.:-]{2,}|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]{2,}", raw):
            clean = token.strip().lower()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            terms.append(clean)
    return terms


def _fingerprint(file_path: Path) -> dict[str, Any]:
    stat = file_path.stat()
    import hashlib

    return {
        "mtime_ns": int(stat.st_mtime_ns),
        "size": int(stat.st_size),
        "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
    }


def _warning(path: str, code: str, error: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": path, "error": error, "code": code}
    payload.update(extra)
    return payload


def build_artifacts_index(
    roots: Iterable[str | Path],
    config: dict[str, Any] | None = None,
    *,
    node_id_root: str | Path | None = None,
) -> dict[str, Any]:
    active_config = config if isinstance(config, dict) else {}
    root_values = _normalize_list(roots, DEFAULT_ARTIFACT_ROOTS)
    resolved_roots = _resolve_roots(root_values)
    root_path = Path(node_id_root).resolve() if node_id_root is not None else _common_root(resolved_roots)
    include_globs = _normalize_list(active_config.get("include_globs"), DEFAULT_INCLUDE_GLOBS)
    exclude_globs = _normalize_list(active_config.get("exclude_globs"), DEFAULT_EXCLUDE_GLOBS)
    stale_after_days = _config_int(active_config.get("stale_after_days"), DEFAULT_STALE_AFTER_DAYS)
    max_file_size_bytes = _config_optional_positive_int(
        active_config.get("max_file_size_bytes"),
        DEFAULT_MAX_FILE_SIZE_BYTES,
    )
    max_jsonl_rows_read = _config_int(
        active_config.get("max_jsonl_rows_read"),
        DEFAULT_MAX_JSONL_ROWS_READ,
    )
    kind_stale_after = active_config.get("stale_after_days_by_kind")
    if not isinstance(kind_stale_after, dict):
        kind_stale_after = active_config.get("kind_stale_after_days")
    kind_stale_after_days = kind_stale_after if isinstance(kind_stale_after, dict) else {}

    nodes: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    fingerprints: dict[str, dict[str, Any]] = {}

    for file_path in _list_artifact_files(resolved_roots, include_globs, exclude_globs):
        try:
            node_id = _node_id_for_path(file_path, root_path)
        except ValueError:
            node_id = _to_posix(str(file_path.resolve()))

        try:
            stat = file_path.stat()
        except FileNotFoundError:
            warnings.append(_warning(node_id, "file_disappeared", "artifact file disappeared during scan"))
            continue
        except OSError as exc:
            warnings.append(_warning(node_id, "stat_failed", str(exc)))
            continue

        if max_file_size_bytes is not None and int(stat.st_size) > max_file_size_bytes:
            warnings.append(
                _warning(
                    node_id,
                    "file_too_large",
                    "artifact file exceeds max_file_size_bytes",
                    size=int(stat.st_size),
                    max_file_size_bytes=max_file_size_bytes,
                )
            )
            continue

        try:
            data, headline, load_warnings = _load_artifact_payload(
                file_path,
                max_jsonl_rows_read=max_jsonl_rows_read,
            )
        except FileNotFoundError:
            warnings.append(_warning(node_id, "file_disappeared", "artifact file disappeared during scan"))
            continue
        except OSError as exc:
            warnings.append(_warning(node_id, "read_failed", str(exc)))
            continue

        relative_hint = node_id
        kind = _infer_kind(relative_hint, data)
        try:
            generated_at = _timestamp_for_artifact(file_path, data).isoformat()
        except FileNotFoundError:
            warnings.append(_warning(node_id, "file_disappeared", "artifact file disappeared during scan"))
            continue
        except OSError as exc:
            warnings.append(_warning(node_id, "stat_failed", str(exc)))
            continue
        status = _status_from_data(data)
        task_ids = _task_ids_from_text(node_id, headline, json.dumps(data, ensure_ascii=False)[:2000])
        stale_days = _config_int(kind_stale_after_days.get(kind), stale_after_days)
        summary_parts = [headline]
        if status != "unknown":
            summary_parts.append(f"status={status}")
        summary_parts.append(f"kind={kind}")
        if task_ids:
            summary_parts.append(f"tasks={','.join(task_ids[:4])}")
        summary = "; ".join(part for part in summary_parts if part)
        matching_roots = [root for root in resolved_roots if file_path.is_relative_to(root)]
        source_root = _common_root(matching_roots or [file_path.parent])
        metadata = {
            "index": "artifacts",
            "kind": kind,
            "generated_at": generated_at,
            "source_root": _to_posix(str(source_root)),
            "path": _to_posix(str(file_path)),
            "status": status,
            "headline": headline,
            "task_ids": task_ids,
            "stale_after_days": stale_days,
        }
        if task_ids:
            metadata["task_id"] = task_ids[0]

        nodes.append(
            {
                "id": node_id,
                "title": headline,
                "type": "artifact",
                "project": "unknown",
                "status": status,
                "summary": summary,
                "tags": ["artifact", kind, status],
                "updated": generated_at,
                "estimated_tokens": estimate_tokens(summary),
                "search_terms": _search_terms(node_id, kind, status, headline, task_ids),
                "learning_note": {},
                "links_to": [],
                "depends_on": [],
                "relates_to": [],
                "metadata": metadata,
            }
        )
        try:
            fingerprints[node_id] = _fingerprint(file_path)
        except FileNotFoundError:
            warnings.append(
                _warning(node_id, "file_disappeared_after_index", "artifact file disappeared after indexing")
            )
        except OSError as exc:
            warnings.append(_warning(node_id, "fingerprint_failed", str(exc)))
        for warning in load_warnings:
            warnings.append(_warning(node_id, "read_warning", warning))

    return {
        "generated": _now_iso(),
        "scan_root": _to_posix(str(root_path)),
        "scan_roots": [_to_posix(str(path)) for path in resolved_roots],
        "nodes": nodes,
        "edges": [],
        "warnings": warnings,
        "fingerprints": fingerprints,
        "index_kind": "artifacts",
    }
