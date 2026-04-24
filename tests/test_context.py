from __future__ import annotations

from pathlib import Path

import pytest

from mdex.builder import build_index
from mdex.context import resolve_context_scoring_config, select_context
from mdex.indexer import write_sqlite


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


def test_select_context_skips_file_read_when_content_not_requested(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "quality_context_no_read.db"
    _build_db(quality_repo, quality_config, db_path)

    original_read_text = Path.read_text

    def _blocked_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.suffix in {".md", ".json", ".jsonl"}:
            raise AssertionError("context should not read source files when include_content=False")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _blocked_read_text)
    result = select_context("root decision", str(db_path), budget=4000, limit=5, include_content=False)
    assert result["nodes"]


def test_select_context_actionable_includes_structured_actions(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_actions_v2.db"
    _build_db(quality_repo, quality_config, db_path)

    result = select_context("root decision", str(db_path), budget=4000, limit=5, actionable=True)
    assert result["recommended_next_actions"]
    assert result["recommended_next_actions_v2"]
    first = result["recommended_next_actions_v2"][0]
    assert first["command"] == "open"
    assert isinstance(first["args"], list)
    assert isinstance(first["reason"], str)


def test_resolve_context_scoring_prefers_runtime_config_over_scan_config() -> None:
    scan_config = {
        "context_scoring": {
            "keyword": {"title": 9.9},
            "soft_budget_multiplier": 1.1,
        }
    }
    runtime_config = {
        "context_scoring": {
            "keyword": {"title": 4.4},
            "soft_budget_multiplier": 1.5,
        }
    }

    scoring, source = resolve_context_scoring_config(runtime_config=runtime_config, scan_config=scan_config)
    assert source == "runtime_config"
    assert scoring["keyword"]["title"] == 4.4
    assert scoring["soft_budget_multiplier"] == 1.5


def test_select_context_score_breakdown_records_config_source(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_config_source.db"
    _build_db(quality_repo, quality_config, db_path)

    scoring, source = resolve_context_scoring_config(
        runtime_config={"context_scoring": {"keyword": {"title": 5.0}}},
        scan_config={"context_scoring": {"keyword": {"title": 2.0}}},
    )
    result = select_context(
        "root decision",
        str(db_path),
        budget=4000,
        limit=5,
        scoring_config=scoring,
        scoring_config_source=source,
    )
    assert result["nodes"]
    assert result["nodes"][0]["score_breakdown"]["config_source"] == "runtime_config"


def test_select_context_ranking_regression_on_quality_fixture(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_regression.db"
    _build_db(quality_repo, quality_config, db_path)

    result = select_context("root decision", str(db_path), budget=4000, limit=5)
    ranked = [row["id"] for row in result["nodes"][:3]]
    assert ranked == ["decision/a.md", "design/root.md", "design/tie.md"]
