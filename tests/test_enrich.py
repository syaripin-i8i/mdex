from __future__ import annotations

from pathlib import Path

from runtime.builder import build_index
from runtime.enrich import enrich_node
from runtime.indexer import write_sqlite
from runtime.store import get_node


def _build_db(root: Path, config: dict[str, object], db_path: Path) -> None:
    index = build_index(str(root), config)
    write_sqlite(index, str(db_path))


def test_enrich_updates_summary_and_source(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_enrich.db"
    _build_db(quality_repo, quality_config, db_path)

    before = get_node(str(db_path), "design/root.md")
    assert before is not None
    assert before["summary_source"] == "seed"

    result = enrich_node(
        "design/root.md",
        str(db_path),
        "Agent generated summary for downstream task selection.",
        force=False,
    )
    assert result["status"] == "enriched"
    assert result["summary_source"] == "agent"
    assert result["skipped"] is False
    assert isinstance(result["previous_summary"], str)
    assert result["new_summary"] == "Agent generated summary for downstream task selection."
    after = get_node(str(db_path), "design/root.md")
    assert after is not None
    assert after["summary_source"] == "agent"
    assert after["summary"] == "Agent generated summary for downstream task selection."
    assert after["summary_updated"]


def test_enrich_requires_summary(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_enrich_required.db"
    _build_db(quality_repo, quality_config, db_path)

    result = enrich_node("design/root.md", str(db_path), "", force=False)
    assert result["status"] == "error"
    assert result["error"] == "summary is required"


def test_enrich_returns_error_for_missing_node(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_enrich_missing.db"
    _build_db(quality_repo, quality_config, db_path)

    result = enrich_node("design/no_such.md", str(db_path), "summary", force=False)
    assert result["status"] == "error"
    assert result["error"] == "node not found"


def test_enrich_skips_existing_agent_summary_without_force(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_enrich_skip.db"
    _build_db(quality_repo, quality_config, db_path)

    first = enrich_node("design/root.md", str(db_path), "Agent summary v1", force=False)
    assert first["status"] == "enriched"

    second = enrich_node("design/root.md", str(db_path), "Agent summary v2", force=False)
    assert second["status"] == "skipped"
    assert second["skipped"] is True
    assert second["summary_source"] == "agent"
    assert second["previous_summary"] == "Agent summary v1"
    assert second["new_summary"] == "Agent summary v1"
