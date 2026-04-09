from __future__ import annotations

from pathlib import Path

from runtime.builder import build_index
from runtime.enrich import enrich_node
from runtime.indexer import write_sqlite
from runtime.store import list_nodes, list_orphan_nodes, search_nodes


def _build_quality_db(quality_repo: Path, quality_config: dict[str, object], db_path: Path) -> None:
    index = build_index(str(quality_repo), quality_config)
    write_sqlite(index, str(db_path))


def test_list_orphan_nodes_ignores_unresolved_edges(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_orphans.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    orphan_ids = [node["id"] for node in list_orphan_nodes(str(db_path))]
    assert "notes/orphan.md" in orphan_ids
    assert "design/root.md" not in orphan_ids


def test_search_nodes_matches_title_summary_and_tags(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_search.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    title_hits = [row["id"] for row in search_nodes(str(db_path), "Root Design", limit=10)]
    summary_hits = [row["id"] for row in search_nodes(str(db_path), "architecture constraints", limit=10)]
    tag_hits = [row["id"] for row in search_nodes(str(db_path), "alpha", limit=10)]

    assert "design/root.md" in title_hits
    assert "decision/a.md" in summary_hits
    assert "design/root.md" in tag_hits


def test_search_nodes_returns_empty_for_no_match(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_nohit.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    rows = search_nodes(str(db_path), "this-does-not-exist", limit=10)
    assert rows == []


def test_list_nodes_prefers_agent_override_after_rescan(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_overrides.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    agent_summary = "Agent override summary for root."
    enriched = enrich_node("design/root.md", str(db_path), agent_summary, force=False)
    assert enriched["status"] == "enriched"

    rescan_index = build_index(str(quality_repo), quality_config)
    write_sqlite(rescan_index, str(db_path))

    nodes = list_nodes(str(db_path))
    node_map = {node["id"]: node for node in nodes}
    assert node_map["design/root.md"]["summary_source"] == "agent"
    assert node_map["design/root.md"]["summary"] == agent_summary
    assert node_map["decision/a.md"]["summary_source"] == "seed"
