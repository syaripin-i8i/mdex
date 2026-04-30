from __future__ import annotations

import sqlite3
from pathlib import Path

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
