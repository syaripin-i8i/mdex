from __future__ import annotations

import pytest

from mdex import impact


def test_build_impact_report_classifies_nodes_and_stale_watch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "root.py").write_text("print('root')\n", encoding="utf-8")
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

    report = impact.build_impact_report("ignored.db", ["./design/root.py"], limit=10, repo_root=tmp_path)
    assert report["inputs"] == [{"path": "design/root.py", "exists": True, "indexed": False}]
    assert report["warnings"] == []
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


def test_build_impact_report_annotates_missing_and_indexed_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "live.md").write_text("# Live\n", encoding="utf-8")
    nodes = [
        {
            "id": "docs/live.md",
            "type": "design",
            "title": "Live",
            "summary": "",
            "links_to": [],
            "depends_on": [],
            "relates_to": [],
        },
        {
            "id": "docs/indexed_only.md",
            "type": "design",
            "title": "Indexed Only",
            "summary": "",
            "links_to": [],
            "depends_on": [],
            "relates_to": [],
        },
    ]
    monkeypatch.setattr(impact, "list_nodes", lambda _db: nodes)
    monkeypatch.setattr(impact, "list_stale_nodes", lambda _db, days=30: [])

    report = impact.build_impact_report(
        "ignored.db",
        ["docs/live.md", str(tmp_path / "docs" / "live.md"), "docs/indexed_only.md", "docs/nope.md"],
        repo_root=tmp_path,
    )

    absolute_live_path = str(tmp_path / "docs" / "live.md").replace("\\", "/")
    assert report["inputs"] == [
        {"path": "docs/live.md", "exists": True, "indexed": True},
        {"path": absolute_live_path, "exists": True, "indexed": False},
        {"path": "docs/indexed_only.md", "exists": False, "indexed": True},
        {"path": "docs/nope.md", "exists": False, "indexed": False},
    ]
    assert report["warnings"] == [
        {
            "code": "input_not_found",
            "path": "docs/nope.md",
            "message": "input path does not exist on disk and is not indexed",
        }
    ]
