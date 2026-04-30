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
    assert first["command"] == "mdex"
    assert first["args"][:1] == ["open"]
    assert isinstance(first["reason"], str)
    digest = result["actionable_digest"]
    assert digest["intent"] == "root decision"
    assert digest["relevant_docs"]
    assert digest["suggested_rg"]
    assert any("code entrypoint" in gap for gap in digest["context_gaps"])


def test_select_context_actionable_digest_minimal_omits_full_only_keys(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_digest_minimal.db"
    _build_db(quality_repo, quality_config, db_path)

    result = select_context("root decision", str(db_path), budget=4000, limit=5, actionable=True, digest="minimal")
    digest = result["actionable_digest"]

    assert set(digest) == {"intent", "relevant_docs", "suggested_rg", "context_gaps"}
    assert digest["intent"] == "root decision"
    assert digest["suggested_rg"][0]["command"] == "rg"
    assert set(result["recommended_next_actions_v2"][0]) >= {"command", "args", "reason"}


def test_select_context_actionable_digest_surfaces_code_and_guardrails(tmp_path: Path) -> None:
    repo = tmp_path / "entrypoint_repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "runtime").mkdir()
    (repo / "tests").mkdir()

    (repo / "docs" / "reply_guard.md").write_text(
        """---
type: design
status: active
tags:
  - reply
  - guardrail
---
# Reply Guard

Reply guardrail must check runtime/elyth_runtime.py and tests/test_elyth_thread_reply_guard.py before changing notification behavior.
""",
        encoding="utf-8",
    )
    (repo / "runtime" / "elyth_runtime.py").write_text(
        "def thread_reply_guard():\n    return 'reply guardrail runtime entrypoint'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_elyth_thread_reply_guard.py").write_text(
        "def test_thread_reply_guard():\n    assert True\n",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md", ".py"],
        "exclude_patterns": [],
        "node_type_map": {"design": ["docs"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "entrypoint.db"
    _build_db(repo, config, db_path)

    result = select_context("reply guardrail runtime", str(db_path), budget=4000, limit=6, actionable=True)
    digest = result["actionable_digest"]

    assert [item["id"] for item in digest["known_guardrails"]] == ["docs/reply_guard.md"]
    code_ids = {item["id"] for item in digest["likely_code_entrypoints"]}
    assert "runtime/elyth_runtime.py" in code_ids
    assert "tests/test_elyth_thread_reply_guard.py" in code_ids
    assert digest["suggested_rg"][0]["command"] == "rg"
    assert digest["suggested_rg"][0]["args"][0] == "-n"
    assert {"runtime", "tests"}.issubset(set(digest["suggested_rg"][0]["paths"]))


def test_select_context_actionable_digest_detects_japanese_guardrails(tmp_path: Path) -> None:
    repo = tmp_path / "japanese_guardrail_repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "reply_policy.md").write_text(
        """---
type: design
status: active
tags:
  - 返信
---
# 返信ポリシー

返信処理の制約。注意: synthetic event は既読化禁止。前提として権限を確認する。
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"design": ["docs"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "japanese_guardrail.db"
    _build_db(repo, config, db_path)

    result = select_context("返信 制約", str(db_path), budget=4000, limit=3, actionable=True)
    guardrails = result["actionable_digest"]["known_guardrails"]

    assert [item["id"] for item in guardrails] == ["docs/reply_policy.md"]
    assert "制約" in guardrails[0]["reason"]


def test_select_context_detects_japanese_guardrails_in_title_summary_and_tags(tmp_path: Path) -> None:
    repo = tmp_path / "japanese_guardrail_fields_repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    for filename, title, tags, summary in (
        ("title.md", "注意事項", ["reply"], "title carries the guardrail term"),
        ("summary.md", "Reply Summary", ["reply"], "認可の前提を確認する"),
        ("tags.md", "Reply Tags", ["ロールバック"], "tag carries the guardrail term"),
    ):
        (repo / "docs" / filename).write_text(
            f"""---
type: design
status: active
tags:
  - {tags[0]}
---
# {title}

返信 policy {summary}
""",
            encoding="utf-8",
        )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"design": ["docs"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "japanese_guardrail_fields.db"
    _build_db(repo, config, db_path)

    result = select_context("返信 policy", str(db_path), budget=4000, limit=3, actionable=True)
    guardrail_ids = {item["id"] for item in result["actionable_digest"]["known_guardrails"]}

    assert guardrail_ids == {"docs/title.md", "docs/summary.md", "docs/tags.md"}


def test_select_context_suggested_rg_uses_args_for_shell_sensitive_terms(tmp_path: Path) -> None:
    repo = tmp_path / "entrypoint_repo"
    code_dir = repo / "runtime space"
    code_dir.mkdir(parents=True)
    (code_dir / "price_reply.py").write_text(
        "def price_reply():\n    return 'price $reply path with spaces'\n",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "entrypoint.db"
    _build_db(repo, config, db_path)

    result = select_context("price $reply", str(db_path), budget=4000, limit=3, actionable=True)
    suggestion = result["actionable_digest"]["suggested_rg"][0]

    assert suggestion["command"] == "rg"
    assert suggestion["args"][0] == "-n"
    assert suggestion["args"][1] == "price|\\$reply"
    assert "runtime space" in suggestion["args"]
    assert suggestion["paths"] == ["runtime space"]


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


def test_resolve_context_scoring_invalid_values_fall_back_to_defaults() -> None:
    scoring, source = resolve_context_scoring_config(
        runtime_config={
            "context_scoring": {
                "keyword": {"title": "not-a-number"},
                "graph_default_boost": 0,
                "soft_budget_multiplier": -1,
                "primary_keyword_search_multiplier": "oops",
            }
        }
    )

    assert source == "defaults"
    assert scoring["keyword"]["title"] == 3.0
    assert scoring["graph_default_boost"] == 0.15
    assert scoring["soft_budget_multiplier"] == 1.2
    assert scoring["primary_keyword_search_multiplier"] == 5


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
