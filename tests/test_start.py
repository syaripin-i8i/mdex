from __future__ import annotations

import pytest

from mdex import start


def test_build_start_payload_uses_context_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        start,
        "select_context",
        lambda *_args, **_kwargs: {
            "recommended_read_order": [{"id": "design/root.md"}],
            "recommended_next_actions": ["open design/root.md"],
            "recommended_next_actions_v2": [
                {"command": "open", "args": ["design/root.md"], "reason": "read first"}
            ],
            "deferred_nodes": [{"id": "notes/later.md"}],
            "confidence": 0.88,
            "why_this_set": ["dependency chain"],
            "actionable_digest": {
                "intent": "root decision",
                "relevant_docs": [{"id": "design/root.md"}],
                "relevant_task_history": [],
                "likely_code_entrypoints": [],
                "known_guardrails": [],
                "suggested_rg": [],
                "context_gaps": [],
            },
            "total_tokens": 123,
            "budget": 456,
            "nodes": [{"id": "design/root.md"}],
        },
    )
    monkeypatch.setattr(start, "get_index_metadata", lambda *_args, **_kwargs: "2026-04-19T00:00:00Z")

    payload = start.build_start_payload(
        "root decision",
        "tmp.db",
        db_source="arg",
        budget=500,
        limit=7,
        include_content=False,
    )

    assert payload["task"] == "root decision"
    assert payload["db"] == {"path": "tmp.db", "source": "arg"}
    assert payload["index_status"]["ready"] is True
    assert payload["index_status"]["generated"] == "2026-04-19T00:00:00Z"
    assert payload["index_status"]["fresh"] is False
    assert payload["entrypoint_reason"] == "stale_index_refresh_recommended"
    assert payload["recommended_read_order"][0]["id"] == "design/root.md"
    assert payload["recommended_next_actions_v2"][0]["command"] == "open"
    assert "run mdex scan" in payload["recommended_next_actions"]
    assert payload["actionable_digest"]["intent"] == "root decision"
    assert payload["confidence"] == 0.88
    assert payload["budget"] == 456


def test_build_start_payload_fallback_digest_is_schema_shaped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        start,
        "select_context",
        lambda *_args, **_kwargs: {
            "recommended_read_order": [],
            "recommended_next_actions": [],
            "recommended_next_actions_v2": [],
            "deferred_nodes": [],
            "confidence": 0.0,
            "why_this_set": [],
            "total_tokens": 0,
            "budget": 456,
            "nodes": [],
        },
    )
    monkeypatch.setattr(start, "get_index_metadata", lambda *_args, **_kwargs: "2026-04-19T00:00:00Z")

    payload = start.build_start_payload(
        "root decision",
        "tmp.db",
        db_source="arg",
        budget=500,
        limit=7,
        include_content=False,
        digest="minimal",
    )

    assert payload["actionable_digest"] == {
        "intent": "root decision",
        "relevant_docs": [],
        "suggested_rg": [],
        "context_gaps": ["select_context did not return actionable_digest"],
    }
