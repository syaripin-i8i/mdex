from __future__ import annotations

from pathlib import Path

from mdex.builder import build_index
from mdex.enrich import enrich_node
from mdex.indexer import write_sqlite
from mdex.resolver import prerequisite_order, related_nodes


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
    assert rows[0]["score"] > rows[1]["score"]
    assert rows[0]["distance"] == 2
    assert rows[0]["reason"] == "transitive depends_on (depth 2)"
    assert rows[1]["distance"] == 1
    assert rows[1]["reason"] == "direct depends_on"


def test_prerequisite_order_decision_top_n(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_prereq_decision.db"
    _build_db(quality_repo, quality_config, db_path)

    rows = prerequisite_order("decision/a.md", str(db_path), limit=3)
    assert [row["id"] for row in rows] == ["spec/b.md"]
    assert rows[0]["distance"] == 1


def test_prerequisite_order_limit_returns_top_n(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_prereq_limit.db"
    _build_db(quality_repo, quality_config, db_path)

    rows = prerequisite_order("design/root.md", str(db_path), limit=1)
    assert len(rows) == 1
    assert rows[0]["id"] == "spec/b.md"


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


def test_related_nodes_returns_expected_top_candidates(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_related_top.db"
    _build_db(quality_repo, quality_config, db_path)

    rows = related_nodes("design/root.md", str(db_path), limit=3)
    top_ids = {row["id"] for row in rows}
    assert "decision/a.md" in top_ids
    assert "spec/b.md" in top_ids
    assert "tasks/pending/T20260101000001.md" in top_ids


def test_related_nodes_decision_neighbors(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_related_decision.db"
    _build_db(quality_repo, quality_config, db_path)

    rows = related_nodes("decision/a.md", str(db_path), limit=3)
    top_ids = [row["id"] for row in rows[:3]]
    assert "spec/b.md" in top_ids
    assert "design/root.md" in top_ids


def test_related_nodes_use_override_summary_for_scoring(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_related_override.db"
    _build_db(quality_repo, quality_config, db_path)

    before = related_nodes("design/root.md", str(db_path), limit=20)
    before_map = {row["id"]: row for row in before}
    before_score = float(before_map.get("notes/orphan.md", {}).get("score", 0.0))

    enriched = enrich_node(
        "notes/orphan.md",
        str(db_path),
        "Root summary context selection and graph traversal checks for architecture decisions.",
        force=False,
    )
    assert enriched["status"] == "enriched"

    after = related_nodes("design/root.md", str(db_path), limit=20)
    after_map = {row["id"]: row for row in after}
    assert "notes/orphan.md" in after_map
    assert float(after_map["notes/orphan.md"]["score"]) > before_score
    assert any(
        reason.startswith("shared_summary_terms:")
        for reason in after_map["notes/orphan.md"]["reasons"]
    )


def test_related_nodes_supports_cjk_summary_terms(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_related_cjk.db"
    _build_db(quality_repo, quality_config, db_path)

    before = related_nodes("design/root.md", str(db_path), limit=20)
    before_map = {row["id"]: row for row in before}
    before_score = float(before_map.get("notes/orphan.md", {}).get("score", 0.0))

    root_enriched = enrich_node(
        "design/root.md",
        str(db_path),
        "設計方針と依存関係を整理する中核ドキュメントです。",
        force=False,
    )
    assert root_enriched["status"] == "enriched"

    orphan_enriched = enrich_node(
        "notes/orphan.md",
        str(db_path),
        "設計方針の確認メモ。依存関係の見直し項目をまとめる。",
        force=False,
    )
    assert orphan_enriched["status"] == "enriched"

    after = related_nodes("design/root.md", str(db_path), limit=20)
    after_map = {row["id"]: row for row in after}
    assert "notes/orphan.md" in after_map
    assert float(after_map["notes/orphan.md"]["score"]) > before_score
    assert any(
        reason.startswith("shared_summary_terms:")
        for reason in after_map["notes/orphan.md"]["reasons"]
    )


def test_first_uses_override_summary_for_tie_breaking(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_resolver_first_override.db"
    _build_db(quality_repo, quality_config, db_path)

    before = prerequisite_order("design/tie.md", str(db_path), limit=2)
    assert [row["id"] for row in before] == ["decision/a.md", "spec/b.md"]

    enriched = enrich_node(
        "spec/b.md",
        str(db_path),
        "Architecture decision tie breaker prerequisite scoring guidance.",
        force=False,
    )
    assert enriched["status"] == "enriched"

    after = prerequisite_order("design/tie.md", str(db_path), limit=2)
    assert [row["id"] for row in after] == ["spec/b.md", "decision/a.md"]
