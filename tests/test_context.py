from __future__ import annotations

from pathlib import Path

from runtime.builder import build_index
from runtime.context import select_context
from runtime.indexer import write_sqlite


def _build_db(root: Path, config: dict[str, object], db_path: Path) -> None:
    index = build_index(str(root), config)
    write_sqlite(index, str(db_path))


def test_select_context_returns_empty_for_blank_query(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_empty.db"
    _build_db(quality_repo, quality_config, db_path)

    result = select_context("", str(db_path), budget=4000, limit=10)
    assert result["nodes"] == []
    assert result["total_tokens"] == 0


def test_select_context_soft_budget_prefers_top_nodes(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_budget.db"
    _build_db(quality_repo, quality_config, db_path)

    result = select_context("root alpha decision", str(db_path), budget=100, limit=10)
    assert result["nodes"]
    assert result["nodes"][0]["id"] == "design/root.md"
    assert int(result["total_tokens"]) <= 120


def test_select_context_includes_score_breakdown(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_breakdown.db"
    _build_db(quality_repo, quality_config, db_path)

    result = select_context("root decision", str(db_path), budget=4000, limit=5)
    assert result["nodes"]
    top = result["nodes"][0]
    breakdown = top["score_breakdown"]
    assert "keyword" in breakdown
    assert "type_status" in breakdown
    assert "recency" in breakdown
    assert "graph_boost" in breakdown
    assert "token_cost" in breakdown
    assert "total" in breakdown
    assert abs(float(breakdown["total"]) - float(top["score"])) < 1e-6


def test_select_context_penalizes_done_more_than_active(tmp_path: Path) -> None:
    repo = tmp_path / "context_status_repo"
    repo.mkdir()
    config = {
        "exclude_patterns": [],
        "node_type_map": {"design": ["design"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }

    (repo / "active_doc.md").write_text(
        """---
type: design
status: active
updated: 2025-01-01
---
# Active Doc

shared ranking term for context selection
""",
        encoding="utf-8",
    )
    (repo / "done_doc.md").write_text(
        """---
type: design
status: done
updated: 2025-01-01
---
# Done Doc

shared ranking term for context selection
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "quality_context_status.db"
    _build_db(repo, config, db_path)

    result = select_context("shared ranking term", str(db_path), budget=4000, limit=2)
    ids = [row["id"] for row in result["nodes"]]
    assert ids[0] == "active_doc.md"
    assert "done_doc.md" in ids
