from __future__ import annotations

import pytest

from mdex import impact


def test_build_impact_report_classifies_nodes_and_stale_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [
        {
            "id": "design/root.md",
            "type": "design",
            "title": "Root Design",
            "summary": "Touches root.py for architecture constraints.",
            "links_to": ["tasks/pending/T1.md", "decision/a.md"],
            "depends_on": [],
            "relates_to": [],
        },
        {
            "id": "tasks/pending/T1.md",
            "type": "task",
            "title": "Follow-up task",
            "summary": "task details",
            "links_to": [],
            "depends_on": [],
            "relates_to": [],
        },
        {
            "id": "decision/a.md",
            "type": "decision",
            "title": "Decision A",
            "summary": "decision details",
            "links_to": [],
            "depends_on": [],
            "relates_to": [],
        },
        {
            "id": "notes/reference.md",
            "type": "reference",
            "title": "Reference note",
            "summary": "extra context",
            "links_to": [],
            "depends_on": [],
            "relates_to": [],
        },
    ]

    monkeypatch.setattr(impact, "list_nodes", lambda _db: nodes)
    monkeypatch.setattr(
        impact,
        "list_stale_nodes",
        lambda _db, days=30: [{"id": "design/root.md"}, {"id": "decision/a.md"}],
    )

    report = impact.build_impact_report("ignored.db", ["./design/root.py"], limit=10)
    assert report["inputs"] == ["design/root.py"]
    assert report["read_first"]
    assert any(row["id"] == "design/root.md" for row in report["read_first"])
    assert any(row["id"] == "tasks/pending/T1.md" for row in report["related_tasks"])
    assert any(row["id"] == "decision/a.md" for row in report["decision_records"])
    assert any(row["id"] == "design/root.md" for row in report["stale_watch"])
    assert any("stale summary" in row["reason"] for row in report["stale_watch"])


def test_build_impact_report_enforces_min_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        impact,
        "list_nodes",
        lambda _db: [
            {
                "id": "design/root.md",
                "type": "design",
                "title": "Root Design",
                "summary": "",
                "links_to": [],
                "depends_on": [],
                "relates_to": [],
            },
            {
                "id": "design/other.md",
                "type": "design",
                "title": "Other",
                "summary": "",
                "links_to": [],
                "depends_on": [],
                "relates_to": [],
            },
        ],
    )
    monkeypatch.setattr(impact, "list_stale_nodes", lambda _db, days=30: [])

    report = impact.build_impact_report("ignored.db", ["design/root.md"], limit=0)
    assert len(report["read_first"]) == 1
