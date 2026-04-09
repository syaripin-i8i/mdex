from __future__ import annotations

from typing import Any

try:
    from .store import get_node, list_edges, list_nodes
except ImportError:
    from store import get_node, list_edges, list_nodes  # type: ignore


OUTGOING_WEIGHTS = {
    "depends_on": 5.0,
    "links_to": 3.0,
    "relates_to": 1.5,
}

INCOMING_WEIGHTS = {
    "depends_on": 3.0,
    "links_to": 1.5,
    "relates_to": 1.0,
}


def _weight_for(edge_type: str, outgoing: bool) -> float:
    if outgoing:
        return OUTGOING_WEIGHTS.get(edge_type, 0.75)
    return INCOMING_WEIGHTS.get(edge_type, 0.5)


def related_nodes(node_id: str, db_path: str, limit: int = 10) -> list[dict[str, Any]]:
    source_node = get_node(db_path, node_id)
    if source_node is None:
        return []

    node_rows = list_nodes(db_path)
    node_map = {str(node.get("id", "")): node for node in node_rows if str(node.get("id", ""))}
    edge_rows = list_edges(db_path)

    score_by_id: dict[str, float] = {}
    reasons_by_id: dict[str, list[str]] = {}

    for edge in edge_rows:
        if not bool(edge.get("resolved", False)):
            continue

        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        edge_type = str(edge.get("type", "")).strip() or "links_to"
        if not src or not dst:
            continue

        if src == node_id and dst != node_id:
            candidate_id = dst
            weight = _weight_for(edge_type, outgoing=True)
            signal = f"outgoing:{edge_type}"
        elif dst == node_id and src != node_id:
            candidate_id = src
            weight = _weight_for(edge_type, outgoing=False)
            signal = f"incoming:{edge_type}"
        else:
            continue

        score_by_id[candidate_id] = score_by_id.get(candidate_id, 0.0) + weight
        reasons_by_id.setdefault(candidate_id, [])
        if signal not in reasons_by_id[candidate_id]:
            reasons_by_id[candidate_id].append(signal)

    ranked_ids = sorted(
        score_by_id.keys(),
        key=lambda candidate: (-score_by_id[candidate], candidate),
    )

    results: list[dict[str, Any]] = []
    for candidate_id in ranked_ids[: max(0, limit)]:
        candidate = node_map.get(candidate_id, {"id": candidate_id})
        results.append(
            {
                "id": candidate_id,
                "score": round(score_by_id[candidate_id], 3),
                "reasons": reasons_by_id.get(candidate_id, []),
                "title": str(candidate.get("title", "")),
                "type": str(candidate.get("type", "")),
                "project": str(candidate.get("project", "")),
                "status": str(candidate.get("status", "")),
            }
        )

    return results
