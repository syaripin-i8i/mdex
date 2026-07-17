from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from mdex import doctor
from mdex.builder import build_index
from mdex.doctor import build_doctor_report
from mdex.indexer import write_json, write_sqlite


def _check(report: dict[str, object], name: str) -> dict[str, object]:
    checks = report.get("checks", [])
    assert isinstance(checks, list)
    for check in checks:
        assert isinstance(check, dict)
        if check.get("name") == name:
            return check
    raise AssertionError(f"missing doctor check: {name}")


def test_doctor_reports_scan_warnings_and_indexed_hygiene(tmp_path: Path) -> None:
    repo = tmp_path / "doctor_repo"
    repo.mkdir()
    (repo / "old").mkdir()
    (repo / "old" / "note.md").write_text("# Old Note\n", encoding="utf-8")
    (repo / "fixtures").mkdir()
    (repo / "fixtures" / "case.md").write_text("# Fixture Case\n", encoding="utf-8")
    (repo / "eval").mkdir()
    (repo / "eval" / "case.json").write_text('{"message":"eval"}\n', encoding="utf-8")
    (repo / "settings.local.json").write_text('{"message":"local"}\n', encoding="utf-8")

    config = {
        "include_extensions": [".md", ".json"],
        "exclude_patterns": [],
        "use_default_exclude_patterns": False,
    }
    index = build_index(str(repo), config)
    db_path = tmp_path / "doctor.db"
    json_path = tmp_path / "doctor.json"
    write_sqlite(index, str(db_path))
    write_json(index, str(json_path))

    report = build_doctor_report(str(db_path), repo_root=repo, json_index_path=json_path)

    assert report["status"] == "warning"
    scan_warnings = _check(report, "scan_warnings")
    indexed_hygiene = _check(report, "indexed_path_hygiene")
    assert any(
        "settings.local.json" == finding.get("path")
        for finding in scan_warnings.get("findings", [])
        if isinstance(finding, dict)
    )
    hygiene_paths = {
        str(finding.get("path", ""))
        for finding in indexed_hygiene.get("findings", [])
        if isinstance(finding, dict)
    }
    assert "settings.local.json" in hygiene_paths
    assert "old/note.md" in hygiene_paths
    assert "fixtures/case.md" in hygiene_paths
    assert "eval/case.json" in hygiene_paths


def test_doctor_reports_orphan_overrides_and_json_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "doctor_mismatch_repo"
    repo.mkdir()
    (repo / "keep.md").write_text("# Keep\n", encoding="utf-8")
    config = {"include_extensions": [".md"], "exclude_patterns": []}
    index = build_index(str(repo), config)
    db_path = tmp_path / "doctor_mismatch.db"
    json_path = tmp_path / "doctor_mismatch.json"
    write_sqlite(index, str(db_path))
    write_json({**index, "generated": "2000-01-01T00:00:00+00:00"}, str(json_path))

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO node_overrides (id, summary, summary_source, summary_updated)
            VALUES (?, ?, ?, ?)
            """,
            ("missing.md", "stale", "agent", "2000-01-01T00:00:00+00:00"),
        )
        conn.commit()

    report = build_doctor_report(str(db_path), repo_root=repo, json_index_path=json_path)

    assert report["status"] == "warning"
    orphan_overrides = _check(report, "orphan_overrides")
    json_sync = _check(report, "json_sqlite_sync")
    assert any(
        finding.get("path") == "missing.md"
        for finding in orphan_overrides.get("findings", [])
        if isinstance(finding, dict)
    )
    assert json_sync["status"] == "warning"


def test_doctor_reports_unresolved_links_as_info(tmp_path: Path) -> None:
    repo = tmp_path / "doctor_unresolved_repo"
    repo.mkdir()
    (repo / "a.md").write_text("# A\n\n[[ghost]]\n", encoding="utf-8")
    (repo / "b.md").write_text("# B\n\n[[ghost]]\n", encoding="utf-8")
    config = {"include_extensions": [".md"], "exclude_patterns": []}
    db_path = tmp_path / "doctor_unresolved.db"
    write_sqlite(build_index(str(repo), config), str(db_path))

    report = build_doctor_report(str(db_path), repo_root=repo)
    unresolved = _check(report, "unresolved_links")

    assert report["status"] == "warning"
    git_check = _check(report, "indexed_untracked_files")
    assert git_check["findings"][0]["reason"] == "git_state_unavailable"
    assert unresolved["status"] == "info"
    assert report["summary"]["info"] == 1
    finding = unresolved["findings"][0]
    assert finding["severity"] == "info"
    assert finding["path"] == "ghost.md"
    assert finding["count"] == 2
    assert finding["referenced_by"] == ["a.md", "b.md"]


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_git_decode_failure_is_reported_as_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    decode_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(decode_error),
    )
    state = doctor._git_index_state(tmp_path)
    assert state["status"] == "unavailable"
    assert "invalid" in state["detail"]


def test_doctor_detects_untracked_and_generated_indexed_paths(tmp_path: Path) -> None:
    repo = tmp_path / "git_repo"
    (repo / "generated").mkdir(parents=True)
    (repo / "tracked.md").write_text("# Tracked\n", encoding="utf-8")
    (repo / "generated" / "result.md").write_text("# Generated\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "tracked.md")

    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "use_default_exclude_patterns": True,
    }
    db_path = tmp_path / "doctor_git.db"
    write_sqlite(build_index(repo, config), str(db_path))
    report = build_doctor_report(str(db_path), repo_root=repo)

    untracked = _check(report, "indexed_untracked_files")
    assert any(
        finding.get("path") == "generated/result.md"
        and finding.get("reason") == "indexed_file_untracked"
        for finding in untracked["findings"]
    )
    generated = _check(report, "generated_paths")
    assert any(
        finding.get("path") == "generated/result.md"
        and finding.get("reason") == "generated_path_indexed"
        for finding in generated["findings"]
    )


def test_doctor_detects_git_ignored_file_as_untracked(tmp_path: Path) -> None:
    repo = tmp_path / "ignored_repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (repo / "ignored.md").write_text("# Ignored but indexed\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".gitignore")
    db_path = tmp_path / "doctor_ignored.db"
    write_sqlite(
        build_index(repo, {"include_extensions": [".md"], "exclude_patterns": []}),
        str(db_path),
    )

    report = build_doctor_report(str(db_path), repo_root=repo)
    findings = _check(report, "indexed_untracked_files")["findings"]
    assert any(
        finding.get("path") == "ignored.md"
        and finding.get("reason") == "indexed_file_untracked"
        for finding in findings
    )


def test_doctor_maps_tracked_paths_from_node_id_root(tmp_path: Path) -> None:
    repo = tmp_path / "nested_root_repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "a.md").write_text("# Tracked nested doc\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "docs/a.md")
    db_path = tmp_path / "doctor_nested_root.db"
    write_sqlite(
        build_index(repo / "docs", {"include_extensions": [".md"], "exclude_patterns": []}),
        str(db_path),
    )

    report = build_doctor_report(str(db_path), repo_root=repo)
    findings = _check(report, "indexed_untracked_files")["findings"]
    assert not any(
        finding.get("path") == "a.md"
        and finding.get("reason") == "indexed_file_untracked"
        for finding in findings
    )


def test_doctor_policy_distinguishes_allowlisted_and_excluded_paths(tmp_path: Path) -> None:
    repo = tmp_path / "policy_repo"
    (repo / "generated").mkdir(parents=True)
    (repo / "excluded").mkdir()
    (repo / "generated" / "allowed.md").write_text("# Allowed\n", encoding="utf-8")
    (repo / "excluded" / "skip.md").write_text("# Skip\n", encoding="utf-8")
    _git(repo, "init", "-q")

    config = {
        "include_extensions": [".md"],
        "exclude_patterns": ["excluded/**"],
        "use_default_exclude_patterns": True,
        "doctor_policy": {"allowlist_patterns": ["generated/allowed.md"]},
    }
    config_path = repo / "scan.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    db_path = tmp_path / "doctor_policy.db"
    write_sqlite(build_index(repo, config), str(db_path))
    report = build_doctor_report(
        str(db_path), repo_root=repo, config_path=config_path
    )

    untracked = _check(report, "indexed_untracked_files")
    allowed = [
        finding
        for finding in untracked["findings"]
        if finding.get("path") == "generated/allowed.md"
    ]
    assert allowed and allowed[0]["severity"] == "info"
    assert allowed[0]["policy_disposition"] == "allowlisted"
    all_paths = {
        finding.get("path")
        for check in report["checks"]
        for finding in check["findings"]
    }
    assert "excluded/skip.md" not in all_paths


def test_doctor_detects_oversized_text_and_total_surface_budget(tmp_path: Path) -> None:
    repo = tmp_path / "budget_repo"
    repo.mkdir()
    (repo / "large.md").write_text("# Large\n\n" + ("word " * 100), encoding="utf-8")
    (repo / "second.md").write_text("# Second\n\nmore text\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "large.md", "second.md")
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "doctor_policy": {
            "max_text_document_tokens": 1,
            "max_node_tokens": 1,
            "max_index_tokens": 1,
            "max_index_files": 1,
        },
    }
    config_path = repo / "scan.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    db_path = tmp_path / "doctor_budget.db"
    write_sqlite(build_index(repo, config), str(db_path))
    report = build_doctor_report(
        str(db_path), repo_root=repo, config_path=config_path
    )

    assert _check(report, "oversized_text_documents")["status"] == "warning"
    assert _check(report, "single_node_token_budget")["status"] == "warning"
    reasons = {
        finding["reason"]
        for finding in _check(report, "index_surface_budget")["findings"]
    }
    assert reasons == {"index_token_budget_exceeded", "index_file_budget_exceeded"}


def test_doctor_warning_does_not_make_verified_health_non_reusable(tmp_path: Path) -> None:
    repo = tmp_path / "warning_repo"
    (repo / "old").mkdir(parents=True)
    (repo / "old" / "note.md").write_text("# Old\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "old/note.md")
    db_path = tmp_path / "doctor_warning.db"
    write_sqlite(
        build_index(repo, {"include_extensions": [".md"], "exclude_patterns": []}),
        str(db_path),
    )
    verified_health = {
        "status": "healthy",
        "reusable": True,
        "reason": "index_reusable",
        "scan_id": "scan-1",
        "config_hash": "sha256:config",
        "source_state": {
            "status": "fresh",
            "reason": "source_state_matches_scan",
            "scan_id": "scan-1",
            "config_hash": "sha256:config",
        },
        "generated": "2026-07-17T00:00:00+00:00",
        "age_hours": 1.0,
        "stale_after_hours": 24,
    }
    report = build_doctor_report(
        str(db_path), repo_root=repo, health=verified_health
    )
    assert report["status"] == "warning"
    assert report["health"]["status"] == "healthy"
    assert report["health"]["reusable"] is True
