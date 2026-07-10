from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


SCAN_CONFIG_SCHEMA_NAME = "scan_config.schema.json"
MAX_SCAN_CONFIG_BYTES = 1024 * 1024


class ScanConfigError(ValueError):
    pass


def _schema_candidates() -> list[Path]:
    source_root = Path(__file__).resolve().parent.parent
    candidates: list[Path] = [source_root / "schemas" / SCAN_CONFIG_SCHEMA_NAME]

    try:
        distribution = metadata.distribution("mdex-cli")
    except metadata.PackageNotFoundError:
        distribution = None
    if distribution is not None:
        for entry in distribution.files or []:
            normalized = str(entry).replace("\\", "/")
            if normalized.endswith(f"schemas/{SCAN_CONFIG_SCHEMA_NAME}"):
                candidates.append(Path(distribution.locate_file(entry)).resolve())
        candidates.append(
            Path(distribution.locate_file("")).resolve()
            / "schemas"
            / SCAN_CONFIG_SCHEMA_NAME
        )

    candidates.append(Path(sys.prefix).resolve() / "schemas" / SCAN_CONFIG_SCHEMA_NAME)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _load_schema(candidates: Iterable[Path] | None = None) -> dict[str, Any]:
    checked: list[str] = []
    for candidate in candidates if candidates is not None else _schema_candidates():
        path = Path(candidate)
        checked.append(str(path))
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScanConfigError(f"scan config schema is unreadable: {path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ScanConfigError(f"scan config schema root must be an object: {path}")
        try:
            Draft202012Validator.check_schema(loaded)
        except SchemaError as exc:
            raise ScanConfigError(f"scan config schema is invalid: {path}: {exc.message}") from exc
        return loaded
    locations = ", ".join(checked) if checked else "<none>"
    raise ScanConfigError(f"scan config schema is missing; checked: {locations}")


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _json_path(parts: Iterable[Any]) -> str:
    path = list(parts)
    if not path:
        return "$"
    output = "$"
    for item in path:
        if isinstance(item, int):
            output += f"[{item}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item)):
            output += f".{item}"
        else:
            output += f"[{json.dumps(str(item), ensure_ascii=False)}]"
    return output


def _reject_non_finite_numbers(
    value: Any,
    *,
    source: str | Path,
    path: tuple[str | int, ...] = (),
) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ScanConfigError(
            f"invalid scan config {source} at {_json_path(path)}: number must be finite"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_non_finite_numbers(item, source=source, path=(*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_finite_numbers(item, source=source, path=(*path, index))


def validate_scan_config(
    config: dict[str, Any],
    *,
    source: str | Path = "<scan config>",
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _reject_non_finite_numbers(config, source=source)
    active_schema = schema if schema is not None else _load_schema()
    validator = Draft202012Validator(active_schema)
    errors = sorted(
        validator.iter_errors(config),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            tuple(str(item) for item in error.absolute_schema_path),
            error.message,
        ),
    )
    if errors:
        first = errors[0]
        extra = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        raise ScanConfigError(
            f"invalid scan config {source} at {_json_path(first.absolute_path)}: {first.message}{extra}"
        )
    return config


def load_scan_config_with_identity(
    path: str | Path,
    *,
    optional: bool = False,
) -> tuple[dict[str, Any], str]:
    source = Path(path)
    if optional and not source.exists():
        payload = b""
        config: dict[str, Any] = {}
        validate_scan_config(config, source=source)
        return config, hashlib.sha256(payload).hexdigest()
    try:
        if source.stat().st_size > MAX_SCAN_CONFIG_BYTES:
            raise ScanConfigError(
                f"scan config exceeds the {MAX_SCAN_CONFIG_BYTES // (1024 * 1024)} MiB safety limit: {source}"
            )
        with source.open("rb") as handle:
            payload = handle.read(MAX_SCAN_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ScanConfigError(f"cannot read scan config {source}: {exc}") from exc
    if len(payload) > MAX_SCAN_CONFIG_BYTES:
        raise ScanConfigError(
            f"scan config exceeds the {MAX_SCAN_CONFIG_BYTES // (1024 * 1024)} MiB safety limit: {source}"
        )
    try:
        loaded = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_nonstandard_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScanConfigError(f"invalid JSON scan config {source}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ScanConfigError(f"scan config root must be an object: {source}")
    validate_scan_config(loaded, source=source)
    return loaded, hashlib.sha256(payload).hexdigest()


def load_scan_config(path: str | Path, *, optional: bool = False) -> dict[str, Any]:
    config, _identity = load_scan_config_with_identity(path, optional=optional)
    return config
