from __future__ import annotations

from typing import Any

from mdex.store import apply_node_summary, resolve_node_id_from_path


def resolve_node_id(node_or_path: str, db_path: str, *, path_mode: bool = False) -> str | None:
    if path_mode:
        return resolve_node_id_from_path(db_path, node_or_path)
    clean = node_or_path.strip()
    if not clean:
        return None
    return clean


def enrich_node(
    node_id: str,
    db_path: str,
    summary: str | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    summary_text = (summary or "").strip()
    if not summary_text:
        return {
            "status": "error",
            "error": "summary is required",
            "node_id": node_id,
        }

    result = apply_node_summary(
        db_path,
        node_id,
        summary_text,
        source="agent",
        overwrite_existing_agent=force,
    )
    status = str(result.get("status", ""))
    if status == "missing":
        return {"status": "error", "error": "node not found", "node_id": node_id}

    previous_summary = str(result.get("previous_summary", "") or "")
    previous_source = str(result.get("previous_source", "") or "").strip().lower()
    if status == "skipped":
        return {
            "status": "skipped",
            "reason": "agent summary already exists",
            "node_id": node_id,
            "previous_summary": previous_summary,
            "new_summary": previous_summary,
            "summary_source": previous_source or "agent",
            "skipped": True,
        }

    if status != "updated":
        return {"status": "error", "error": "failed to persist summary", "node_id": node_id}

    return {
        "status": "enriched",
        "node_id": node_id,
        "previous_summary": previous_summary,
        "new_summary": summary_text,
        "summary_source": "agent",
        "skipped": False,
    }
