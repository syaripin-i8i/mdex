from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from mdex.reader import read_node_text
from mdex.resolver import prerequisite_order, related_nodes
from mdex.store import get_scan_root, list_edges_for_nodes, list_nodes, list_orphan_nodes, list_stale_nodes, search_nodes

KEYWORD_SPLIT_RE = re.compile(r"[\s,.;:!?/\\(){}\[\]<>\"'、。．，！？：；・「」『』（）［］｛｝＜＞]+")
MDEX_FIND_ACTION_RE = re.compile(r'^run mdex find "(?P<query>.*)"$')
SCRIPT_SEGMENT_RE = re.compile(r"[a-z0-9_][a-z0-9_.-]{2,}|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]{2,}")

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
AGENT_PACK_ROLES = {"worker", "reviewer", "commander"}

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
    # Search terms are scan-derived aliases/symbols that should bridge sparse wording.
    "search_terms": 2.4,
    # Learning notes capture past failure symptoms and should counteract done-task decay.
    "learning_note": 3.0,
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
DEFAULT_PATH_SYMBOL_WEIGHT = 3.5


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
        "path_symbol_weight": float(DEFAULT_PATH_SYMBOL_WEIGHT),
        "synonyms": {},
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
        for key in ("title", "summary", "tags", "search_terms", "learning_note"):
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
        "path_symbol_weight",
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


def _merge_synonyms(base: dict[str, Any], config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    raw = config.get("synonyms")
    if raw is None:
        raw = config.get("search_synonyms")
    if not isinstance(raw, dict):
        return False

    synonyms = dict(base.get("synonyms", {}))
    changed = False
    for raw_key, raw_values in raw.items():
        key = str(raw_key).strip().lower()
        if not key:
            continue
        values: list[str] = []
        if isinstance(raw_values, str):
            values = [raw_values]
        elif isinstance(raw_values, list):
            values = [str(item) for item in raw_values]
        clean_values = [item.strip().lower() for item in values if str(item).strip()]
        if not clean_values:
            continue
        previous = set(str(item).strip().lower() for item in synonyms.get(key, []) if str(item).strip())
        next_values = sorted(previous | set(clean_values))
        if next_values != sorted(previous):
            synonyms[key] = next_values
            changed = True
    base["synonyms"] = synonyms
    return changed


def resolve_context_scoring_config(
    *,
    runtime_config: dict[str, Any] | None = None,
    scan_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    resolved = _copy_default_scoring_config()
    source = "defaults"

    scan_section = _extract_scoring_section(scan_config)
    if _apply_scoring_overrides(resolved, scan_section) or _merge_synonyms(resolved, scan_config) or _merge_synonyms(resolved, scan_section):
        source = "scan_config"

    runtime_section = _extract_scoring_section(runtime_config)
    if _apply_scoring_overrides(resolved, runtime_section) or _merge_synonyms(resolved, runtime_config) or _merge_synonyms(resolved, runtime_section):
        source = "runtime_config"

    return resolved, source


def _expand_keywords_with_synonyms(keywords: list[str], synonyms: dict[str, Any]) -> list[str]:
    if not synonyms:
        return keywords
    expanded: list[str] = []
    seen: set[str] = set()

    def append(term: str) -> None:
        clean = term.strip().lower()
        if not clean or clean in seen:
            return
        seen.add(clean)
        expanded.append(clean)

    synonym_map: dict[str, set[str]] = {}
    for raw_key, raw_values in synonyms.items():
        key = str(raw_key).strip().lower()
        if not key:
            continue
        values = [str(item).strip().lower() for item in raw_values if str(item).strip()] if isinstance(raw_values, list) else []
        family = {key, *values}
        for item in family:
            synonym_map.setdefault(item, set()).update(family - {item})

    for keyword in keywords:
        append(keyword)
        for synonym in sorted(synonym_map.get(keyword, set())):
            append(synonym)
            for piece in _extract_keywords(synonym):
                append(piece)
    return expanded


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
        for segment in _script_segments(lowered):
            if segment in seen:
                continue
            seen.add(segment)
            keywords.append(segment)

    for part in parts:
        if len(part) <= 1:
            continue
        if part not in seen:
            seen.add(part)
            keywords.append(part)
        for segment in _script_segments(part):
            if segment in seen:
                continue
            seen.add(segment)
            keywords.append(segment)
        for gram in _cjk_ngrams(part):
            if gram in seen:
                continue
            seen.add(gram)
            keywords.append(gram)
    return keywords


def _script_segments(text: str) -> list[str]:
    if not re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]", text):
        return []
    if not re.search(r"[a-z0-9_]", text):
        return []
    return [match.group(0) for match in SCRIPT_SEGMENT_RE.finditer(text)]


def _cjk_ngrams(text: str) -> list[str]:
    grams: list[str] = []
    for match in re.finditer(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]+", text):
        segment = match.group(0)
        if len(segment) < 4:
            continue
        for size in (2, 3, 4):
            if len(segment) < size:
                continue
            grams.extend(segment[index : index + size] for index in range(0, len(segment) - size + 1))
    return grams


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


def _node_metadata(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _is_artifact_node(node: dict[str, Any]) -> bool:
    metadata = _node_metadata(node)
    node_type = str(node.get("type", "")).strip().lower()
    return node_type == "artifact" or str(metadata.get("index", "")).strip().lower() == "artifacts"


def _node_timestamp(node: dict[str, Any]) -> str:
    metadata = _node_metadata(node)
    generated_at = str(metadata.get("generated_at", "")).strip()
    if generated_at:
        return generated_at
    return str(node.get("updated", "")).strip()


def _freshness_for_node(node: dict[str, Any]) -> dict[str, Any]:
    timestamp = _node_timestamp(node)
    parsed = _parse_updated_timestamp(timestamp)
    if parsed is None:
        return {}

    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)
    metadata = _node_metadata(node)
    stale_after_raw = metadata.get("stale_after_days", 30 if _is_artifact_node(node) else 90)
    try:
        stale_after_days = int(stale_after_raw)
    except (TypeError, ValueError):
        stale_after_days = 30 if _is_artifact_node(node) else 90
    stale_after_days = max(1, stale_after_days)
    stale = age_days > stale_after_days
    node_type = str(node.get("type", "")).strip().lower()
    status = "stale" if stale else "fresh"
    if stale and node_type in {"decision", "reference", "spec"}:
        status = "stale_but_authoritative"

    payload: dict[str, Any] = {
        "generated_at": timestamp,
        "age_days": round(age_days, 3),
        "stale": stale,
        "status": status,
        "stale_after_days": stale_after_days,
    }
    kind = str(metadata.get("kind", "")).strip()
    if kind:
        payload["kind"] = kind
    return payload


def _with_node_context_fields(payload: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    metadata = _node_metadata(node)
    if metadata:
        public_metadata = {
            key: value
            for key, value in metadata.items()
            if key not in {"path"}
        }
        if public_metadata:
            payload["metadata"] = public_metadata
    freshness = _freshness_for_node(node)
    if freshness:
        payload["freshness"] = freshness
    return payload


def _keyword_match_breakdown(
    node: dict[str, Any],
    keywords: list[str],
    *,
    scoring: dict[str, Any],
) -> dict[str, float]:
    title = str(node.get("title", "")).lower()
    summary = str(node.get("summary", "")).lower()
    tags = {str(item).strip().lower() for item in node.get("tags", []) if str(item).strip()}
    search_terms = [str(item).strip().lower() for item in node.get("search_terms", []) if str(item).strip()]
    search_text = " ".join(search_terms)
    learning_note = node.get("learning_note", {})
    learning_text = ""
    if isinstance(learning_note, dict):
        for value in learning_note.values():
            if isinstance(value, dict):
                learning_text += " " + " ".join(str(item) for item in value.values())
            elif isinstance(value, list):
                learning_text += " " + " ".join(str(item) for item in value)
            else:
                learning_text += " " + str(value)
    learning_text = learning_text.lower()

    keyword_weights = scoring.get("keyword", DEFAULT_KEYWORD_WEIGHTS)
    title_weight = float(keyword_weights.get("title", DEFAULT_KEYWORD_WEIGHTS["title"]))
    summary_weight = float(keyword_weights.get("summary", DEFAULT_KEYWORD_WEIGHTS["summary"]))
    tags_weight = float(keyword_weights.get("tags", DEFAULT_KEYWORD_WEIGHTS["tags"]))
    search_terms_weight = float(keyword_weights.get("search_terms", DEFAULT_KEYWORD_WEIGHTS["search_terms"]))
    learning_note_weight = float(keyword_weights.get("learning_note", DEFAULT_KEYWORD_WEIGHTS["learning_note"]))

    title_score = 0.0
    summary_score = 0.0
    tags_score = 0.0
    search_terms_score = 0.0
    learning_note_score = 0.0
    matched_terms: list[str] = []
    matched_fields: set[str] = set()
    for keyword in keywords:
        if keyword in title:
            title_score += title_weight
            matched_terms.append(keyword)
            matched_fields.add("title")
        if keyword in summary:
            summary_score += summary_weight
            matched_terms.append(keyword)
            matched_fields.add("summary")
        if keyword in tags:
            tags_score += tags_weight
            matched_terms.append(keyword)
            matched_fields.add("tags")
        if keyword in search_text or keyword in search_terms:
            search_terms_score += search_terms_weight
            matched_terms.append(keyword)
            matched_fields.add("search_terms")
        if keyword in learning_text:
            learning_note_score += learning_note_weight
            matched_terms.append(keyword)
            matched_fields.add("learning_note")

    total = title_score + summary_score + tags_score + search_terms_score + learning_note_score
    return {
        "title": round(title_score, 3),
        "summary": round(summary_score, 3),
        "tags": round(tags_score, 3),
        "search_terms": round(search_terms_score, 3),
        "learning_note": round(learning_note_score, 3),
        "matched_terms": sorted(set(matched_terms)),
        "matched_fields": sorted(matched_fields),
        "total": round(total, 3),
    }


def _query_path_symbol_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./\\-]+", query):
        clean = token.strip().strip(".,;:()[]{}<>\"'").replace("\\", "/")
        if len(clean) < 3 or clean in seen:
            continue
        if "/" not in clean and "." not in clean and "_" not in clean:
            continue
        seen.add(clean)
        terms.append(clean)
    return terms


def _path_symbol_breakdown(node_id: str, node: dict[str, Any], query: str, *, scoring: dict[str, Any]) -> dict[str, Any]:
    weight = float(scoring.get("path_symbol_weight", DEFAULT_PATH_SYMBOL_WEIGHT))
    terms = _query_path_symbol_terms(query)
    if not terms:
        return {"matched_terms": [], "matched_fields": [], "total": 0.0}

    node_id_l = node_id.lower().replace("\\", "/")
    title = str(node.get("title", "")).lower()
    summary = str(node.get("summary", "")).lower()
    search_terms = [str(item).strip().lower() for item in node.get("search_terms", []) if str(item).strip()]
    search_text = " ".join(search_terms)

    total = 0.0
    matched_terms: list[str] = []
    matched_fields: set[str] = set()
    for term in terms:
        lowered = term.lower()
        if lowered in node_id_l or node_id_l.endswith(lowered):
            total += weight
            matched_terms.append(term)
            matched_fields.add("id")
        basename = node_id_l.rsplit("/", 1)[-1]
        if lowered == basename or lowered == _basename_stem(basename):
            total += weight * 0.6
            matched_terms.append(term)
            matched_fields.add("id")
        if lowered in title:
            total += weight * 0.5
            matched_terms.append(term)
            matched_fields.add("title")
        if lowered in summary:
            total += weight * 0.4
            matched_terms.append(term)
            matched_fields.add("summary")
        if lowered in search_text or lowered in search_terms:
            total += weight
            matched_terms.append(term)
            matched_fields.add("search_terms")
    return {
        "matched_terms": sorted(set(matched_terms)),
        "matched_fields": sorted(matched_fields),
        "total": round(total, 3),
    }


def _basename_stem(name: str) -> str:
    if "." not in name:
        return name
    return name.rsplit(".", 1)[0]


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
    node_id = str(node.get("id", "")).strip()
    if _is_code_entrypoint(node_id, node):
        fallback = str(node.get("summary", "")) or str(node.get("title", ""))
        return max(1, len(fallback) // 4)
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


def _content_for_output(node_id: str, node: dict[str, Any], scan_root: str, summary_fallback: str) -> str:
    if _is_code_entrypoint(node_id, node):
        return summary_fallback
    return _load_node_content(node_id, scan_root, summary_fallback)


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
            _with_node_context_fields(
                {
                    "id": clean_id,
                    "title": str(node.get("title", "")),
                    "priority": len(ordered) + 1,
                    "source": source,
                    "reason": reason,
                },
                node,
            )
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


DISCOVERY_REASON_CODES = {
    "shared_dependencies",
    "shared_links",
    "stale_but_related",
    "orphan_nearby",
    "recently_updated_neighbor",
    "same_type_same_project",
}


def _discovery_candidate(
    node_id: str,
    node_map: dict[str, dict[str, Any]],
    *,
    reason_code: str,
    score_parts: dict[str, float],
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    score = round(sum(float(value) for value in score_parts.values()), 3)
    reasons = {
        "shared_dependencies": "shares dependency or prerequisite edges with the main context",
        "shared_links": "near the main context through resolved links",
        "stale_but_related": "related context is stale enough to review before closing assumptions",
        "orphan_nearby": "orphaned node appears lexically near this task",
        "recently_updated_neighbor": "recently updated neighbor may contain fresh context",
        "same_type_same_project": "same type/project as a selected node but outside the main read path",
    }
    payload = {
        "id": node_id,
        "title": _node_title(node_map, node_id),
        "type": _node_type(node_map, node_id),
        "status": _node_status(node_map, node_id),
        "reason": reasons.get(reason_code, reason_code),
        "reason_code": reason_code,
        "score": score,
        "score_breakdown": {**{key: round(value, 3) for key, value in score_parts.items()}, "total": score},
        "evidence": evidence or [],
    }
    return _with_node_context_fields(payload, node_map.get(node_id, {}))


def _discovery_candidates(
    query: str,
    selected_nodes: list[dict[str, Any]],
    deferred_nodes: list[dict[str, Any]],
    db_path: str,
    node_map: dict[str, dict[str, Any]],
    *,
    excluded_ids: set[str] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    selected_ids = {str(item.get("id", "")).strip() for item in selected_nodes if str(item.get("id", "")).strip()}
    blocked_ids = set(selected_ids)
    if excluded_ids:
        blocked_ids.update(node_id for node_id in excluded_ids if node_id)
    if not selected_ids:
        return []

    candidates: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any]) -> None:
        node_id = str(item.get("id", "")).strip()
        if not node_id or node_id in blocked_ids:
            return
        if node_id not in node_map:
            return
        existing = candidates.get(node_id)
        if existing is None or float(item.get("score", 0.0) or 0.0) > float(existing.get("score", 0.0) or 0.0):
            candidates[node_id] = item

    for index, item in enumerate(deferred_nodes[:8], start=1):
        node_id = str(item.get("id", "")).strip()
        if not node_id:
            continue
        add(
            _discovery_candidate(
                node_id,
                node_map,
                reason_code="shared_links",
                score_parts={"graph_proximity": max(0.2, 1.2 - index * 0.1)},
                evidence=[str(item.get("reason", "")).strip()],
            )
        )

    selected_nodes_meta = [node_map.get(node_id, {}) for node_id in selected_ids]
    selected_type_project = {
        (
            str(node.get("type", "")).strip().lower(),
            str(node.get("project", "")).strip().lower(),
        )
        for node in selected_nodes_meta
    }
    for node_id, node in node_map.items():
        if node_id in blocked_ids:
            continue
        pair = (
            str(node.get("type", "")).strip().lower(),
            str(node.get("project", "")).strip().lower(),
        )
        if pair not in selected_type_project or not pair[0]:
            continue
        recency = _recency_score(str(node.get("updated", "")))
        if recency > 0:
            add(
                _discovery_candidate(
                    node_id,
                    node_map,
                    reason_code="recently_updated_neighbor",
                    score_parts={"same_type_project": 0.6, "recency": recency},
                    evidence=[f"type={pair[0]}", f"project={pair[1] or 'unknown'}"],
                )
            )
        else:
            add(
                _discovery_candidate(
                    node_id,
                    node_map,
                    reason_code="same_type_same_project",
                    score_parts={"same_type_project": 0.5},
                    evidence=[f"type={pair[0]}", f"project={pair[1] or 'unknown'}"],
                )
            )

    stale_ids = {str(item.get("id", "")).strip() for item in list_stale_nodes(db_path, days=30)}
    for edge in list_edges_for_nodes(db_path, selected_ids, resolved_only=True):
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        other = dst if src in selected_ids else src
        if not other or other in blocked_ids or other not in node_map:
            continue
        reason_code = "stale_but_related" if other in stale_ids else "shared_dependencies"
        edge_type = str(edge.get("type", "")).strip() or "links_to"
        edge_score = 0.9 if edge_type == "depends_on" else 0.7
        stale_score = 0.5 if other in stale_ids else 0.0
        add(
            _discovery_candidate(
                other,
                node_map,
                reason_code=reason_code,
                score_parts={"edge": edge_score, "stale": stale_score},
                evidence=[f"{edge_type}:{src}->{dst}"],
            )
        )

    query_terms = set(_query_keywords(query))
    for orphan in list_orphan_nodes(db_path):
        node_id = str(orphan.get("id", "")).strip()
        if not node_id or node_id in blocked_ids or node_id not in node_map:
            continue
        haystack = " ".join([_node_title(node_map, node_id), _node_summary(node_map, node_id), " ".join(_node_tags(node_map, node_id))]).lower()
        matched = sorted(term for term in query_terms if term and term in haystack)
        if not matched:
            continue
        add(
            _discovery_candidate(
                node_id,
                node_map,
                reason_code="orphan_nearby",
                score_parts={"orphan": 0.7, "lexical": min(1.0, len(matched) * 0.25)},
                evidence=matched[:4],
            )
        )

    ranked = sorted(candidates.values(), key=lambda item: (-float(item.get("score", 0.0) or 0.0), str(item.get("id", ""))))
    return ranked[: max(0, int(limit))]


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
    return any(marker in lowered for marker in TEST_PATH_MARKERS) or normalized.rsplit("/", 1)[-1].startswith("test_")


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
    node = node_map.get(node_id, {})
    return _with_node_context_fields(payload, node)


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
    discovery_candidates: list[dict[str, Any]],
    confidence: float,
    node_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    read_items: list[dict[str, Any]] = []
    artifact_items: list[dict[str, Any]] = []
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

        if _is_artifact_node(node):
            artifact_items.append(_node_brief(node_id, node_map, reason=reason, priority=index))
            continue

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
    relevant_artifacts = _unique_node_briefs(artifact_items, limit=6)
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
        "relevant_artifacts": relevant_artifacts,
        "relevant_task_history": relevant_task_history,
        "likely_code_entrypoints": likely_code_entrypoints,
        "known_guardrails": known_guardrails,
        "discovery_candidates": discovery_candidates,
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
        "relevant_artifacts": [],
        "relevant_task_history": [],
        "likely_code_entrypoints": [],
        "known_guardrails": [],
        "discovery_candidates": [],
        "suggested_rg": suggested_rg,
        "context_gaps": [reason],
    }


def zero_hits_field(query: str) -> dict[str, Any]:
    """Disclosure for a searched query that matched nothing.

    Field names (lanes_searched / lanes_inactive / caveat / remediation) are
    shared protocol vocabulary with cdex (cdex 51c9ffa, decisions/0003) and
    must stay aligned across both tools. Zero hits after a search claims this
    field; a blank query or a budget trim to zero does not.
    """
    terms = _query_keywords(query)
    if terms:
        pattern = "|".join(re.escape(term) for term in terms[:5])
        rg_hint = f'rg -n "{pattern}" .'
        cdex_terms = " ".join(terms[:5])
    else:
        rg_hint = "rg -n <term> ."
        cdex_terms = str(query).strip()
    return {
        "lanes_searched": ["metadata"],
        # Always-present map; {} would mean every known lane was searched.
        "lanes_inactive": {"body_text": "documented_non_goal"},
        "caveat": (
            "zero hits bounds only the metadata lane (title / tags / summary / "
            "search_terms) over the indexed corpus — it is not evidence that the "
            "document does not exist"
        ),
        "remediation": (
            f"for body text run: {rg_hint}; to make a document findable here, "
            "add the term to its frontmatter tags (docs/convention.md); "
            f'if cdex is available, cdex search "{cdex_terms}" covers code prior art'
        ),
    }


def project_actionable_digest(payload: dict[str, Any], digest: str) -> dict[str, Any]:
    if str(digest or "full").strip().lower() != "minimal":
        return payload
    return {key: payload.get(key, [] if key != "intent" else "") for key in MINIMAL_DIGEST_KEYS}


def build_agent_prompt_pack(query: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
    clean_role = str(role or "").strip().lower()
    if clean_role not in AGENT_PACK_ROLES:
        clean_role = "worker"

    read_order = [item for item in list(payload.get("recommended_read_order", []) or []) if isinstance(item, dict)]
    discovery = [item for item in list(payload.get("discovery_candidates", []) or []) if isinstance(item, dict)]
    digest = payload.get("actionable_digest") if isinstance(payload.get("actionable_digest"), dict) else {}
    role_instructions = {
        "worker": "Implement only the assigned slice, preserve existing contracts, and report changed files plus verification.",
        "reviewer": "Review for correctness, regressions, contract compatibility, missing tests, and risky omissions.",
        "commander": "Coordinate scope, decide read order, and split follow-up work into bounded tasks.",
    }
    required_reads = [
        {
            "id": str(item.get("id", "")),
            "reason": str(item.get("reason", "")),
            "source": str(item.get("source", "")),
            "index": str(item.get("index", "")),
        }
        for item in read_order[:6]
    ]
    side_reads = [
        {
            "id": str(item.get("id", "")),
            "reason_code": str(item.get("reason_code", "")),
            "reason": str(item.get("reason", "")),
            "index": str(item.get("index", "")),
        }
        for item in discovery[:3]
    ]
    suggested_rg = [item for item in list(digest.get("suggested_rg", []) or []) if isinstance(item, dict)]
    prompt_lines = [
        f"Role: {clean_role}",
        f"Task/query: {query.strip()}",
        role_instructions[clean_role],
        "Read the required_reads first, then inspect discovery_candidates only if they can change the decision.",
    ]
    return {
        "role": clean_role,
        "query": query.strip(),
        "instructions": role_instructions[clean_role],
        "required_reads": required_reads,
        "discovery_candidates": side_reads,
        "actionable_digest": digest,
        "suggested_rg": suggested_rg[:3],
        "prompt": "\n".join(prompt_lines),
    }


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
        _merge_synonyms(active_scoring, scoring_config)

    keywords = _expand_keywords_with_synonyms(
        _extract_keywords(query),
        active_scoring.get("synonyms", {}) if isinstance(active_scoring.get("synonyms"), dict) else {},
    )
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
        "discovery_candidates": [],
        "confidence": 0.0,
        "why_this_set": [],
        "budget_dropped_nodes": [],
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
                "discovery_candidates": [],
                "confidence": 0.0,
                "why_this_set": [],
                "budget_dropped_nodes": [],
            "zero_hits": zero_hits_field(query),
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
    graph_reason: dict[str, list[str]] = {}
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
            graph_reason.setdefault(dst, []).append(f"{edge_type}:{src}->{dst}")
            linked_ids.add(dst)
        if dst in seed_ids and src not in seed_ids:
            graph_boost[src] = graph_boost.get(src, 0.0) + boost
            graph_reason.setdefault(src, []).append(f"{edge_type}:{src}->{dst}")
            linked_ids.add(src)

    for linked_id in linked_ids:
        if linked_id in all_node_map:
            candidate_map[linked_id] = all_node_map[linked_id]

    scored_rows: list[tuple[float, str, dict[str, Any], dict[str, Any]]] = []
    recency_weight = float(active_scoring.get("recency_weight", DEFAULT_RECENCY_WEIGHT))
    for node_id, node in candidate_map.items():
        keyword_breakdown = _keyword_match_breakdown(node, keywords, scoring=active_scoring)
        path_symbol_breakdown = _path_symbol_breakdown(node_id, node, query, scoring=active_scoring)
        type_status_breakdown = _type_status_breakdown(node, scoring=active_scoring)
        recency_raw = _recency_score(str(node.get("updated", "")))
        recency = recency_raw * recency_weight
        graph = graph_boost.get(node_id, 0.0)
        total_score = (
            float(keyword_breakdown["total"])
            + float(path_symbol_breakdown["total"])
            + float(type_status_breakdown["total"])
            + recency
            + graph
        )
        score_breakdown = {
            "keyword": keyword_breakdown,
            "path_symbol": path_symbol_breakdown,
            "type_status": type_status_breakdown,
            "recency": round(recency, 3),
            "recency_raw": round(recency_raw, 3),
            "recency_weight": round(recency_weight, 3),
            "graph_boost": round(graph, 3),
            "graph_reason": graph_reason.get(node_id, []),
            "config_source": scoring_config_source,
            "total": round(total_score, 3),
        }
        scored_rows.append((total_score, node_id, node, score_breakdown))

    scored_rows.sort(key=lambda row: (-row[0], row[1]))

    scan_root = get_scan_root(db_path, default=".")
    selected_nodes: list[dict[str, Any]] = []
    budget_dropped_nodes: list[dict[str, Any]] = []
    total_tokens = 0
    soft_budget_multiplier = float(active_scoring.get("soft_budget_multiplier", DEFAULT_SOFT_BUDGET_MULTIPLIER))
    soft_cap = int(safe_budget * soft_budget_multiplier)

    for score, node_id, node, score_breakdown in scored_rows:
        if len(selected_nodes) >= safe_limit:
            break

        estimated_tokens = _estimated_tokens_for_node(node)
        projected = total_tokens + estimated_tokens

        if selected_nodes and projected > soft_cap:
            budget_dropped_nodes.append(
                {
                    "id": node_id,
                    "score": round(score, 3),
                    "estimated_tokens": estimated_tokens,
                    "budget_drop_reason": "soft_budget_exceeded",
                    "projected_tokens": projected,
                    "soft_cap": soft_cap,
                }
            )
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
                    "budget_drop_reason": None,
                },
            },
            "estimated_tokens": estimated_tokens,
        }
        _with_node_context_fields(row, node)
        if include_content:
            summary_fallback = str(node.get("summary", "")) or str(node.get("title", ""))
            row["content"] = _content_for_output(node_id, node, scan_root, summary_fallback)
        selected_nodes.append(row)
        total_tokens = projected

    payload = {
        "query": query,
        "nodes": selected_nodes,
        "total_tokens": total_tokens,
        "budget": safe_budget,
        "budget_dropped_nodes": budget_dropped_nodes[:10],
    }
    if not actionable:
        return payload

    node_map = all_node_map
    read_order = _read_order(selected_nodes, db_path, node_map)
    read_order_ids = {str(item.get("id", "")).strip() for item in read_order}
    deferred = _deferred_nodes(selected_nodes, db_path, read_order_ids)
    discovery = _discovery_candidates(query, selected_nodes, deferred, db_path, node_map, excluded_ids=read_order_ids)
    confidence = _confidence(selected_nodes)
    why_this_set = _why_this_set(selected_nodes, confidence, node_map)
    next_actions = _next_actions(query, read_order, confidence, node_map)
    next_actions_v2 = _next_actions_v2(next_actions)
    actionable_digest = _build_actionable_digest(
        query,
        selected_nodes,
        read_order,
        deferred,
        discovery,
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
            "discovery_candidates": discovery,
            "confidence": confidence,
            "why_this_set": why_this_set,
            "actionable_digest": actionable_digest,
        }
    )
    return payload
