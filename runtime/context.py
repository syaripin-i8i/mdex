from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from runtime.reader import read_node_text
from runtime.resolver import prerequisite_order, related_nodes
from runtime.store import get_scan_root, list_edges_for_nodes, list_nodes, search_nodes

KEYWORD_SPLIT_RE = re.compile(r"[\s,.;:!?/\\(){}\[\]<>\"']+")

# Title is strongest lexical signal because it usually expresses scope succinctly.
KEYWORD_TITLE_WEIGHT = 3.0
# Summary is curated but shorter than body; keep medium importance.
KEYWORD_SUMMARY_WEIGHT = 1.5
# Tags are explicit intent markers; weight slightly above summary.
KEYWORD_TAG_WEIGHT = 2.2

# Design/decision documents usually contain constraints and rationale needed before editing.
TYPE_BONUS = {
    "design": 1.2,
    "decision": 1.2,
    # Reference/spec often explain interfaces and invariants.
    "reference": 0.9,
    "spec": 0.9,
    # Task nodes are useful but often procedural rather than foundational.
    "task": 0.4,
}

# Active/draft work is more likely to influence current tasks.
STATUS_BONUS = {
    "active": 0.8,
    "draft": 0.4,
    "pending": 0.2,
    # Done items remain useful but should not dominate current context selection.
    "done": -0.5,
    # Archived content should be de-prioritized by default.
    "archived": -0.7,
}

# Graph proximity is useful, but weaker than direct lexical match.
GRAPH_BOOST_BY_EDGE_TYPE = {
    # dependencies are strongest because they imply prerequisites.
    "depends_on": 0.6,
    # links_to is informative but looser than explicit dependency.
    "links_to": 0.35,
    # relates_to is broad and should only provide small assistance.
    "relates_to": 0.2,
}

# Fallback boost for unknown edge types. Keep it small to avoid overfitting.
GRAPH_DEFAULT_BOOST = 0.15

# search_nodes limit multipliers used to gather candidates before graph expansion.
PRIMARY_KEYWORD_SEARCH_MULTIPLIER = 5
SECONDARY_KEYWORD_SEARCH_MULTIPLIER = 2
PRIMARY_KEYWORD_SEARCH_FLOOR = 20
SECONDARY_KEYWORD_SEARCH_FLOOR = 10

# Context selection allows a soft overrun to avoid returning an empty set too often.
SOFT_BUDGET_MULTIPLIER = 1.2


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _extract_keywords(query: str) -> list[str]:
    lowered = query.strip().lower()
    if not lowered:
        return []

    parts = [item.strip() for item in KEYWORD_SPLIT_RE.split(lowered) if item.strip()]
    keywords: list[str] = []
    seen = set()

    if lowered not in seen:
        seen.add(lowered)
        keywords.append(lowered)

    for part in parts:
        if len(part) <= 1:
            continue
        if part in seen:
            continue
        seen.add(part)
        keywords.append(part)
    return keywords


def _parse_updated_timestamp(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _recency_score(value: str) -> float:
    parsed = _parse_updated_timestamp(value)
    if parsed is None:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)
    if age_days <= 30:
        return 1.0
    if age_days <= 90:
        return 0.6
    if age_days <= 180:
        return 0.3
    return 0.0


def _keyword_match_breakdown(node: dict[str, Any], keywords: list[str]) -> dict[str, float]:
    title = str(node.get("title", "")).lower()
    summary = str(node.get("summary", "")).lower()
    tags = {str(item).strip().lower() for item in node.get("tags", []) if str(item).strip()}

    title_score = 0.0
    summary_score = 0.0
    tags_score = 0.0
    for keyword in keywords:
        if keyword in title:
            title_score += KEYWORD_TITLE_WEIGHT
        if keyword in summary:
            summary_score += KEYWORD_SUMMARY_WEIGHT
        if keyword in tags:
            tags_score += KEYWORD_TAG_WEIGHT

    total = title_score + summary_score + tags_score
    return {
        "title": round(title_score, 3),
        "summary": round(summary_score, 3),
        "tags": round(tags_score, 3),
        "total": round(total, 3),
    }


def _type_status_breakdown(node: dict[str, Any]) -> dict[str, float]:
    node_type = str(node.get("type", "")).strip().lower()
    status = str(node.get("status", "")).strip().lower()
    type_bonus = TYPE_BONUS.get(node_type, 0.0)
    status_bonus = STATUS_BONUS.get(status, 0.0)
    total = type_bonus + status_bonus
    return {
        "type_bonus": round(type_bonus, 3),
        "status_bonus": round(status_bonus, 3),
        "total": round(total, 3),
    }


def _estimated_tokens_for_node(node: dict[str, Any]) -> int:
    value = int(node.get("estimated_tokens", 0) or 0)
    if value > 0:
        return value
    fallback = str(node.get("summary", "")) or str(node.get("title", ""))
    return max(1, len(fallback) // 4)


def _load_node_content(node_id: str, scan_root: str, summary_fallback: str) -> str:
    try:
        return read_node_text(scan_root, node_id)
    except FileNotFoundError:
        return summary_fallback


def _node_meta_map(db_path: str) -> dict[str, dict[str, Any]]:
    rows = list_nodes(db_path)
    return {
        str(row.get("id", "")).strip(): row
        for row in rows
        if str(row.get("id", "")).strip()
    }


def _read_order(
    selected_nodes: list[dict[str, Any]],
    db_path: str,
    node_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(node_id: str, source: str, reason: str) -> None:
        clean_id = node_id.strip()
        if not clean_id or clean_id in seen:
            return
        seen.add(clean_id)
        node = node_map.get(clean_id, {})
        ordered.append(
            {
                "id": clean_id,
                "title": str(node.get("title", "")),
                "priority": len(ordered) + 1,
                "source": source,
                "reason": reason,
            }
        )

    anchors = selected_nodes[:3]
    for anchor in anchors:
        anchor_id = str(anchor.get("id", "")).strip()
        if not anchor_id:
            continue
        for prerequisite in prerequisite_order(anchor_id, db_path, limit=2):
            prereq_id = str(prerequisite.get("id", "")).strip()
            if not prereq_id:
                continue
            _append(prereq_id, "first", str(prerequisite.get("reason", "prerequisite")))
        _append(anchor_id, "context", "high lexical or graph score")

    for row in selected_nodes:
        node_id = str(row.get("id", "")).strip()
        if not node_id:
            continue
        _append(node_id, "context", "selected by context score")

    return ordered


def _deferred_nodes(
    selected_nodes: list[dict[str, Any]],
    db_path: str,
    picked_ids: set[str],
) -> list[dict[str, Any]]:
    deferred: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in selected_nodes[:2]:
        anchor_id = str(anchor.get("id", "")).strip()
        if not anchor_id:
            continue
        for related in related_nodes(anchor_id, db_path, limit=4):
            related_id = str(related.get("id", "")).strip()
            if not related_id or related_id in picked_ids or related_id in seen:
                continue
            seen.add(related_id)
            deferred.append(
                {
                    "id": related_id,
                    "reason": "related but low priority for first pass",
                }
            )
            if len(deferred) >= 8:
                return deferred
    return deferred


def _confidence(selected_nodes: list[dict[str, Any]]) -> float:
    if not selected_nodes:
        return 0.0
    total = float(len(selected_nodes))
    direct = 0
    graph = 0
    fresh = 0
    for node in selected_nodes:
        breakdown = node.get("score_breakdown", {})
        keyword = breakdown.get("keyword", {})
        if float(keyword.get("total", 0.0) or 0.0) > 0:
            direct += 1
        if float(breakdown.get("graph_boost", 0.0) or 0.0) > 0:
            graph += 1
        if float(breakdown.get("recency", 0.0) or 0.0) > 0:
            fresh += 1
    score = min(
        1.0,
        0.25 + (direct / total) * 0.4 + (graph / total) * 0.2 + (fresh / total) * 0.15,
    )
    return round(score, 2)


def _why_this_set(
    selected_nodes: list[dict[str, Any]],
    confidence: float,
    node_map: dict[str, dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if selected_nodes:
        reasons.append("top nodes contain direct query hits")
    if any(
        float(node.get("score_breakdown", {}).get("graph_boost", 0.0) or 0.0) > 0
        for node in selected_nodes
    ):
        reasons.append("prerequisite documents are pulled ahead")
    statuses = [
        str(node_map.get(str(row.get("id", "")), {}).get("status", "")).strip().lower()
        for row in selected_nodes
    ]
    if any(status in {"done", "archived"} for status in statuses):
        reasons.append("done/archived nodes were deprioritized")
    if confidence < 0.6:
        reasons.append("low confidence due to sparse direct matches")
    return reasons[:4]


def _query_keywords(query: str) -> list[str]:
    words = [item for item in _extract_keywords(query) if item and " " not in item]
    filtered: list[str] = []
    seen = set()
    for word in words:
        if len(word) < 2:
            continue
        if word in seen:
            continue
        seen.add(word)
        filtered.append(word)
    return filtered


def _next_actions(
    query: str,
    recommended_read_order: list[dict[str, Any]],
    confidence: float,
    node_map: dict[str, dict[str, Any]],
) -> list[str]:
    actions: list[str] = []

    for row in recommended_read_order[:2]:
        node_id = str(row.get("id", "")).strip()
        if not node_id:
            continue
        actions.append(f"open {node_id}")

    keyword_terms = _query_keywords(query)
    has_design_or_decision = any(
        str(node_map.get(str(row.get("id", "")), {}).get("type", "")).strip().lower()
        in {"design", "decision"}
        for row in recommended_read_order[:4]
    )
    if has_design_or_decision and len(keyword_terms) >= 2:
        actions.append(f"search code for {' '.join(keyword_terms[:3])}")
    elif len(keyword_terms) >= 2:
        actions.append(f"search code for {' '.join(keyword_terms[:3])}")

    if confidence < 0.6:
        actions.append(f'run mdex find "{query}"')

    if not actions:
        actions.append("run mdex context with a more specific query")
    return actions[:5]


def select_context(
    query: str,
    db_path: str,
    budget: int = 4000,
    limit: int = 10,
    *,
    include_content: bool = False,
    actionable: bool = False,
) -> dict[str, Any]:
    keywords = _extract_keywords(query)
    if not keywords:
        return {
            "query": query,
            "nodes": [],
            "total_tokens": 0,
            "budget": int(budget),
            "recommended_read_order": [],
            "recommended_next_actions": [],
            "deferred_nodes": [],
            "confidence": 0.0,
            "why_this_set": [],
        }

    safe_limit = _coerce_positive_int(limit, 10)
    safe_budget = _coerce_positive_int(budget, 4000)

    all_nodes = list_nodes(db_path)
    all_node_map = {str(node.get("id", "")).strip(): node for node in all_nodes if str(node.get("id", "")).strip()}
    candidate_map: dict[str, dict[str, Any]] = {}
    for index, keyword in enumerate(keywords):
        search_limit = max(PRIMARY_KEYWORD_SEARCH_FLOOR, safe_limit * PRIMARY_KEYWORD_SEARCH_MULTIPLIER)
        if index > 0:
            search_limit = max(
                SECONDARY_KEYWORD_SEARCH_FLOOR,
                safe_limit * SECONDARY_KEYWORD_SEARCH_MULTIPLIER,
            )
        for node in search_nodes(db_path, keyword, limit=search_limit, nodes=all_nodes):
            node_id = str(node.get("id", "")).strip()
            if node_id:
                candidate_map[node_id] = node

    if not candidate_map:
        return {
            "query": query,
            "nodes": [],
            "total_tokens": 0,
            "budget": safe_budget,
            "recommended_read_order": [],
            "recommended_next_actions": [],
            "deferred_nodes": [],
            "confidence": 0.0,
            "why_this_set": [],
        }

    seed_ids = sorted(candidate_map.keys())
    graph_boost: dict[str, float] = {}
    linked_ids: set[str] = set(seed_ids)
    for edge in list_edges_for_nodes(db_path, seed_ids, resolved_only=True):
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        edge_type = str(edge.get("type", "")).strip() or "links_to"
        boost = GRAPH_BOOST_BY_EDGE_TYPE.get(edge_type, GRAPH_DEFAULT_BOOST)
        if not src or not dst:
            continue
        if src in seed_ids and dst not in seed_ids:
            graph_boost[dst] = graph_boost.get(dst, 0.0) + boost
            linked_ids.add(dst)
        if dst in seed_ids and src not in seed_ids:
            graph_boost[src] = graph_boost.get(src, 0.0) + boost
            linked_ids.add(src)

    for linked_id in linked_ids:
        if linked_id in all_node_map:
            candidate_map[linked_id] = all_node_map[linked_id]

    scored_rows: list[tuple[float, str, dict[str, Any], dict[str, Any]]] = []
    for node_id, node in candidate_map.items():
        keyword_breakdown = _keyword_match_breakdown(node, keywords)
        type_status_breakdown = _type_status_breakdown(node)
        recency = _recency_score(str(node.get("updated", "")))
        graph = graph_boost.get(node_id, 0.0)
        total_score = (
            float(keyword_breakdown["total"])
            + float(type_status_breakdown["total"])
            + recency
            + graph
        )
        score_breakdown = {
            "keyword": keyword_breakdown,
            "type_status": type_status_breakdown,
            "recency": round(recency, 3),
            "graph_boost": round(graph, 3),
            "total": round(total_score, 3),
        }
        scored_rows.append((total_score, node_id, node, score_breakdown))

    scored_rows.sort(key=lambda row: (-row[0], row[1]))

    scan_root = get_scan_root(db_path, default=".")
    selected_nodes: list[dict[str, Any]] = []
    total_tokens = 0
    soft_cap = int(safe_budget * SOFT_BUDGET_MULTIPLIER)

    for score, node_id, node, score_breakdown in scored_rows:
        if len(selected_nodes) >= safe_limit:
            break

        estimated_tokens = _estimated_tokens_for_node(node)
        projected = total_tokens + estimated_tokens

        if selected_nodes and projected > soft_cap:
            continue

        row: dict[str, Any] = {
            "id": node_id,
            "priority": len(selected_nodes) + 1,
            "score": round(score, 3),
            "score_breakdown": {
                **score_breakdown,
                "token_cost": {
                    "estimated_tokens": estimated_tokens,
                    "soft_cap": soft_cap,
                },
            },
            "estimated_tokens": estimated_tokens,
        }
        if include_content:
            summary_fallback = str(node.get("summary", "")) or str(node.get("title", ""))
            row["content"] = _load_node_content(node_id, scan_root, summary_fallback)
        selected_nodes.append(row)
        total_tokens = projected

    payload = {
        "query": query,
        "nodes": selected_nodes,
        "total_tokens": total_tokens,
        "budget": safe_budget,
    }
    if not actionable:
        return payload

    node_map = all_node_map
    read_order = _read_order(selected_nodes, db_path, node_map)
    read_order_ids = {str(item.get("id", "")).strip() for item in read_order}
    deferred = _deferred_nodes(selected_nodes, db_path, read_order_ids)
    confidence = _confidence(selected_nodes)
    why_this_set = _why_this_set(selected_nodes, confidence, node_map)
    next_actions = _next_actions(query, read_order, confidence, node_map)

    payload.update(
        {
            "recommended_read_order": read_order,
            "recommended_next_actions": next_actions,
            "deferred_nodes": deferred,
            "confidence": confidence,
            "why_this_set": why_this_set,
        }
    )
    return payload
