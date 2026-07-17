from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from mdex.scan_manifest import ScanManifestError, load_scan_manifest
from mdex.source_freshness import verify_manifest_source_state
from mdex.store import list_index_metadata


HEALTH_STATUSES = {"healthy", "warning", "stale", "unavailable"}
HEALTH_REASON_CODES = {
    "index_reusable",
    "source_fingerprint_mismatch",
    "config_hash_mismatch",
    "config_file_identity_mismatch",
    "scan_input_missing",
    "source_state_verification_failed",
    "index_age_exceeded",
    "worktree_borrowed_index",
    "missing_or_invalid_generated_timestamp",
    "scan_manifest_missing",
    "scan_manifest_invalid",
    "index_db_missing",
    "index_metadata_unavailable",
    "multi_index_reusable",
    "multi_index_not_reusable",
    "multi_index_unavailable",
}
DEFAULT_STALE_AFTER_HOURS = 24


def _parse_utc_timestamp(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_fields(generated: str, stale_after_hours: int) -> dict[str, Any]:
    safe_limit = max(1, int(stale_after_hours))
    parsed = _parse_utc_timestamp(generated)
    age_hours = (
        None
        if parsed is None
        else round(
            max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0),
            2,
        )
    )
    return {
        "generated": generated,
        "age_hours": age_hours,
        "stale_after_hours": safe_limit,
    }


def _source_state(
    status: str,
    reason: str,
    *,
    scan_id: str = "",
    config_hash: str = "",
    detail: str = "",
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "scan_id": scan_id,
        "config_hash": config_hash,
    }
    if detail:
        output["detail"] = detail
    return output


def _health(
    *,
    status: str,
    reusable: bool,
    reason: str,
    source_state: Mapping[str, Any],
    generated: str,
    age_hours: float | None,
    stale_after_hours: int,
    scan_id: str = "",
    config_hash: str = "",
    index_kind: str = "",
    alias: str = "repo",
    db: str = "",
    source: str = "unknown",
) -> dict[str, Any]:
    return {
        "status": status,
        "reusable": bool(reusable),
        "reason": reason,
        "scan_id": scan_id,
        "config_hash": config_hash,
        "source_state": dict(source_state),
        "generated": generated,
        "age_hours": age_hours,
        "stale_after_hours": stale_after_hours,
        "index_kind": index_kind,
        "alias": alias,
        "db": db,
        "source": source,
    }


def unavailable_health(
    reason: str,
    *,
    db_path: str | Path = "",
    alias: str = "repo",
    source: str = "unknown",
    generated: str = "",
    age_hours: float | None = None,
    stale_after_hours: int = DEFAULT_STALE_AFTER_HOURS,
    scan_id: str = "",
    config_hash: str = "",
    index_kind: str = "",
    detail: str = "",
) -> dict[str, Any]:
    return _health(
        status="unavailable",
        reusable=False,
        reason=reason,
        source_state=_source_state(
            "unknown",
            reason,
            scan_id=scan_id,
            config_hash=config_hash,
            detail=detail,
        ),
        generated=generated,
        age_hours=age_hours,
        stale_after_hours=max(1, int(stale_after_hours)),
        scan_id=scan_id,
        config_hash=config_hash,
        index_kind=index_kind,
        alias=alias,
        db=str(db_path),
        source=source,
    )


def evaluate_index_health(
    db_path: str | Path,
    *,
    alias: str = "repo",
    source: str = "unknown",
    borrowed: bool = False,
    stale_after_hours: int = DEFAULT_STALE_AFTER_HOURS,
) -> dict[str, Any]:
    """Return the authoritative, evidence-reuse health for one index.

    Source identity is evaluated before age. A recent timestamp can therefore
    never make changed, unidentified, or borrowed source evidence reusable.
    Doctor hygiene findings are intentionally outside this evaluator: a
    hygiene warning may require cleanup without invalidating a verified scan.
    """
    path = Path(db_path)
    safe_limit = max(1, int(stale_after_hours))
    if not path.exists():
        return unavailable_health(
            "index_db_missing",
            db_path=path,
            alias=alias,
            source=source,
            stale_after_hours=safe_limit,
        )

    try:
        metadata = list_index_metadata(str(path))
    except Exception as exc:
        return unavailable_health(
            "index_metadata_unavailable",
            db_path=path,
            alias=alias,
            source=source,
            stale_after_hours=safe_limit,
            detail=str(exc),
        )

    generated = str(metadata.get("generated", "") or "").strip()
    age = _age_fields(generated, safe_limit)
    parsed_generated = _parse_utc_timestamp(generated)
    manifest_raw = str(metadata.get("scan_manifest", "") or "").strip()
    if not manifest_raw:
        return unavailable_health(
            "scan_manifest_missing",
            db_path=path,
            alias=alias,
            source=source,
            **age,
        )

    try:
        manifest = load_scan_manifest(metadata)
    except (ScanManifestError, ValueError) as exc:
        return unavailable_health(
            "scan_manifest_invalid",
            db_path=path,
            alias=alias,
            source=source,
            detail=str(exc),
            **age,
        )

    scan_id = str(manifest.get("scan_id", ""))
    config_hash = str(manifest.get("config_hash", ""))
    index_kind = str(manifest.get("index_kind", ""))
    source_state = verify_manifest_source_state(manifest, metadata)
    source_status = str(source_state.get("status", "unknown"))
    source_reason = str(source_state.get("reason", "source_state_verification_failed"))

    # Source invalidity outranks timestamp age.
    if source_status == "stale":
        return _health(
            status="stale",
            reusable=False,
            reason=source_reason,
            source_state=source_state,
            scan_id=scan_id,
            config_hash=config_hash,
            index_kind=index_kind,
            alias=alias,
            db=str(path),
            source=source,
            **age,
        )
    if source_status != "fresh":
        return _health(
            status="unavailable",
            reusable=False,
            reason=source_reason,
            source_state=source_state,
            scan_id=scan_id,
            config_hash=config_hash,
            index_kind=index_kind,
            alias=alias,
            db=str(path),
            source=source,
            **age,
        )
    if borrowed:
        return _health(
            status="stale",
            reusable=False,
            reason="worktree_borrowed_index",
            source_state=source_state,
            scan_id=scan_id,
            config_hash=config_hash,
            index_kind=index_kind,
            alias=alias,
            db=str(path),
            source=source,
            **age,
        )
    if parsed_generated is None:
        return _health(
            status="unavailable",
            reusable=False,
            reason="missing_or_invalid_generated_timestamp",
            source_state=source_state,
            scan_id=scan_id,
            config_hash=config_hash,
            index_kind=index_kind,
            alias=alias,
            db=str(path),
            source=source,
            **age,
        )
    if datetime.now(timezone.utc) - parsed_generated > timedelta(hours=safe_limit):
        return _health(
            status="stale",
            reusable=False,
            reason="index_age_exceeded",
            source_state=source_state,
            scan_id=scan_id,
            config_hash=config_hash,
            index_kind=index_kind,
            alias=alias,
            db=str(path),
            source=source,
            **age,
        )
    return _health(
        status="healthy",
        reusable=True,
        reason="index_reusable",
        source_state=source_state,
        scan_id=scan_id,
        config_hash=config_hash,
        index_kind=index_kind,
        alias=alias,
        db=str(path),
        source=source,
        **age,
    )


def index_status_from_health(health: Mapping[str, Any]) -> dict[str, Any]:
    """Project the legacy start/index_status contract from official health."""
    reusable = bool(health.get("reusable", False))
    reason = str(health.get("reason", "health_unavailable"))
    return {
        "ready": reason != "index_db_missing",
        "generated": str(health.get("generated", "")),
        "fresh": reusable,
        "stale": not reusable,
        "age_hours": health.get("age_hours"),
        "stale_after_hours": int(
            health.get("stale_after_hours", DEFAULT_STALE_AFTER_HOURS)
            or DEFAULT_STALE_AFTER_HOURS
        ),
        "reason": reason,
    }


def evidence_identity_from_health(health: Mapping[str, Any]) -> dict[str, Any]:
    reusable = bool(health.get("reusable", False))
    scan_id = str(health.get("scan_id", ""))
    output: dict[str, Any] = {
        "status": "verified" if reusable else ("identified" if scan_id else "unavailable"),
        "reusable": reusable,
        "reason": str(health.get("reason", "health_unavailable")),
    }
    for key in ("scan_id", "config_hash", "index_kind", "source_state"):
        value = health.get(key)
        if value not in (None, "", {}):
            output[key] = dict(value) if isinstance(value, Mapping) else value
    return output


def aggregate_health(
    items: Iterable[Mapping[str, Any]],
    *,
    alias: str = "multi",
) -> dict[str, Any]:
    rows = [dict(item) for item in items]
    if not rows:
        return unavailable_health("multi_index_unavailable", alias=alias)

    rank = {"healthy": 0, "warning": 1, "stale": 2, "unavailable": 3}
    worst = max(rows, key=lambda item: rank.get(str(item.get("status", "unavailable")), 3))
    reusable = all(bool(item.get("reusable", False)) for item in rows)
    if reusable:
        status = "healthy"
        reason = "multi_index_reusable"
        source_status = "fresh"
    else:
        status = str(worst.get("status", "unavailable"))
        reason = "multi_index_not_reusable"
        source_status = "unknown" if status == "unavailable" else "stale"
    ages = [float(item["age_hours"]) for item in rows if item.get("age_hours") is not None]
    return _health(
        status=status,
        reusable=reusable,
        reason=reason,
        source_state=_source_state(source_status, reason),
        generated="",
        age_hours=round(max(ages), 2) if ages else None,
        stale_after_hours=max(
            int(item.get("stale_after_hours", DEFAULT_STALE_AFTER_HOURS) or DEFAULT_STALE_AFTER_HOURS)
            for item in rows
        ),
        alias=alias,
        source="multi_index",
    ) | {
        "blocking_reasons": [
            {
                "alias": str(item.get("alias", "")),
                "reason": str(item.get("reason", "")),
            }
            for item in rows
            if not bool(item.get("reusable", False))
        ],
        "indexes": {str(item.get("alias", "")): item for item in rows},
    }


def combine_health(
    items: Iterable[Mapping[str, Any]],
    *,
    alias: str = "multi",
) -> dict[str, Any]:
    """Keep a single lane's identity intact; aggregate only true multi-index sets."""
    rows = [dict(item) for item in items]
    if len(rows) == 1:
        return rows[0]
    return aggregate_health(rows, alias=alias)
