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
            "deferred_nodes": [{"id": "notes/later.md"}],
            "confidence": 0.88,
            "why_this_set": ["dependency chain"],
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
    assert payload["recommended_read_order"][0]["id"] == "design/root.md"
    assert payload["confidence"] == 0.88
    assert payload["budget"] == 456
