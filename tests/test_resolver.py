from __future__ import annotations

from pathlib import Path

from runtime.builder import build_index
from runtime.indexer import write_sqlite
from runtime.resolver import prerequisite_order, related_nodes


def _build_db(root: Path, config: dict[str, object], db_path: Path) -> None:
    index = build_index(str(root), config)
    write_sqlite(index, str(db_path))


def test_prerequisite_order_returns_root_first(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_prereq.db"
    _build_db(quality_repo, quality_config, db_path)

    rows = prerequisite_order("design/root.md", str(db_path), limit=10)
    ids = [row["id"] for row in rows]
    assert ids == ["spec/b.md", "decision/a.md"]
    assert rows[0]["distance"] == 2
    assert rows[0]["reason"] == "transitive depends_on (depth 2)"
    assert rows[1]["distance"] == 1
    assert rows[1]["reason"] == "direct depends_on"


def test_prerequisite_order_handles_cycle(
    fixture_repo: Path,
    build_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_cycle.db"
    _build_db(fixture_repo, build_config, db_path)

    rows = prerequisite_order("docs/cycle_x.md", str(db_path), limit=10)
    ids = [row["id"] for row in rows]
    assert "docs/cycle_x.md" not in ids
    assert len(ids) <= 1


def test_related_nodes_ignores_unresolved_edges(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_related.db"
    _build_db(quality_repo, quality_config, db_path)

    rows = related_nodes("design/root.md", str(db_path), limit=20)
    ids = [row["id"] for row in rows]
    assert "missing.md" not in ids
