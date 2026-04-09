from __future__ import annotations

from collections import deque
from typing import Any

from runtime.store import get_node, list_edges, list_nodes


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

TAG_MATCH_WEIGHT = 1.75
TYPE_MATCH_WEIGHT = 0.85


def _weight_for(edge_type: str, outgoing: bool) -> float:
    if outgoing:
        return OUTGOING_WEIGHTS.get(edge_type, 0.75)
    return INCOMING_WEIGHTS.get(edge_type, 0.5)


def _normalize_tags(node: dict[str, Any]) -> set[str]:
    value = node.get("tags", [])
    if not isinstance(value, list):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _normalize_type(node: dict[str, Any]) -> str:
    value = str(node.get("type", "")).strip().lower()
    if value in {"", "unknown"}:
        return ""
    return value


def _append_reason(reasons_by_id: dict[str, list[str]], candidate_id: str, reason: str) -> None:
    reasons_by_id.setdefault(candidate_id, [])
    if reason not in reasons_by_id[candidate_id]:
        reasons_by_id[candidate_id].append(reason)


def _node_payload(node: dict[str, Any], node_id: str) -> dict[str, Any]:
    return {
        "id": node_id,
        "title": str(node.get("title", "")),
        "type": str(node.get("type", "")),
        "project": str(node.get("project", "")),
        "status": str(node.get("status", "")),
    }


def related_nodes(node_id: str, db_path: str, limit: int = 10) -> list[dict[str, Any]]:
    source_node = get_node(db_path, node_id)
    if source_node is None:
        return []

    node_rows = list_nodes(db_path)
    node_map = {str(node.get("id", "")): node for node in node_rows if str(node.get("id", ""))}
    edge_rows = list_edges(db_path)
    source_tags = _normalize_tags(source_node)
    source_type = _normalize_type(source_node)

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
        _append_reason(reasons_by_id, candidate_id, signal)

    for candidate_id, candidate in node_map.items():
        if candidate_id == node_id:
            continue

        candidate_tags = _normalize_tags(candidate)
        shared_tags = sorted(source_tags.intersection(candidate_tags))
        if shared_tags:
            score_by_id[candidate_id] = score_by_id.get(candidate_id, 0.0) + (
                TAG_MATCH_WEIGHT * len(shared_tags)
            )
            _append_reason(reasons_by_id, candidate_id, f"shared_tags:{','.join(shared_tags)}")

        candidate_type = _normalize_type(candidate)
        if source_type and candidate_type and source_type == candidate_type:
            score_by_id[candidate_id] = score_by_id.get(candidate_id, 0.0) + TYPE_MATCH_WEIGHT
            _append_reason(reasons_by_id, candidate_id, "same_type")

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


def prerequisite_order(node_id: str, db_path: str, limit: int = 10) -> list[dict[str, Any]]:
    source_node = get_node(db_path, node_id)
    if source_node is None:
        return []

    node_rows = list_nodes(db_path)
    node_map = {str(node.get("id", "")): node for node in node_rows if str(node.get("id", ""))}
    dependency_edges = list_edges(db_path, edge_type="depends_on", resolved=True)

    adjacency: dict[str, set[str]] = {}
    for edge in dependency_edges:
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        if not src or not dst or src == dst:
            continue
        adjacency.setdefault(src, set()).add(dst)

    ordered_ids: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def _walk(current: str) -> None:
        if current in visiting:
            return
        visiting.add(current)
        for dependency in sorted(adjacency.get(current, set())):
            if dependency == node_id:
                continue
            if dependency in visited:
                continue
            if dependency in visiting:
                continue
            _walk(dependency)
            if dependency not in visited:
                visited.add(dependency)
                ordered_ids.append(dependency)
        visiting.remove(current)

    _walk(node_id)

    distance_map: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque([(node_id, 0)])
    seen_for_distance: set[str] = {node_id}
    while queue:
        current, depth = queue.popleft()
        for dependency in sorted(adjacency.get(current, set())):
            if dependency == node_id:
                continue
            next_depth = depth + 1
            if dependency not in distance_map or next_depth < distance_map[dependency]:
                distance_map[dependency] = next_depth
            if dependency in seen_for_distance:
                continue
            seen_for_distance.add(dependency)
            queue.append((dependency, next_depth))

    safe_limit = max(0, int(limit))
    if safe_limit == 0:
        return []

    prerequisites: list[dict[str, Any]] = []
    for index, dependency_id in enumerate(ordered_ids[:safe_limit], start=1):
        dependency_node = node_map.get(dependency_id, {"id": dependency_id})
        payload = _node_payload(dependency_node, dependency_id)
        payload["order"] = index
        distance = int(distance_map.get(dependency_id, 1))
        payload["distance"] = distance
        if distance <= 1:
            payload["reason"] = "direct depends_on"
        else:
            payload["reason"] = f"transitive depends_on (depth {distance})"
        prerequisites.append(payload)
    return prerequisites
