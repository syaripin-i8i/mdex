from __future__ import annotations

from typing import Any

from mdex.context import project_actionable_digest, select_context
from mdex.health import evaluate_index_health, evidence_identity_from_health, index_status_from_health


def build_start_payload(
    task: str,
    db_path: str,
    *,
    db_source: str,
    budget: int,
    limit: int,
    include_content: bool,
    digest: str = "full",
    scoring_config: dict[str, Any] | None = None,
    scoring_config_source: str = "defaults",
    stale_after_hours: int = 24,
    health: dict[str, Any] | None = None,
    borrowed: bool = False,
) -> dict[str, Any]:
    official_health = health or evaluate_index_health(
        db_path,
        source=db_source,
        borrowed=borrowed,
        stale_after_hours=stale_after_hours,
    )
    index_status = index_status_from_health(official_health)

    context_payload = select_context(
        task,
        db_path,
        budget=budget,
        limit=limit,
        include_content=include_content,
        actionable=True,
        digest=digest,
        scoring_config=scoring_config,
        scoring_config_source=scoring_config_source,
    )
    recommended_next_actions = list(context_payload.get("recommended_next_actions", []))
    recommended_next_actions_v2 = list(context_payload.get("recommended_next_actions_v2", []))

    if not bool(official_health.get("reusable", False)):
        _append_scan_action(recommended_next_actions, recommended_next_actions_v2)

    recommended_read_order = list(context_payload.get("recommended_read_order", []))
    if not bool(official_health.get("reusable", False)):
        recommended_read_order = [
            {
                **item,
                "evidence_use": "unverified_non_reusable",
                "health_reason": str(official_health.get("reason", "health_unavailable")),
            }
            for item in recommended_read_order
            if isinstance(item, dict)
        ]
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
        "health": official_health,
        "evidence_identity": evidence_identity_from_health(official_health),
        "index_status": index_status,
        "entrypoint_reason": entrypoint_reason,
        "recommended_read_order": recommended_read_order,
        "recommended_next_actions": recommended_next_actions,
        "recommended_next_actions_v2": recommended_next_actions_v2,
        "deferred_nodes": context_payload.get("deferred_nodes", []),
        "discovery_candidates": context_payload.get("discovery_candidates", []),
        "confidence": confidence,
        "why_this_set": list(context_payload.get("why_this_set", []))
        + (
            ["ranked candidates are unverified because index evidence is not reusable"]
            if not bool(official_health.get("reusable", False))
            else []
        ),
        "actionable_digest": context_payload.get("actionable_digest")
        or project_actionable_digest(_fallback_actionable_digest(task), digest),
        "total_tokens": int(context_payload.get("total_tokens", 0) or 0),
        "budget": int(context_payload.get("budget", budget) or budget),
        "budget_dropped_nodes": context_payload.get("budget_dropped_nodes", []),
        "nodes": context_payload.get("nodes", []),
    }
    return payload


def _entrypoint_reason(
    *,
    recommended_read_order: list[dict[str, Any]],
    confidence: float,
    index_status: dict[str, Any],
) -> str:
    has_read_order = bool(recommended_read_order)
    is_stale = not bool(index_status.get("fresh", False))

    if not has_read_order and is_stale:
        return "no_recommended_read_order_and_index_not_reusable"
    if not has_read_order:
        return "no_recommended_read_order"
    if confidence < 0.6 and is_stale:
        return "low_confidence_and_index_not_reusable"
    if confidence < 0.6:
        return "low_confidence_candidates"
    if is_stale:
        return "index_not_reusable"
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
            "reason": "refresh non-reusable index evidence before deciding entrypoint",
        }
    )


def _fallback_actionable_digest(task: str) -> dict[str, Any]:
    return {
        "intent": task.strip(),
        "relevant_docs": [],
        "relevant_artifacts": [],
        "relevant_task_history": [],
        "likely_code_entrypoints": [],
        "known_guardrails": [],
        "suggested_rg": [],
        "context_gaps": ["select_context did not return actionable_digest"],
    }
