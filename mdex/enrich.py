from __future__ import annotations

from typing import Any

from mdex.store import get_node, resolve_node_id_from_path, update_node_summary

def resolve_node_id(node_or_path: str, db_path: str, *, path_mode: bool = False) -> str | None:
    if path_mode:
        return resolve_node_id_from_path(db_path, node_or_path)
    clean = node_or_path.strip()
    if not clean:
        return None
    return clean


def _should_skip_existing(summary_source: str, force: bool) -> bool:
    if force:
        return False
    return summary_source.strip().lower() == "agent"


def enrich_node(
    node_id: str,
    db_path: str,
    summary: str | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    node = get_node(db_path, node_id)
    if node is None:
        return {"status": "error", "error": "node not found", "node_id": node_id}

    previous_summary = str(node.get("summary", "") or "")
    previous_source = str(node.get("summary_source", "") or "").strip().lower()
    summary_text = (summary or "").strip()
    if not summary_text:
        return {
            "status": "error",
            "error": "summary is required",
            "node_id": node_id,
        }

    if _should_skip_existing(previous_source, force):
        return {
            "status": "skipped",
            "reason": "agent summary already exists",
            "node_id": node_id,
            "previous_summary": previous_summary,
            "new_summary": previous_summary,
            "summary_source": previous_source or "agent",
            "skipped": True,
        }

    updated = update_node_summary(db_path, node_id, summary_text, source="agent")
    if not updated:
        return {"status": "error", "error": "failed to persist summary", "node_id": node_id}

    return {
        "status": "enriched",
        "node_id": node_id,
        "previous_summary": previous_summary,
        "new_summary": summary_text,
        "summary_source": "agent",
        "skipped": False,
    }
