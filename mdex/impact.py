from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mdex.store import list_nodes, list_stale_nodes


@dataclass
class _ScoredNode:
    node_id: str
    score: float
    reasons: list[str]


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def _basename(path: str) -> str:
    return Path(path).name.lower()


def _stem(path: str) -> str:
    return Path(path).stem.lower()


def _shared_segments(a: str, b: str) -> int:
    a_parts = {part.lower() for part in Path(a).parts if part and part != "."}
    b_parts = {part.lower() for part in Path(b).parts if part and part != "."}
    return len(a_parts.intersection(b_parts))


def _is_task_node(node: dict[str, Any], node_id: str) -> bool:
    node_type = str(node.get("type", "")).strip().lower()
    if node_type == "task":
        return True
    lowered = node_id.lower()
    return lowered.startswith("tasks/") or "/tasks/" in lowered


def _is_decision_node(node: dict[str, Any], node_id: str) -> bool:
    node_type = str(node.get("type", "")).strip().lower()
    if node_type == "decision":
        return True
    lowered = node_id.lower()
    return lowered.startswith("decision/") or lowered.startswith("decisions/")


def _is_stale(node_id: str, stale_ids: set[str]) -> bool:
    return node_id in stale_ids


def _reason_text(reasons: list[str]) -> str:
    if not reasons:
        return "path proximity"
    return "; ".join(reasons[:3])


def _score_node_against_changed(node: dict[str, Any], changed_paths: list[str]) -> _ScoredNode | None:
    node_id = str(node.get("id", "")).strip()
    if not node_id:
        return None

    node_id_lower = node_id.lower()
    node_stem = _stem(node_id_lower)
    node_summary = str(node.get("summary", "")).lower()
    node_title = str(node.get("title", "")).lower()
    refs = {
        _normalize_path(str(item)).lower()
        for key in ("links_to", "depends_on", "relates_to")
        for item in node.get(key, [])
        if str(item).strip()
    }

    score = 0.0
    reasons: list[str] = []
    for changed in changed_paths:
        changed_lower = changed.lower()
        changed_base = _basename(changed_lower)
        changed_stem = _stem(changed_lower)

        if node_id_lower == changed_lower:
            score += 6.0
            reasons.append("exact path match")
        elif node_id_lower.endswith(changed_lower) or changed_lower.endswith(node_id_lower):
            score += 3.5
            reasons.append("path suffix match")

        if changed_stem and node_stem and changed_stem == node_stem:
            score += 2.4
            reasons.append("same stem")

        shared = _shared_segments(node_id_lower, changed_lower)
        if shared > 0:
            score += min(1.6, shared * 0.4)
            reasons.append("shared directory segment")

        if changed_lower in refs or changed_base in {Path(ref).name.lower() for ref in refs}:
            score += 3.0
            reasons.append("direct path reference")

        if changed_base and (changed_base in node_summary or changed_base in node_title):
            score += 1.6
            reasons.append("path token in summary/title")

    if score <= 0:
        return None
    return _ScoredNode(node_id=node_id, score=score, reasons=reasons)


def _dedupe_reasons(items: list[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def build_impact_report(db_path: str, changed_paths: list[str], *, limit: int = 10) -> dict[str, Any]:
    normalized_inputs = [_normalize_path(path) for path in changed_paths if _normalize_path(path)]
    nodes = list_nodes(db_path)
    stale_ids = {str(row.get("id", "")) for row in list_stale_nodes(db_path, days=30)}
    node_map = {str(node.get("id", "")): node for node in nodes if str(node.get("id", "")).strip()}

    scored: dict[str, _ScoredNode] = {}
    for node in nodes:
        row = _score_node_against_changed(node, normalized_inputs)
        if row is None:
            continue
        prior = scored.get(row.node_id)
        if prior is None or row.score > prior.score:
            scored[row.node_id] = row
        else:
            merged_reasons = _dedupe_reasons(prior.reasons + row.reasons)
            scored[row.node_id] = _ScoredNode(
                node_id=prior.node_id,
                score=prior.score,
                reasons=merged_reasons,
            )

    for scored_node in list(scored.values()):
        source = node_map.get(scored_node.node_id, {})
        for key in ("links_to", "depends_on", "relates_to"):
            for target in source.get(key, []):
                target_id = str(target).strip()
                if target_id not in node_map:
                    continue
                target_node = node_map[target_id]
                if not (_is_task_node(target_node, target_id) or _is_decision_node(target_node, target_id)):
                    continue
                prior = scored.get(target_id)
                boost = 1.1
                reasons = ["linked from impacted design"]
                if prior is None:
                    scored[target_id] = _ScoredNode(target_id, boost, reasons)
                    continue
                merged_reasons = _dedupe_reasons(prior.reasons + reasons)
                scored[target_id] = _ScoredNode(target_id, prior.score + boost, merged_reasons)

    ranked = sorted(
        scored.values(),
        key=lambda row: (-row.score, row.node_id),
    )

    read_first: list[dict[str, Any]] = []
    related_tasks: list[dict[str, Any]] = []
    decision_records: list[dict[str, Any]] = []
    stale_watch: list[dict[str, Any]] = []

    for row in ranked:
        node = node_map.get(row.node_id, {})
        entry = {
            "id": row.node_id,
            "reason": _reason_text(_dedupe_reasons(row.reasons)),
            "score": round(row.score, 3),
        }
        if _is_task_node(node, row.node_id):
            related_tasks.append(entry)
        elif _is_decision_node(node, row.node_id):
            decision_records.append(entry)
        else:
            read_first.append(entry)

        if _is_stale(row.node_id, stale_ids):
            stale_reason = _dedupe_reasons(row.reasons + ["stale summary"])
            stale_watch.append(
                {
                    "id": row.node_id,
                    "reason": _reason_text(stale_reason),
                    "score": round(row.score + 0.8, 3),
                }
            )

    safe_limit = max(1, int(limit))
    return {
        "inputs": normalized_inputs,
        "read_first": read_first[:safe_limit],
        "related_tasks": related_tasks[:safe_limit],
        "decision_records": decision_records[:safe_limit],
        "stale_watch": stale_watch[:safe_limit],
    }
