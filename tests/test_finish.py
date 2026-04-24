from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdex import finish
from mdex.dbresolve import RuntimeContext
from mdex.finish import FinishError, run_finish


def _context(tmp_path: Path) -> RuntimeContext:
    repo = tmp_path / "repo"
    repo.mkdir()
    return RuntimeContext(
        repo_root=repo,
        config_path=repo / ".mdex" / "config.json",
        config={},
    )


def test_read_summary_file_validates_existence_and_content(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    with pytest.raises(FinishError, match="summary file not found"):
        finish._read_summary_file(str(missing))

    blank = tmp_path / "blank.txt"
    blank.write_text("   \n", encoding="utf-8")
    with pytest.raises(FinishError, match="summary is required"):
        finish._read_summary_file(str(blank))

    summary = tmp_path / "summary.txt"
    summary.write_text("  useful summary  \n", encoding="utf-8")
    assert finish._read_summary_file(str(summary)) == "useful summary"


def test_candidate_rows_and_primary_selection() -> None:
    impact_payload = {
        "read_first": [
            {"id": "design/root.md", "score": 9.0, "reason": "exact path match"},
            {"id": "notes/nearby.md", "score": 3.0, "reason": "path suffix match"},
        ],
        "stale_watch": [
            {"id": "design/root.md", "score": 8.0, "reason": "stale summary"},
            {"id": "decision/a.md", "score": 2.0, "reason": "direct path reference"},
        ],
    }
    ranked = finish._candidate_rows(impact_payload)
    assert [row["id"] for row in ranked] == ["design/root.md", "notes/nearby.md", "decision/a.md"]

    node_map = {
        "design/root.md": {"id": "design/root.md", "type": "design"},
        "notes/nearby.md": {"id": "notes/nearby.md", "type": "reference"},
        "decision/a.md": {"id": "decision/a.md", "type": "decision"},
    }
    primary = finish._primary_ids(ranked, changed_paths=["design/root.py"], node_map=node_map)
    assert "design/root.md" in primary
    assert finish._has_stem_match(["src/root.py"], "design/root.md") is True


def test_build_enrich_candidates_marks_primary_and_secondary() -> None:
    impact_payload = {
        "read_first": [
            {"id": "design/root.md", "score": 10.0, "reason": "exact path match"},
            {"id": "notes/other.md", "score": 4.0, "reason": "path token in summary/title"},
        ],
        "stale_watch": [],
    }
    candidates, primary_ids = finish._build_enrich_candidates(
        impact_payload,
        changed_paths=["design/root.py"],
        node_map={
            "design/root.md": {"id": "design/root.md", "type": "design"},
            "notes/other.md": {"id": "notes/other.md", "type": "reference"},
        },
    )
    assert candidates[0]["kind"] == "primary"
    assert candidates[1]["kind"] == "secondary"
    assert primary_ids == ["design/root.md"]


def test_scan_helpers_handle_missing_and_non_object_config(tmp_path: Path) -> None:
    missing = finish._load_scan_config(tmp_path / "missing.json")
    assert missing == {}

    bad = tmp_path / "scan.json"
    bad.write_text(json.dumps(["not-object"]), encoding="utf-8")
    assert finish._load_scan_config(bad) == {}

    summary = finish._scan_summary({"generated": "now", "nodes": [{"id": "x"}], "edges": [{"a": 1}]})
    assert summary == {"generated": "now", "nodes": 1, "edges": 1}


def test_next_actions_covers_manual_and_auto_paths() -> None:
    auto_actions = finish._next_actions(
        "task-a",
        ["design/root.md"],
        [{"id": "design/root.md", "kind": "primary", "reason": "exact path match", "score": 9.0}],
        False,
    )
    assert any("prepare summary text" in action for action in auto_actions)

    manual_actions = finish._next_actions(
        "task-b",
        [],
        [{"id": "design/root.md", "kind": "secondary", "reason": "path suffix match", "score": 4.0}],
        True,
    )
    assert any("run mdex enrich" in action for action in manual_actions)
    assert any("run mdex finish --changed-files-from-git" in action for action in manual_actions)


def test_run_finish_raises_when_git_repo_required_and_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(finish, "collect_changed_files", lambda *_args, **_kwargs: (_ for _ in ()).throw(finish.GitError("no git")))
    with pytest.raises(FinishError, match="not a git repository"):
        run_finish(
            task="task",
            db_path="tmp.db",
            db_source="arg",
            context=_context(tmp_path),
            changed_files_from_git=True,
            dry_run=True,
            summary_file=None,
            scan=False,
        )


def test_run_finish_dry_run_keeps_scan_not_ran(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(finish, "collect_changed_files", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        finish,
        "build_impact_report",
        lambda *_args, **_kwargs: {
            "inputs": [],
            "read_first": [],
            "related_tasks": [],
            "decision_records": [],
            "stale_watch": [],
        },
    )
    monkeypatch.setattr(finish, "list_nodes", lambda *_args, **_kwargs: [])

    payload = run_finish(
        task="task",
        db_path="tmp.db",
        db_source="arg",
        context=_context(tmp_path),
        changed_files_from_git=False,
        dry_run=True,
        summary_file=None,
        scan=True,
    )
    assert payload["dry_run"] is True
    assert payload["status"] == "success"
    assert payload["noop"] is True
    assert "no changed files" in payload["noop_reason"]
    assert payload["scan"]["requested"] is True
    assert payload["scan"]["ran"] is False
    assert payload["requires_manual_targeting"] is False
    assert payload["changed_files"] == []


def test_run_finish_sets_manual_targeting_for_multiple_primaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary_file = tmp_path / "summary.txt"
    summary_file.write_text("summary text", encoding="utf-8")

    monkeypatch.setattr(finish, "collect_changed_files", lambda *_args, **_kwargs: ["design/root.md"])
    monkeypatch.setattr(
        finish,
        "build_impact_report",
        lambda *_args, **_kwargs: {
            "inputs": ["design/root.md"],
            "read_first": [
                {"id": "design/root.md", "score": 9.0, "reason": "exact path match"},
                {"id": "notes/root.md", "score": 8.0, "reason": "exact path match"},
            ],
            "related_tasks": [],
            "decision_records": [],
            "stale_watch": [],
        },
    )
    monkeypatch.setattr(
        finish,
        "list_nodes",
        lambda *_args, **_kwargs: [
            {"id": "design/root.md", "type": "design"},
            {"id": "notes/root.md", "type": "reference"},
        ],
    )
    enrich_calls: list[str] = []
    monkeypatch.setattr(
        finish,
        "enrich_node",
        lambda *_args, **_kwargs: enrich_calls.append("called") or {"status": "enriched"},
    )

    payload = run_finish(
        task="task",
        db_path="tmp.db",
        db_source="arg",
        context=_context(tmp_path),
        changed_files_from_git=False,
        dry_run=False,
        summary_file=str(summary_file),
        scan=False,
    )

    assert payload["requires_manual_targeting"] is True
    assert payload["status"] == "success"
    assert payload["noop"] is False
    assert payload["applied_enrichments"] == []
    assert enrich_calls == []


def test_run_finish_applies_enrich_and_runs_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary_file = tmp_path / "summary.txt"
    summary_file.write_text("summary text", encoding="utf-8")

    monkeypatch.setattr(finish, "collect_changed_files", lambda *_args, **_kwargs: ["design/root.md"])
    monkeypatch.setattr(
        finish,
        "build_impact_report",
        lambda *_args, **_kwargs: {
            "inputs": ["design/root.md"],
            "read_first": [{"id": "design/root.md", "score": 9.0, "reason": "exact path match"}],
            "related_tasks": [],
            "decision_records": [],
            "stale_watch": [],
        },
    )
    monkeypatch.setattr(
        finish,
        "list_nodes",
        lambda *_args, **_kwargs: [{"id": "design/root.md", "type": "design"}],
    )
    monkeypatch.setattr(finish, "get_node", lambda *_args, **_kwargs: {"id": "design/root.md"})
    monkeypatch.setattr(
        finish,
        "enrich_node",
        lambda *_args, **_kwargs: {"status": "enriched", "id": "design/root.md"},
    )
    monkeypatch.setattr(
        finish,
        "_run_scan",
        lambda *_args, **_kwargs: {"generated": "now", "nodes": 1, "edges": 0},
    )

    payload = run_finish(
        task="task",
        db_path="tmp.db",
        db_source="arg",
        context=_context(tmp_path),
        changed_files_from_git=False,
        dry_run=False,
        summary_file=str(summary_file),
        scan=True,
    )
    assert payload["status"] == "success"
    assert payload["noop"] is False
    assert payload["requires_manual_targeting"] is False
    assert payload["applied_enrichments"][0]["status"] == "enriched"
    assert payload["scan"]["ran"] is True
    assert payload["scan"]["result"]["nodes"] == 1


def test_run_finish_raises_when_enrich_returns_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary_file = tmp_path / "summary.txt"
    summary_file.write_text("summary text", encoding="utf-8")

    monkeypatch.setattr(finish, "collect_changed_files", lambda *_args, **_kwargs: ["design/root.md"])
    monkeypatch.setattr(
        finish,
        "build_impact_report",
        lambda *_args, **_kwargs: {
            "inputs": ["design/root.md"],
            "read_first": [{"id": "design/root.md", "score": 9.0, "reason": "exact path match"}],
            "related_tasks": [],
            "decision_records": [],
            "stale_watch": [],
        },
    )
    monkeypatch.setattr(
        finish,
        "list_nodes",
        lambda *_args, **_kwargs: [{"id": "design/root.md", "type": "design"}],
    )
    monkeypatch.setattr(finish, "get_node", lambda *_args, **_kwargs: {"id": "design/root.md"})
    monkeypatch.setattr(
        finish,
        "enrich_node",
        lambda *_args, **_kwargs: {"status": "error", "error": "boom"},
    )

    with pytest.raises(FinishError, match="enrich failed"):
        run_finish(
            task="task",
            db_path="tmp.db",
            db_source="arg",
            context=_context(tmp_path),
            changed_files_from_git=False,
            dry_run=False,
            summary_file=str(summary_file),
            scan=False,
        )
