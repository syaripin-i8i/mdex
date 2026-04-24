from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mdex.context import select_context
from mdex.store import get_index_metadata


def build_start_payload(
    task: str,
    db_path: str,
    *,
    db_source: str,
    budget: int,
    limit: int,
    include_content: bool,
    scoring_config: dict[str, Any] | None = None,
    scoring_config_source: str = "defaults",
    stale_after_hours: int = 24,
) -> dict[str, Any]:
    generated = str(get_index_metadata(db_path, "generated", "") or "").strip()
    index_status = _index_status(generated, stale_after_hours=stale_after_hours)

    context_payload = select_context(
        task,
        db_path,
        budget=budget,
        limit=limit,
        include_content=include_content,
        actionable=True,
        scoring_config=scoring_config,
        scoring_config_source=scoring_config_source,
    )
    recommended_next_actions = list(context_payload.get("recommended_next_actions", []))
    recommended_next_actions_v2 = list(context_payload.get("recommended_next_actions_v2", []))

    if not bool(index_status.get("fresh", False)):
        _append_scan_action(recommended_next_actions, recommended_next_actions_v2)

    recommended_read_order = context_payload.get("recommended_read_order", [])
    confidence = float(context_payload.get("confidence", 0.0) or 0.0)
    entrypoint_reason = _entrypoint_reason(
        recommended_read_order=recommended_read_order,
        confidence=confidence,
        index_status=index_status,
    )

    payload: dict[str, Any] = {
        "task": task,
        "db": {
            "path": db_path,
            "source": db_source,
        },
        "index_status": index_status,
        "entrypoint_reason": entrypoint_reason,
        "recommended_read_order": recommended_read_order,
        "recommended_next_actions": recommended_next_actions,
        "recommended_next_actions_v2": recommended_next_actions_v2,
        "deferred_nodes": context_payload.get("deferred_nodes", []),
        "confidence": confidence,
        "why_this_set": context_payload.get("why_this_set", []),
        "total_tokens": int(context_payload.get("total_tokens", 0) or 0),
        "budget": int(context_payload.get("budget", budget) or budget),
        "nodes": context_payload.get("nodes", []),
    }
    return payload


def _parse_utc_timestamp(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _index_status(generated: str, *, stale_after_hours: int) -> dict[str, Any]:
    safe_stale_after_hours = max(1, int(stale_after_hours))
    parsed_generated = _parse_utc_timestamp(generated)

    if parsed_generated is None:
        return {
            "ready": True,
            "generated": generated,
            "fresh": False,
            "stale": True,
            "age_hours": None,
            "stale_after_hours": safe_stale_after_hours,
            "reason": "missing_or_invalid_generated_timestamp",
        }

    age_hours = max(0.0, (datetime.now(timezone.utc) - parsed_generated).total_seconds() / 3600.0)
    fresh = age_hours <= float(safe_stale_after_hours)
    return {
        "ready": True,
        "generated": generated,
        "fresh": fresh,
        "stale": not fresh,
        "age_hours": round(age_hours, 2),
        "stale_after_hours": safe_stale_after_hours,
        "reason": "fresh_index" if fresh else "stale_index",
    }


def _entrypoint_reason(
    *,
    recommended_read_order: list[dict[str, Any]],
    confidence: float,
    index_status: dict[str, Any],
) -> str:
    has_read_order = bool(recommended_read_order)
    is_stale = not bool(index_status.get("fresh", False))

    if not has_read_order and is_stale:
        return "no_recommended_read_order_and_stale_index"
    if not has_read_order:
        return "no_recommended_read_order"
    if confidence < 0.6 and is_stale:
        return "low_confidence_and_stale_index"
    if confidence < 0.6:
        return "low_confidence_candidates"
    if is_stale:
        return "stale_index_refresh_recommended"
    return "ranked_entrypoint_available"


def _append_scan_action(actions: list[str], actions_v2: list[dict[str, Any]]) -> None:
    if "run mdex scan" not in actions:
        actions.append("run mdex scan")

    has_scan_v2 = any(
        str(item.get("command", "")).strip() == "mdex"
        and list(item.get("args", []))[:1] == ["scan"]
        for item in actions_v2
        if isinstance(item, dict)
    )
    if has_scan_v2:
        return
    actions_v2.append(
        {
            "command": "mdex",
            "args": ["scan"],
            "reason": "refresh stale index before deciding entrypoint",
        }
    )
