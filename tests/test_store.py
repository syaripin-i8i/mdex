from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mdex.builder import build_index
from mdex.enrich import enrich_node
from mdex import indexer
from mdex.indexer import write_sqlite
from mdex.store import list_nodes, list_orphan_nodes, list_stale_nodes, search_nodes, update_node_summary


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


def test_search_nodes_uses_override_summary(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_search_override.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    before = search_nodes(str(db_path), "override-needle-xyz", limit=10)
    assert before == []

    enriched = enrich_node(
        "spec/b.md",
        str(db_path),
        "Override needle xyz appears only in agent summary.",
        force=False,
    )
    assert enriched["status"] == "enriched"

    after = search_nodes(str(db_path), "override-needle-xyz", limit=10)
    assert any(row["id"] == "spec/b.md" for row in after)


def test_search_nodes_supports_cjk_query_terms(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_search_cjk.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    before = search_nodes(str(db_path), "設計方針", limit=10)
    assert before == []

    enriched = enrich_node(
        "design/root.md",
        str(db_path),
        "設計方針を短く共有するための要約です。",
        force=False,
    )
    assert enriched["status"] == "enriched"

    after = search_nodes(str(db_path), "設計方針", limit=10)
    assert any(row["id"] == "design/root.md" for row in after)


def test_list_stale_nodes_filters_seed_and_age(tmp_path: Path) -> None:
    repo = tmp_path / "stale_repo"
    repo.mkdir()
    config = {
        "exclude_patterns": [],
        "node_type_map": {"design": ["design"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }

    (repo / "stale_seed.md").write_text(
        """---
type: design
status: active
updated: 2000-01-01
---
# Stale Seed

seed summary text
""",
        encoding="utf-8",
    )
    (repo / "fresh_seed.md").write_text(
        """---
type: design
status: active
updated: 2100-01-01
---
# Fresh Seed

fresh summary text
""",
        encoding="utf-8",
    )
    (repo / "agent_override.md").write_text(
        """---
type: design
status: active
updated: 2000-01-01
---
# Agent Override

override candidate
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "quality_store_stale.db"
    index = build_index(str(repo), config)
    write_sqlite(index, str(db_path))

    enriched = enrich_node("agent_override.md", str(db_path), "agent updated summary", force=False)
    assert enriched["status"] == "enriched"

    rows = list_stale_nodes(str(db_path), days=30)
    stale_ids = [row["id"] for row in rows]

    assert "stale_seed.md" in stale_ids
    assert "fresh_seed.md" not in stale_ids
    assert "agent_override.md" not in stale_ids
    assert rows[0]["summary_source"] == "seed"
    assert "updated" in rows[0]


def test_list_nodes_includes_estimated_tokens(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_estimated_tokens.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    rows = list_nodes(str(db_path))
    assert rows
    assert all("estimated_tokens" in row for row in rows)
    assert all(int(row["estimated_tokens"]) > 0 for row in rows)


def test_update_node_summary_uses_source_argument(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_store_source.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    ok = update_node_summary(str(db_path), "design/root.md", "scan refresh summary", source="scan")
    assert ok is True
    nodes = {row["id"]: row for row in list_nodes(str(db_path))}
    assert nodes["design/root.md"]["summary_source"] == "scan"


def test_write_sqlite_rolls_back_on_insert_failure(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "quality_store_tx_rollback.db"
    _build_quality_db(quality_repo, quality_config, db_path)

    enriched = enrich_node("design/root.md", str(db_path), "keep override", force=False)
    assert enriched["status"] == "enriched"

    with sqlite3.connect(str(db_path)) as conn:
        original_nodes = int(conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        original_overrides = int(conn.execute("SELECT COUNT(*) FROM node_overrides").fetchone()[0])

    failing_index = build_index(str(quality_repo), quality_config)
    original_insert_nodes = indexer._insert_nodes

    def _boom(cur: sqlite3.Cursor, nodes: list[dict[str, object]]) -> None:
        original_insert_nodes(cur, nodes[:1])
        raise RuntimeError("injected failure")

    monkeypatch.setattr(indexer, "_insert_nodes", _boom)

    with pytest.raises(RuntimeError, match="injected failure"):
        write_sqlite(failing_index, str(db_path))

    with sqlite3.connect(str(db_path)) as conn:
        nodes_after = int(conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
        overrides_after = int(conn.execute("SELECT COUNT(*) FROM node_overrides").fetchone()[0])

    assert nodes_after == original_nodes
    assert overrides_after == original_overrides
