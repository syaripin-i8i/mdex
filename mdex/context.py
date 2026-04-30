from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from mdex.reader import read_node_text
from mdex.resolver import prerequisite_order, related_nodes
from mdex.store import get_scan_root, list_edges_for_nodes, list_nodes, search_nodes

KEYWORD_SPLIT_RE = re.compile(r"[\s,.;:!?/\\(){}\[\]<>\"']+")
MDEX_FIND_ACTION_RE = re.compile(r'^run mdex find "(?P<query>.*)"$')

CODE_ENTRYPOINT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".ts",
    ".tsx",
}
TEST_PATH_MARKERS = {"/test/", "/tests/", "_test.", ".test.", ".spec.", "/spec/"}
PATH_TOKEN_RE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]+")
GUARDRAIL_TERMS = {
    "SLA",
    "SLO",
    "boundary",
    "caveat",
    "compatibility",
    "constraint",
    "contract",
    "credential",
    "credentials",
    "gotcha",
    "guard",
    "guardrail",
    "hazard",
    "invariant",
    "migration",
    "must",
    "pitfall",
    "prohibit",
    "rule",
    "rollback",
    "secret",
    "secrets",
    "token",
    "warning",
    "breaking",
    "breaking change",
    "ロールバック",
    "互換性",
    "例外",
    "制約",
    "前提",
    "注意",
    "権限",
    "破壊的変更",
    "禁止",
    "落とし穴",
    "認可",
    "認証",
}

_GUARDRAIL_MATCH_TERMS = tuple(sorted((term.lower() for term in GUARDRAIL_TERMS), key=lambda item: (len(item), item)))
_GUARDRAIL_REASON_TERMS = tuple(sorted(GUARDRAIL_TERMS, key=lambda item: (len(item), item)))

DIGEST_MODES = {"minimal", "full"}

MINIMAL_DIGEST_KEYS = (
    "intent",
    "relevant_docs",
    "suggested_rg",
    "context_gaps",
)

DEFAULT_KEYWORD_WEIGHTS = {
    # Title is strongest lexical signal because it usually expresses scope succinctly.
    "title": 3.0,
    # Summary is curated but shorter than body; keep medium importance.
    "summary": 1.5,
    # Tags are explicit intent markers; weight slightly above summary.
    "tags": 2.2,
}

DEFAULT_TYPE_BONUS = {
    # Design/decision documents usually contain constraints and rationale needed before editing.
    "design": 1.2,
    "decision": 1.2,
    # Reference/spec often explain interfaces and invariants.
    "reference": 0.9,
    "spec": 0.9,
    # Task nodes are useful but often procedural rather than foundational.
    "task": 0.4,
}

DEFAULT_STATUS_BONUS = {
    # Active/draft work is more likely to influence current tasks.
    "active": 0.8,
    "draft": 0.4,
    "pending": 0.2,
    # Done items remain useful but should not dominate current context selection.
    "done": -0.5,
    # Archived content should be de-prioritized by default.
    "archived": -0.7,
}

DEFAULT_GRAPH_BOOST_BY_EDGE_TYPE = {
    # Graph proximity is useful, but weaker than direct lexical match.
    # dependencies are strongest because they imply prerequisites.
    "depends_on": 0.6,
    # links_to is informative but looser than explicit dependency.
    "links_to": 0.35,
    # relates_to is broad and should only provide small assistance.
    "relates_to": 0.2,
}

DEFAULT_GRAPH_DEFAULT_BOOST = 0.15

DEFAULT_PRIMARY_KEYWORD_SEARCH_MULTIPLIER = 5
DEFAULT_SECONDARY_KEYWORD_SEARCH_MULTIPLIER = 2
DEFAULT_PRIMARY_KEYWORD_SEARCH_FLOOR = 20
DEFAULT_SECONDARY_KEYWORD_SEARCH_FLOOR = 10

DEFAULT_SOFT_BUDGET_MULTIPLIER = 1.2
DEFAULT_RECENCY_WEIGHT = 1.0


def _copy_default_scoring_config() -> dict[str, Any]:
    return {
        "keyword": dict(DEFAULT_KEYWORD_WEIGHTS),
        "type_bonus": dict(DEFAULT_TYPE_BONUS),
        "status_bonus": dict(DEFAULT_STATUS_BONUS),
        "graph_boost_by_edge_type": dict(DEFAULT_GRAPH_BOOST_BY_EDGE_TYPE),
        "graph_default_boost": float(DEFAULT_GRAPH_DEFAULT_BOOST),
        "recency_weight": float(DEFAULT_RECENCY_WEIGHT),
        "primary_keyword_search_multiplier": int(DEFAULT_PRIMARY_KEYWORD_SEARCH_MULTIPLIER),
        "secondary_keyword_search_multiplier": int(DEFAULT_SECONDARY_KEYWORD_SEARCH_MULTIPLIER),
        "primary_keyword_search_floor": int(DEFAULT_PRIMARY_KEYWORD_SEARCH_FLOOR),
        "secondary_keyword_search_floor": int(DEFAULT_SECONDARY_KEYWORD_SEARCH_FLOOR),
        "soft_budget_multiplier": float(DEFAULT_SOFT_BUDGET_MULTIPLIER),
    }


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_positive_float(value: Any, default: float) -> float:
    parsed = _coerce_float(value, default)
    if parsed <= 0:
        return default
    return parsed


def _extract_scoring_section(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    section = config.get("context_scoring")
    if isinstance(section, dict):
        return section
    return {}


def _apply_scoring_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> bool:
    if not isinstance(overrides, dict):
        return False

    changed = False

    keyword = overrides.get("keyword")
    if isinstance(keyword, dict):
        keyword_map = dict(base["keyword"])
        for key in ("title", "summary", "tags"):
            if key not in keyword:
                continue
            next_value = _coerce_float(keyword.get(key), float(keyword_map[key]))
            if next_value != float(keyword_map[key]):
                keyword_map[key] = next_value
                changed = True
        base["keyword"] = keyword_map

    type_bonus = overrides.get("type_bonus")
    if isinstance(type_bonus, dict):
        type_map = dict(base["type_bonus"])
        for raw_key, raw_value in type_bonus.items():
            key = str(raw_key).strip().lower()
            if not key:
                continue
            prev_value = float(type_map.get(key, 0.0))
            next_value = _coerce_float(raw_value, prev_value)
            if key not in type_map or next_value != prev_value:
                type_map[key] = next_value
                changed = True
        base["type_bonus"] = type_map

    status_bonus = overrides.get("status_bonus")
    if isinstance(status_bonus, dict):
        status_map = dict(base["status_bonus"])
        for raw_key, raw_value in status_bonus.items():
            key = str(raw_key).strip().lower()
            if not key:
                continue
            prev_value = float(status_map.get(key, 0.0))
            next_value = _coerce_float(raw_value, prev_value)
            if key not in status_map or next_value != prev_value:
                status_map[key] = next_value
                changed = True
        base["status_bonus"] = status_map

    graph_boost = overrides.get("graph_boost_by_edge_type")
    if isinstance(graph_boost, dict):
        graph_map = dict(base["graph_boost_by_edge_type"])
        for raw_key, raw_value in graph_boost.items():
            key = str(raw_key).strip().lower()
            if not key:
                continue
            prev_value = float(graph_map.get(key, 0.0))
            next_value = _coerce_float(raw_value, prev_value)
            if key not in graph_map or next_value != prev_value:
                graph_map[key] = next_value
                changed = True
        base["graph_boost_by_edge_type"] = graph_map

    scalar_float_keys = (
        "graph_default_boost",
        "recency_weight",
        "soft_budget_multiplier",
    )
    for key in scalar_float_keys:
        if key not in overrides:
            continue
        prev_value = float(base[key])
        next_value = _coerce_positive_float(overrides.get(key), prev_value)
        if next_value != prev_value:
            base[key] = next_value
            changed = True

    scalar_int_keys = (
        "primary_keyword_search_multiplier",
        "secondary_keyword_search_multiplier",
        "primary_keyword_search_floor",
        "secondary_keyword_search_floor",
    )
    for key in scalar_int_keys:
        if key not in overrides:
            continue
        prev_value = int(base[key])
        next_value = _coerce_positive_int(overrides.get(key), prev_value)
        if next_value != prev_value:
            base[key] = next_value
            changed = True

    return changed


def resolve_context_scoring_config(
    *,
    runtime_config: dict[str, Any] | None = None,
    scan_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    resolved = _copy_default_scoring_config()
    source = "defaults"

    scan_section = _extract_scoring_section(scan_config)
    if _apply_scoring_overrides(resolved, scan_section):
        source = "scan_config"

    runtime_section = _extract_scoring_section(runtime_config)
    if _apply_scoring_overrides(resolved, runtime_section):
        source = "runtime_config"

    return resolved, source


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


def _keyword_match_breakdown(
    node: dict[str, Any],
    keywords: list[str],
    *,
    scoring: dict[str, Any],
) -> dict[str, float]:
    title = str(node.get("title", "")).lower()
    summary = str(node.get("summary", "")).lower()
    tags = {str(item).strip().lower() for item in node.get("tags", []) if str(item).strip()}

    keyword_weights = scoring.get("keyword", DEFAULT_KEYWORD_WEIGHTS)
    title_weight = float(keyword_weights.get("title", DEFAULT_KEYWORD_WEIGHTS["title"]))
    summary_weight = float(keyword_weights.get("summary", DEFAULT_KEYWORD_WEIGHTS["summary"]))
    tags_weight = float(keyword_weights.get("tags", DEFAULT_KEYWORD_WEIGHTS["tags"]))

    title_score = 0.0
    summary_score = 0.0
    tags_score = 0.0
    for keyword in keywords:
        if keyword in title:
            title_score += title_weight
        if keyword in summary:
            summary_score += summary_weight
        if keyword in tags:
            tags_score += tags_weight

    total = title_score + summary_score + tags_score
    return {
        "title": round(title_score, 3),
        "summary": round(summary_score, 3),
        "tags": round(tags_score, 3),
        "total": round(total, 3),
    }


def _type_status_breakdown(
    node: dict[str, Any],
    *,
    scoring: dict[str, Any],
) -> dict[str, float]:
    node_type = str(node.get("type", "")).strip().lower()
    status = str(node.get("status", "")).strip().lower()
    type_bonus_map = scoring.get("type_bonus", DEFAULT_TYPE_BONUS)
    status_bonus_map = scoring.get("status_bonus", DEFAULT_STATUS_BONUS)
    type_bonus = float(type_bonus_map.get(node_type, 0.0))
    status_bonus = float(status_bonus_map.get(status, 0.0))
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


def _node_type(node_map: dict[str, dict[str, Any]], node_id: str) -> str:
    return str(node_map.get(node_id, {}).get("type", "")).strip().lower()


def _node_status(node_map: dict[str, dict[str, Any]], node_id: str) -> str:
    return str(node_map.get(node_id, {}).get("status", "")).strip().lower()


def _node_title(node_map: dict[str, dict[str, Any]], node_id: str) -> str:
    return str(node_map.get(node_id, {}).get("title", ""))


def _node_summary(node_map: dict[str, dict[str, Any]], node_id: str) -> str:
    return str(node_map.get(node_id, {}).get("summary", ""))


def _node_tags(node_map: dict[str, dict[str, Any]], node_id: str) -> list[str]:
    tags = node_map.get(node_id, {}).get("tags", [])
    if not isinstance(tags, list):
        return []
    return [str(item).strip() for item in tags if str(item).strip()]


def _is_code_entrypoint(node_id: str, node: dict[str, Any] | None = None) -> bool:
    lowered = node_id.lower().replace("\\", "/")
    if any(lowered.endswith(extension) for extension in CODE_ENTRYPOINT_EXTENSIONS):
        return True
    if lowered.endswith((".md", ".json", ".jsonl", ".txt", ".rst")):
        return False
    if node is None:
        return False
    node_type = str(node.get("type", "")).strip().lower()
    return node_type in {"code", "source", "implementation", "test"}


def _is_test_entrypoint(node_id: str) -> bool:
    normalized = node_id.lower().replace("\\", "/")
    lowered = f"/{normalized}"
    return any(marker in lowered for marker in TEST_PATH_MARKERS)


def _node_brief(
    node_id: str,
    node_map: dict[str, dict[str, Any]],
    *,
    reason: str,
    priority: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": node_id,
        "title": _node_title(node_map, node_id),
        "type": _node_type(node_map, node_id),
        "status": _node_status(node_map, node_id),
        "reason": reason,
    }
    if priority is not None:
        payload["priority"] = priority
    return payload


def _unique_node_briefs(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        node_id = str(item.get("id", "")).strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _top_level_path(node_id: str) -> str:
    normalized = node_id.replace("\\", "/").strip("/")
    if not normalized:
        return "."
    return normalized.split("/", 1)[0]


def _indexed_code_mentions(node_id: str, node_map: dict[str, dict[str, Any]]) -> list[str]:
    source = " ".join([_node_title(node_map, node_id), _node_summary(node_map, node_id)])
    mentioned: list[str] = []
    seen: set[str] = set()
    for match in PATH_TOKEN_RE.findall(source):
        candidate = match.strip().strip(".,;:()[]{}<>\"'").replace("\\", "/")
        if candidate in seen:
            continue
        node = node_map.get(candidate)
        if not node:
            continue
        if not _is_code_entrypoint(candidate, node):
            continue
        seen.add(candidate)
        mentioned.append(candidate)
    return mentioned


def _rg_paths(
    recommended_read_order: list[dict[str, Any]],
    likely_code_entrypoints: list[dict[str, Any]],
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    for item in likely_code_entrypoints:
        node_id = str(item.get("id", "")).strip()
        if not node_id:
            continue
        path = _top_level_path(node_id)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)

    for item in recommended_read_order:
        node_id = str(item.get("id", "")).strip()
        if not node_id:
            continue
        path = _top_level_path(node_id)
        if path in {"tasks", "task"}:
            continue
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
        if len(paths) >= 4:
            break

    return paths[:4] or ["."]


def _suggested_rg_commands(
    query: str,
    recommended_read_order: list[dict[str, Any]],
    likely_code_entrypoints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    terms = _query_keywords(query)
    if not terms:
        return []

    pattern = "|".join(re.escape(term) for term in terms[:5])
    paths = _rg_paths(recommended_read_order, likely_code_entrypoints)
    args = ["-n", pattern, *paths]
    return [
        {
            "command": "rg",
            "args": args,
            "pattern": pattern,
            "paths": paths,
            "reason": "expand from mdex entrypoint candidates into exact source matches",
        }
    ]


def _guardrail_reason(node_id: str, node_map: dict[str, dict[str, Any]]) -> str:
    haystack = " ".join(
        [
            _node_title(node_map, node_id),
            _node_summary(node_map, node_id),
            " ".join(_node_tags(node_map, node_id)),
        ]
    ).lower()
    matches = [term for term in _GUARDRAIL_REASON_TERMS if term.lower() in haystack]
    if matches:
        return f"mentions {'/'.join(matches[:3])}"
    return "design/spec/reference node may define constraints"


def _build_actionable_digest(
    query: str,
    selected_nodes: list[dict[str, Any]],
    recommended_read_order: list[dict[str, Any]],
    deferred_nodes: list[dict[str, Any]],
    confidence: float,
    node_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    read_items: list[dict[str, Any]] = []
    task_items: list[dict[str, Any]] = []
    code_items: list[dict[str, Any]] = []
    guardrail_items: list[dict[str, Any]] = []

    ordered_candidates = list(recommended_read_order)
    ordered_candidates.extend({"id": str(item.get("id", "")), "reason": "selected by context score"} for item in selected_nodes)
    ordered_candidates.extend(deferred_nodes)

    for index, item in enumerate(ordered_candidates, start=1):
        node_id = str(item.get("id", "")).strip()
        if not node_id:
            continue
        node = node_map.get(node_id, {})
        node_type = _node_type(node_map, node_id)
        reason = str(item.get("reason", "")).strip() or "selected by context score"

        if _is_code_entrypoint(node_id, node):
            code_reason = "likely test entrypoint" if _is_test_entrypoint(node_id) else "likely code entrypoint"
            code_items.append(_node_brief(node_id, node_map, reason=code_reason, priority=index))
            continue

        if node_type == "task":
            task_items.append(_node_brief(node_id, node_map, reason=reason, priority=index))
            continue

        read_items.append(_node_brief(node_id, node_map, reason=reason, priority=index))

        if node_type in {"decision", "design", "reference", "spec"}:
            haystack = " ".join(
                [
                    _node_title(node_map, node_id),
                    _node_summary(node_map, node_id),
                    " ".join(_node_tags(node_map, node_id)),
                ]
            ).lower()
            if any(term in haystack for term in _GUARDRAIL_MATCH_TERMS):
                guardrail_items.append(
                    _node_brief(node_id, node_map, reason=_guardrail_reason(node_id, node_map), priority=index)
                )

        for mentioned_id in _indexed_code_mentions(node_id, node_map):
            code_reason = "mentioned test entrypoint" if _is_test_entrypoint(mentioned_id) else "mentioned code entrypoint"
            code_items.append(_node_brief(mentioned_id, node_map, reason=code_reason, priority=index))

    relevant_docs = _unique_node_briefs(read_items, limit=6)
    relevant_task_history = _unique_node_briefs(task_items, limit=5)
    likely_code_entrypoints = _unique_node_briefs(code_items, limit=5)
    known_guardrails = _unique_node_briefs(guardrail_items, limit=5)
    suggested_rg = _suggested_rg_commands(query, recommended_read_order, likely_code_entrypoints)

    context_gaps: list[str] = []
    if confidence < 0.6:
        context_gaps.append("low confidence: mdex found sparse direct matches")
    if not relevant_docs:
        context_gaps.append("no strong document entrypoint found")
    if not likely_code_entrypoints:
        context_gaps.append("no indexed code entrypoint found; use suggested rg to bridge into source code")
    if not known_guardrails:
        context_gaps.append("no explicit guardrail/trap node found for this query")

    return {
        "intent": query.strip(),
        "relevant_docs": relevant_docs,
        "relevant_task_history": relevant_task_history,
        "likely_code_entrypoints": likely_code_entrypoints,
        "known_guardrails": known_guardrails,
        "suggested_rg": suggested_rg,
        "context_gaps": context_gaps,
    }


def _empty_actionable_digest(query: str, reason: str) -> dict[str, Any]:
    terms = _query_keywords(query)
    suggested_rg: list[dict[str, Any]] = []
    if terms:
        pattern = "|".join(re.escape(term) for term in terms[:5])
        args = ["-n", pattern, "."]
        suggested_rg.append(
            {
                "command": "rg",
                "args": args,
                "pattern": pattern,
                "paths": ["."],
                "reason": "mdex has insufficient context; fall back to exact source search",
            }
        )
    return {
        "intent": query.strip(),
        "relevant_docs": [],
        "relevant_task_history": [],
        "likely_code_entrypoints": [],
        "known_guardrails": [],
        "suggested_rg": suggested_rg,
        "context_gaps": [reason],
    }


def project_actionable_digest(payload: dict[str, Any], digest: str) -> dict[str, Any]:
    if str(digest or "full").strip().lower() != "minimal":
        return payload
    return {key: payload.get(key, [] if key != "intent" else "") for key in MINIMAL_DIGEST_KEYS}


def _normalize_digest_mode(digest: str) -> str:
    clean_digest = str(digest or "full").strip().lower()
    if clean_digest in DIGEST_MODES:
        return clean_digest
    return "full"



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


def _structured_action(command: str, args: list[str], reason: str) -> dict[str, Any]:
    return {
        "command": command,
        "args": [item for item in args if str(item).strip()],
        "reason": reason,
    }


def _action_v2_from_legacy(action: str) -> dict[str, Any]:
    text = action.strip()
    if text.startswith("open "):
        node_id = text[5:].strip()
        return _structured_action("mdex", ["open", node_id], "read the recommended node first")

    if text.startswith("search code for "):
        query = text[len("search code for ") :].strip()
        return _structured_action("rg", ["-n", query, "."], "expand evidence from source code")

    find_match = MDEX_FIND_ACTION_RE.match(text)
    if find_match:
        query = str(find_match.group("query")).strip()
        return _structured_action("mdex", ["find", query], "collect broader candidates when confidence is low")

    if text == "run mdex context with a more specific query":
        return _structured_action("mdex", ["context"], "retry with a narrower query for better ranking")

    if text == "run mdex scan":
        return _structured_action("mdex", ["scan"], "refresh index metadata before selecting an entrypoint")

    return _structured_action("mdex", ["context", text], "retry with the manual follow-up text")


def _next_actions_v2(actions: list[str]) -> list[dict[str, Any]]:
    return [_action_v2_from_legacy(action) for action in actions if str(action).strip()]


def select_context(
    query: str,
    db_path: str,
    budget: int = 4000,
    limit: int = 10,
    *,
    include_content: bool = False,
    actionable: bool = False,
    digest: str = "full",
    scoring_config: dict[str, Any] | None = None,
    scoring_config_source: str = "defaults",
) -> dict[str, Any]:
    digest_mode = _normalize_digest_mode(digest)
    active_scoring = _copy_default_scoring_config()
    if isinstance(scoring_config, dict):
        _apply_scoring_overrides(active_scoring, scoring_config)

    keywords = _extract_keywords(query)
    if not keywords:
        return {
            "query": query,
            "nodes": [],
            "total_tokens": 0,
            "budget": int(budget),
            "recommended_read_order": [],
            "recommended_next_actions": [],
            "recommended_next_actions_v2": [],
            "deferred_nodes": [],
            "confidence": 0.0,
            "why_this_set": [],
            "actionable_digest": project_actionable_digest(
                _empty_actionable_digest(query, "blank query: provide a task description"),
                digest_mode,
            ),
        }

    safe_limit = _coerce_positive_int(limit, 10)
    safe_budget = _coerce_positive_int(budget, 4000)

    all_nodes = list_nodes(db_path)
    all_node_map = {str(node.get("id", "")).strip(): node for node in all_nodes if str(node.get("id", "")).strip()}
    candidate_map: dict[str, dict[str, Any]] = {}
    primary_multiplier = _coerce_positive_int(
        active_scoring.get("primary_keyword_search_multiplier"),
        DEFAULT_PRIMARY_KEYWORD_SEARCH_MULTIPLIER,
    )
    secondary_multiplier = _coerce_positive_int(
        active_scoring.get("secondary_keyword_search_multiplier"),
        DEFAULT_SECONDARY_KEYWORD_SEARCH_MULTIPLIER,
    )
    primary_floor = _coerce_positive_int(
        active_scoring.get("primary_keyword_search_floor"),
        DEFAULT_PRIMARY_KEYWORD_SEARCH_FLOOR,
    )
    secondary_floor = _coerce_positive_int(
        active_scoring.get("secondary_keyword_search_floor"),
        DEFAULT_SECONDARY_KEYWORD_SEARCH_FLOOR,
    )

    for index, keyword in enumerate(keywords):
        search_limit = max(primary_floor, safe_limit * primary_multiplier)
        if index > 0:
            search_limit = max(
                secondary_floor,
                safe_limit * secondary_multiplier,
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
            "recommended_next_actions_v2": [],
            "deferred_nodes": [],
            "confidence": 0.0,
            "why_this_set": [],
            "actionable_digest": project_actionable_digest(
                _empty_actionable_digest(
                    query,
                    "no mdex candidates found; use suggested rg or add frontmatter/tags to entry docs",
                ),
                digest_mode,
            ),
        }

    seed_ids = sorted(candidate_map.keys())
    graph_boost: dict[str, float] = {}
    linked_ids: set[str] = set(seed_ids)
    for edge in list_edges_for_nodes(db_path, seed_ids, resolved_only=True):
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        edge_type = str(edge.get("type", "")).strip() or "links_to"
        graph_boost_map = active_scoring.get("graph_boost_by_edge_type", DEFAULT_GRAPH_BOOST_BY_EDGE_TYPE)
        default_graph_boost = float(active_scoring.get("graph_default_boost", DEFAULT_GRAPH_DEFAULT_BOOST))
        boost = float(graph_boost_map.get(edge_type, default_graph_boost))
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
    recency_weight = float(active_scoring.get("recency_weight", DEFAULT_RECENCY_WEIGHT))
    for node_id, node in candidate_map.items():
        keyword_breakdown = _keyword_match_breakdown(node, keywords, scoring=active_scoring)
        type_status_breakdown = _type_status_breakdown(node, scoring=active_scoring)
        recency_raw = _recency_score(str(node.get("updated", "")))
        recency = recency_raw * recency_weight
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
            "recency_raw": round(recency_raw, 3),
            "recency_weight": round(recency_weight, 3),
            "graph_boost": round(graph, 3),
            "config_source": scoring_config_source,
            "total": round(total_score, 3),
        }
        scored_rows.append((total_score, node_id, node, score_breakdown))

    scored_rows.sort(key=lambda row: (-row[0], row[1]))

    scan_root = get_scan_root(db_path, default=".")
    selected_nodes: list[dict[str, Any]] = []
    total_tokens = 0
    soft_budget_multiplier = float(active_scoring.get("soft_budget_multiplier", DEFAULT_SOFT_BUDGET_MULTIPLIER))
    soft_cap = int(safe_budget * soft_budget_multiplier)

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
                    "soft_budget_multiplier": round(soft_budget_multiplier, 3),
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
    next_actions_v2 = _next_actions_v2(next_actions)
    actionable_digest = _build_actionable_digest(
        query,
        selected_nodes,
        read_order,
        deferred,
        confidence,
        node_map,
    )
    actionable_digest = project_actionable_digest(actionable_digest, digest_mode)

    payload.update(
        {
            "recommended_read_order": read_order,
            "recommended_next_actions": next_actions,
            "recommended_next_actions_v2": next_actions_v2,
            "deferred_nodes": deferred,
            "confidence": confidence,
            "why_this_set": why_this_set,
            "actionable_digest": actionable_digest,
        }
    )
    return payload
