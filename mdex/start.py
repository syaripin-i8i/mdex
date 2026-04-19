from __future__ import annotations

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
) -> dict[str, Any]:
    context_payload = select_context(
        task,
        db_path,
        budget=budget,
        limit=limit,
        include_content=include_content,
        actionable=True,
    )
    generated = get_index_metadata(db_path, "generated", "")

    payload: dict[str, Any] = {
        "task": task,
        "db": {
            "path": db_path,
            "source": db_source,
        },
        "index_status": {
            "ready": True,
            "generated": generated,
        },
        "recommended_read_order": context_payload.get("recommended_read_order", []),
        "recommended_next_actions": context_payload.get("recommended_next_actions", []),
        "deferred_nodes": context_payload.get("deferred_nodes", []),
        "confidence": context_payload.get("confidence", 0.0),
        "why_this_set": context_payload.get("why_this_set", []),
        "total_tokens": int(context_payload.get("total_tokens", 0) or 0),
        "budget": int(context_payload.get("budget", budget) or budget),
        "nodes": context_payload.get("nodes", []),
    }
    return payload
