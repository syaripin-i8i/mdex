from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdex import finish, indexer
from mdex.dbresolve import RuntimeContext
from mdex.finish import FinishError, run_finish
from mdex.scan_manifest import build_scan_manifest, set_scan_manifest


def _context(tmp_path: Path) -> RuntimeContext:
    repo = tmp_path / "repo"
    repo.mkdir()
    return RuntimeContext(
        repo_root=repo,
        config_path=repo / ".mdex" / "config.json",
        config={},
    )


def _scan_metadata(
    context: RuntimeContext,
    db_path: Path,
    *,
    scan_roots: list[Path] | None = None,
    node_id_root: Path | None = None,
    output_path: Path | None = None,
    config_path: Path | None = None,
    config: dict[str, object] | None = None,
) -> dict[str, str]:
    roots = scan_roots or [context.repo_root.resolve()]
    id_root = node_id_root or context.repo_root.resolve()
    json_path = output_path or (context.repo_root / ".mdex" / "index.json")
    source_config = config_path or (context.repo_root / "control" / "scan_config.json")
    manifest = build_scan_manifest(
        repo_root=context.repo_root,
        scan_roots=roots,
        node_id_root=id_root,
        config_path=source_config,
        config=config or {},
        db_output=db_path,
        output_json=json_path,
        output_origin="config",
        index_kind="repo",
    )
    return {
        "scan_root": id_root.resolve().as_posix(),
        "scan_roots": json.dumps([path.resolve().as_posix() for path in roots]),
        "scan_manifest": json.dumps(manifest),
    }


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
    assert payload["suspicion_signals"] == {
        "suspiciously_unupdated": [],
        "likely_missing_links": [],
        "unreviewed_neighbors": [],
        "decision_gap_candidates": [],
    }


def test_run_finish_maps_impact_anomalies_to_suspicion_signals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    anomaly = {
        "id": "design/root.md",
        "score": 1.0,
        "reason": "isolated",
        "reason_code": "isolated_changes",
        "score_breakdown": {"total": 1.0},
    }
    monkeypatch.setattr(finish, "collect_changed_files", lambda *_args, **_kwargs: ["design/root.md"])
    monkeypatch.setattr(
        finish,
        "build_impact_report",
        lambda *_args, **_kwargs: {
            "inputs": ["design/root.md"],
            "read_first": [],
            "related_tasks": [],
            "decision_records": [],
            "stale_watch": [anomaly],
            "isolated_changes": [anomaly],
            "unusual_neighbors": [anomaly],
            "missing_decision_links": [anomaly],
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
        scan=False,
    )

    signals = payload["suspicion_signals"]
    assert signals["suspiciously_unupdated"] == [anomaly]
    assert signals["likely_missing_links"] == [anomaly]
    assert signals["unreviewed_neighbors"] == [anomaly]
    assert signals["decision_gap_candidates"] == [anomaly]


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
    monkeypatch.setattr(finish, "_prepare_scan", lambda *_args, **_kwargs: {})

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


@pytest.mark.parametrize("scope_kind", ["narrow", "other_repo"])
def test_prepare_scan_rejects_db_scope_that_does_not_match_current_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scope_kind: str,
) -> None:
    context = _context(tmp_path)
    if scope_kind == "narrow":
        db_root = context.repo_root / "docs"
    else:
        db_root = tmp_path / "other-repo"
    db_root.mkdir(parents=True)
    db_path = context.repo_root / ".mdex" / "index.db"
    metadata = _scan_metadata(context, db_path)
    metadata.update(
        {
            "scan_root": db_root.as_posix(),
            "scan_roots": json.dumps([db_root.as_posix()]),
        }
    )

    monkeypatch.setattr(
        finish,
        "list_index_metadata",
        lambda *_args, **_kwargs: metadata,
    )

    with pytest.raises(FinishError) as exc_info:
        finish._prepare_scan(context, str(db_path))

    payload = exc_info.value.payload
    assert payload["reason"] == "scan_scope_mismatch"
    assert payload["partial_update"] == {
        "occurred": False,
        "stage": "scan_preflight",
        "applied_enrichments": [],
    }
    assert payload["current_scan_roots"] == [context.repo_root.resolve().as_posix()]
    assert payload["db_scan_roots"] == [db_root.resolve().as_posix()]
    if scope_kind == "other_repo":
        assert payload["db_roots_outside_repo"]


@pytest.mark.parametrize("invalid_part", ["scan_root", "node_id_root"])
def test_prepare_scan_rejects_missing_or_non_directory_manifest_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_part: str,
) -> None:
    context = _context(tmp_path)
    db_path = context.repo_root / ".mdex" / "index.db"
    invalid_path = context.repo_root / "invalid-root"
    if invalid_part == "node_id_root":
        invalid_path.write_text("not a directory", encoding="utf-8")
        scan_roots = [context.repo_root]
        node_id_root = invalid_path
    else:
        scan_roots = [invalid_path]
        node_id_root = context.repo_root
    metadata = _scan_metadata(
        context,
        db_path,
        scan_roots=scan_roots,
        node_id_root=node_id_root,
    )
    monkeypatch.setattr(finish, "list_index_metadata", lambda *_args, **_kwargs: metadata)

    with pytest.raises(FinishError, match="scan failed") as exc_info:
        finish._prepare_scan(context, str(db_path))

    assert "missing or not a directory" in exc_info.value.payload["detail"]
    assert exc_info.value.payload["partial_update"]["occurred"] is False


def test_prepare_scan_rejects_output_outside_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    config_path = context.repo_root / "control" / "scan_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"scan_roots": ["."], "output_file": "../escaped.json"}),
        encoding="utf-8",
    )
    root = context.repo_root.resolve()
    db_path = context.repo_root / ".mdex" / "index.db"
    metadata = _scan_metadata(
        context,
        db_path,
        output_path=tmp_path / "escaped.json",
        config_path=config_path,
        config={"scan_roots": ["."], "output_file": "../escaped.json"},
    )
    monkeypatch.setattr(
        finish,
        "list_index_metadata",
        lambda *_args, **_kwargs: metadata,
    )

    with pytest.raises(ValueError, match="must stay within repo"):
        finish._prepare_scan(context, str(db_path))


def test_prepare_scan_rejects_identical_db_and_json_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    config_path = context.repo_root / "control" / "scan_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"scan_roots": ["."], "output_file": ".mdex/index.db"}),
        encoding="utf-8",
    )
    root = context.repo_root.resolve()
    db_path = context.repo_root / ".mdex" / "index.db"
    metadata = _scan_metadata(
        context,
        db_path,
        output_path=db_path,
        config_path=config_path,
        config={"scan_roots": ["."], "output_file": ".mdex/index.db"},
    )
    monkeypatch.setattr(
        finish,
        "list_index_metadata",
        lambda *_args, **_kwargs: metadata,
    )

    with pytest.raises(ValueError, match="database and JSON output paths must be different"):
        finish._prepare_scan(context, str(db_path))


def test_run_scan_updates_configured_json_output_with_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    config_path = context.repo_root / "control" / "scan_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"scan_roots": ["."], "output_file": ".mdex/custom_index.json"}),
        encoding="utf-8",
    )
    root = context.repo_root.resolve()
    index = {
        "generated": "now",
        "scan_root": root.as_posix(),
        "scan_roots": [root.as_posix()],
        "nodes": [{"id": "README.md"}],
        "edges": [],
        "warnings": [],
    }
    monkeypatch.setattr(finish, "build_index", lambda *_args, **_kwargs: index)
    output_calls: list[tuple[dict[str, object], str, str]] = []
    monkeypatch.setattr(
        finish,
        "write_scan_outputs",
        lambda value, db, output, **_kwargs: output_calls.append((value, db, output)),
    )

    result = finish._run_scan(
        context,
        "index.db",
        plan={
            "config": {"scan_roots": ["."], "output_file": ".mdex/custom_index.json"},
            "scan_roots": [root],
            "scan_root_warnings": [],
            "node_id_root": root,
            "config_path": config_path,
            "output_path": context.repo_root / ".mdex" / "custom_index.json",
            "output_origin": "config",
            "index_kind": "repo",
        },
    )

    expected_json = context.repo_root / ".mdex" / "custom_index.json"
    assert output_calls == [(index, "index.db", str(expected_json.resolve()))]
    assert "_scan_manifest" in index
    assert result["output"] == {"db": "index.db", "json": str(expected_json.resolve())}


def test_run_scan_rejects_stale_preflight_plan_without_overwriting_concurrent_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    root = context.repo_root.resolve()
    db_path = root / ".mdex" / "index.db"
    json_path = root / ".mdex" / "index.json"
    config_path = root / "control" / "scan_config.json"
    config = {"scan_roots": ["."], "output_file": ".mdex/index.json"}
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")

    def scan_index(generation: str) -> tuple[dict[str, object], dict[str, object]]:
        value: dict[str, object] = {
            "generated": generation,
            "scan_root": root.as_posix(),
            "scan_roots": [root.as_posix()],
            "nodes": [],
            "edges": [],
            "warnings": [],
        }
        manifest = build_scan_manifest(
            repo_root=root,
            scan_roots=[root],
            node_id_root=root,
            config_path=config_path,
            config=config,
            db_output=db_path,
            output_json=json_path,
            output_origin="config",
            index_kind="repo",
        )
        set_scan_manifest(value, manifest)
        return value, manifest

    index_a, manifest_a = scan_index("generation-a")
    indexer.write_scan_outputs(index_a, str(db_path), str(json_path))

    stale_plan = finish._prepare_scan(context, str(db_path))
    assert stale_plan["previous_manifest"]["scan_id"] == manifest_a["scan_id"]

    index_b, manifest_b = scan_index("generation-b")
    indexer.write_scan_outputs(index_b, str(db_path), str(json_path))
    metadata_b = finish.list_index_metadata(str(db_path))
    json_b = json.loads(json_path.read_text(encoding="utf-8"))

    stale_index, _stale_manifest = scan_index("stale-finish-generation")
    monkeypatch.setattr(finish, "build_index", lambda *_args, **_kwargs: stale_index)

    with pytest.raises(
        indexer.ScanOutputsWriteError,
        match="scan plan became stale before write",
    ) as exc_info:
        finish._run_scan(context, str(db_path), plan=stale_plan)

    assert exc_info.value.db_written is False
    assert exc_info.value.json_written is False
    assert finish.list_index_metadata(str(db_path)) == metadata_b
    assert json.loads(json_path.read_text(encoding="utf-8")) == json_b
    assert metadata_b["generated"] == "generation-b"
    assert metadata_b["scan_id"] == manifest_b["scan_id"]
    assert json_b["generated"] == "generation-b"
    assert json_b["scan_manifest"]["scan_id"] == manifest_b["scan_id"]


def test_run_finish_propagates_scan_failure_with_partial_enrich_detail(
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
    monkeypatch.setattr(finish, "list_nodes", lambda *_args, **_kwargs: [{"id": "design/root.md", "type": "design"}])
    monkeypatch.setattr(finish, "get_node", lambda *_args, **_kwargs: {"id": "design/root.md"})
    monkeypatch.setattr(
        finish,
        "enrich_node",
        lambda *_args, **_kwargs: {"status": "enriched", "id": "design/root.md"},
    )
    monkeypatch.setattr(finish, "_prepare_scan", lambda *_args, **_kwargs: {"prepared": True})
    monkeypatch.setattr(
        finish,
        "_run_scan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    with pytest.raises(FinishError) as exc_info:
        run_finish(
            task="task",
            db_path="tmp.db",
            db_source="arg",
            context=_context(tmp_path),
            changed_files_from_git=False,
            dry_run=False,
            summary_file=str(summary_file),
            scan=True,
        )

    payload = exc_info.value.payload
    assert payload["error"] == "scan failed"
    assert "after enrich updates were applied" in payload["detail"]
    assert "disk full" in payload["detail"]
    assert payload["partial_update"] == {
        "occurred": True,
        "stage": "scan_after_enrich",
        "applied_enrichments": [{"status": "enriched", "id": "design/root.md"}],
        "scan_outputs": {
            "db_written": False,
            "json_written": False,
            "db": None,
            "json": None,
        },
    }


def test_scan_json_failure_reports_partial_database_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    root = context.repo_root.resolve()
    output_path = root / ".mdex" / "index.json"
    index = {
        "generated": "now",
        "scan_root": root.as_posix(),
        "scan_roots": [root.as_posix()],
        "nodes": [],
        "edges": [],
        "warnings": [],
    }
    monkeypatch.setattr(finish, "build_index", lambda *_args, **_kwargs: index)
    monkeypatch.setattr(
        finish,
        "write_scan_outputs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            finish.ScanWriteError(
                "disk full",
                db_path=str(root / ".mdex" / "index.db"),
                json_path=str(output_path),
                db_written=True,
                json_written=False,
            )
        ),
    )

    with pytest.raises(finish.ScanWriteError) as exc_info:
        finish._run_scan(
            context,
            str(root / ".mdex" / "index.db"),
            plan={
                "config": {},
                "scan_roots": [root],
                "scan_root_warnings": [],
                "output_path": output_path,
            },
        )

    payload = finish._scan_failure(exc_info.value, applied_enrichments=[]).payload
    assert payload["partial_update"] == {
        "occurred": True,
        "stage": "scan_json",
        "applied_enrichments": [],
        "scan_outputs": {
            "db_written": True,
            "json_written": False,
            "db": str(root / ".mdex" / "index.db"),
            "json": str(output_path),
        },
    }
    assert "failed to update the JSON index" in payload["detail"]
