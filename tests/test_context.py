from __future__ import annotations

from pathlib import Path

import pytest

from mdex.builder import build_index
from mdex.context import resolve_context_scoring_config, select_context
from mdex.indexer import write_sqlite
from mdex.multiindex import build_multi_context_payload


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


def test_learning_note_boost_offsets_done_status_for_failure_terms(tmp_path: Path) -> None:
    repo = tmp_path / "learning_note_repo"
    repo.mkdir()
    (repo / "tasks").mkdir()
    (repo / "tasks" / "T20260101010101.md").write_text(
        """---
type: task
status: done
updated: 2026-01-01
---
# Task: reply tone fix

Past completed implementation.

### Learning Note
- symptom: 返信が刺しすぎで重すぎる。ずぶり感が強く、穏やかさと儀式感の調整が必要。
- next_time_query_seed: reply tone too sharp, heavy ritual wording, gentle self post
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"task": ["tasks"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "learning_note.db"
    _build_db(repo, config, db_path)

    result = select_context("刺しすぎ 穏やか", str(db_path), budget=4000, limit=3)

    assert result["nodes"][0]["id"] == "tasks/T20260101010101.md"
    breakdown = result["nodes"][0]["score_breakdown"]
    assert breakdown["keyword"]["learning_note"] > 0
    assert breakdown["type_status"]["status_bonus"] < 0


def test_learning_note_boost_handles_japanese_punctuation(tmp_path: Path) -> None:
    repo = tmp_path / "learning_note_punctuation_repo"
    repo.mkdir()
    (repo / "tasks").mkdir()
    (repo / "tasks" / "T20260101010103.md").write_text(
        """---
type: task
status: done
updated: 2026-01-01
---
# Task: reply tone fix

### Learning Note
- symptom: 返信が刺しすぎで重すぎる。穏やかさの調整が必要。
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"task": ["tasks"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "learning_note_punctuation.db"
    _build_db(repo, config, db_path)

    result = select_context("刺しすぎ、穏やか", str(db_path), budget=4000, limit=3)

    assert result["nodes"][0]["id"] == "tasks/T20260101010103.md"

    compact_result = select_context("刺しすぎ穏やか", str(db_path), budget=4000, limit=3)
    assert compact_result["nodes"][0]["id"] == "tasks/T20260101010103.md"


def test_learning_note_boost_handles_full_width_colon_short_cjk_terms(tmp_path: Path) -> None:
    repo = tmp_path / "learning_note_colon_repo"
    repo.mkdir()
    (repo / "tasks").mkdir()
    (repo / "tasks" / "T20260101010104.md").write_text(
        """---
type: task
status: done
updated: 2026-01-01
---
# Task: reply constraint

### Learning Note
- symptom: 返信 制約 gate missed
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"task": ["tasks"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "learning_note_colon.db"
    _build_db(repo, config, db_path)

    result = select_context("返信：制約", str(db_path), budget=4000, limit=3)

    assert result["nodes"][0]["id"] == "tasks/T20260101010104.md"

    compact_result = select_context("返信制約", str(db_path), budget=4000, limit=3)
    assert compact_result["nodes"][0]["id"] == "tasks/T20260101010104.md"


def test_learning_note_boost_handles_mixed_latin_cjk_compact_query(tmp_path: Path) -> None:
    repo = tmp_path / "learning_note_mixed_repo"
    repo.mkdir()
    (repo / "tasks").mkdir()
    (repo / "tasks" / "T20260101010105.md").write_text(
        """---
type: task
status: done
updated: 2026-01-01
---
# Task: pylock constraint

### Learning Note
- symptom: pylock 制約 handling failed
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"task": ["tasks"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "learning_note_mixed.db"
    _build_db(repo, config, db_path)

    result = select_context("pylock制約", str(db_path), budget=4000, limit=3)

    assert result["nodes"][0]["id"] == "tasks/T20260101010105.md"


def test_learning_note_seed_terms_are_searchable(tmp_path: Path) -> None:
    from mdex.store import search_nodes

    repo = tmp_path / "learning_note_seed_repo"
    repo.mkdir()
    (repo / "tasks").mkdir()
    (repo / "tasks" / "T20260101010102.md").write_text(
        """---
type: task
status: done
updated: 2026-01-01
---
# Task: install hardening

Implementation notes.

### Learning Note
- symptom: lock install hash verification was missing
- next_time_query_seed: pylock require-hashes, pip install hash pinning, tomllib lock parser
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"task": ["tasks"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "learning_note_seed.db"
    _build_db(repo, config, db_path)

    rows = search_nodes(str(db_path), "require-hashes", limit=5)

    assert [row["id"] for row in rows] == ["tasks/T20260101010102.md"]
    assert rows[0]["learning_note"]["captured"] is True


def test_learning_note_section_does_not_become_public_summary(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "learning_note_summary_repo"
    repo.mkdir()
    (repo / "tasks").mkdir()
    (repo / "tasks" / "T20260101010106.md").write_text(
        """---
type: task
status: done
updated: 2026-01-01
---
# Task: note only

### Learning Note

- symptom: SECRET_LEARNING_NOTE_TEXT should not become summary
- next_time_query_seed: private query seed
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"task": ["tasks"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "learning_note_summary.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["tasks/T20260101010106.md"]

    assert "SECRET_LEARNING_NOTE_TEXT" not in node["summary"]


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


def test_python_symbol_summary_surfaces_code_entrypoint(tmp_path: Path) -> None:
    repo = tmp_path / "python_symbol_repo"
    repo.mkdir()
    (repo / "runtime").mkdir()
    (repo / "runtime" / "reason_code.py").write_text(
        """import json


class ReasonCode:
    pass


def classify_reason_code(value: str) -> str:
    return json.dumps({"reason_code": value})
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "python_symbol.db"
    _build_db(repo, config, db_path)

    result = select_context("classify_reason_code", str(db_path), budget=4000, limit=3, actionable=True)

    assert result["nodes"][0]["id"] == "runtime/reason_code.py"
    assert result["nodes"][0]["score_breakdown"]["keyword"]["search_terms"] > 0
    digest_ids = [item["id"] for item in result["actionable_digest"]["likely_code_entrypoints"]]
    assert digest_ids == ["runtime/reason_code.py"]


def test_python_symbol_summary_avoids_source_literals_and_raw_token_budget(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "python_privacy_repo"
    repo.mkdir()
    (repo / "runtime").mkdir()
    (repo / "runtime" / "big_module.py").write_text(
        "\n".join(
            [
                "import os",
                "",
                "def classify_reason_code(value: str) -> str:",
                "    secret_literal = 'SECRET_TOKEN_SHOULD_NOT_INDEX'",
                "    return value",
                *["# filler comment SECRET_TOKEN_SHOULD_NOT_INDEX" for _ in range(500)],
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "python_privacy.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["runtime/big_module.py"]
    search_text = " ".join(node["search_terms"])

    assert "SECRET_TOKEN_SHOULD_NOT_INDEX" not in search_text
    assert str(tmp_path).replace("\\", "/") not in search_text
    assert int(node["estimated_tokens"]) < 200

    result = select_context(
        "classify_reason_code",
        str(db_path),
        budget=100,
        limit=1,
        include_content=True,
    )
    assert "SECRET_TOKEN_SHOULD_NOT_INDEX" not in result["nodes"][0]["content"]
    assert int(result["total_tokens"]) < 100


def test_python_syntax_error_uses_safe_summary(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "python_syntax_error_repo"
    repo.mkdir()
    (repo / "runtime").mkdir()
    (repo / "runtime" / "broken.py").write_text(
        "def broken(:\n    secret = 'SECRET_TOKEN_SHOULD_NOT_INDEX'\n",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "python_syntax_error.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["runtime/broken.py"]

    assert "SECRET_TOKEN_SHOULD_NOT_INDEX" not in node["summary"]
    assert "Syntax error prevented symbol extraction" in node["summary"]


def test_python_test_detection_uses_node_id_not_absolute_parent_path(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "test_project_parent" / "repo"
    repo.mkdir(parents=True)
    (repo / "runtime").mkdir()
    (repo / "runtime" / "worker.py").write_text("def run_worker():\n    return True\n", encoding="utf-8")
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "python_parent_path.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["runtime/worker.py"]

    assert node["type"] == "code"
    assert node["title"].startswith("Python module")


def test_python_test_detection_does_not_match_contest_filename(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "contest_repo"
    repo.mkdir()
    (repo / "runtime").mkdir()
    (repo / "runtime" / "contest_helper.py").write_text("def helper():\n    return True\n", encoding="utf-8")
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "contest.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["runtime/contest_helper.py"]

    assert node["type"] == "code"
    assert "test" not in node["tags"]


def test_python_symbol_summary_includes_class_methods(tmp_path: Path) -> None:
    repo = tmp_path / "python_method_repo"
    repo.mkdir()
    (repo / "runtime").mkdir()
    (repo / "runtime" / "client.py").write_text(
        """class ApiClient:
    def send_message(self) -> None:
        pass
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "python_method.db"
    _build_db(repo, config, db_path)

    result = select_context("send_message", str(db_path), budget=4000, limit=3)

    assert result["nodes"][0]["id"] == "runtime/client.py"


def test_non_python_code_budget_uses_emitted_summary_when_content_requested(tmp_path: Path) -> None:
    repo = tmp_path / "js_budget_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "huge.js").write_text(
        "function classifyReasonCode() { return true; }\n" + "\n".join(["// filler" for _ in range(1000)]),
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".js"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 120,
    }
    db_path = tmp_path / "js_budget.db"
    _build_db(repo, config, db_path)

    result = select_context("classifyReasonCode", str(db_path), budget=100, limit=1, include_content=True)

    assert result["nodes"][0]["id"] == "src/huge.js"
    assert int(result["total_tokens"]) < 100
    assert "// filler" not in result["nodes"][0]["content"]


def test_generic_code_symbol_summary_ignores_comment_and_string_literals(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "generic_privacy_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "privacy.js").write_text(
        """// function SECRET_TOKEN_SHOULD_NOT_INDEX() {}
const text = "function STRING_LITERAL_SHOULD_NOT_INDEX() {}";
export function publishMessage() {
  return true;
}
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".js"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 120,
    }
    db_path = tmp_path / "generic_privacy.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["src/privacy.js"]
    public_text = " ".join([node["summary"], " ".join(node["tags"])])

    assert "publishMessage" in public_text
    assert "SECRET_TOKEN_SHOULD_NOT_INDEX" not in public_text
    assert "STRING_LITERAL_SHOULD_NOT_INDEX" not in public_text


def test_generic_code_symbol_summary_ignores_block_comments_and_template_literals(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "generic_multiline_privacy_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "privacy.ts").write_text(
        """/*
function BLOCK_COMMENT_SHOULD_NOT_INDEX() {}
*/
const text = `
function TEMPLATE_LITERAL_SHOULD_NOT_INDEX() {}
`;
export function publishMessage() {
  return true;
}
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".ts"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 120,
    }
    db_path = tmp_path / "generic_multiline_privacy.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["src/privacy.ts"]
    public_text = " ".join([node["summary"], " ".join(node["tags"])])

    assert "publishMessage" in public_text
    assert "BLOCK_COMMENT_SHOULD_NOT_INDEX" not in public_text
    assert "TEMPLATE_LITERAL_SHOULD_NOT_INDEX" not in public_text


def test_generic_code_symbol_summary_handles_common_languages(tmp_path: Path) -> None:
    repo = tmp_path / "generic_symbols_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "lib.rs").write_text("pub fn transmit_packet() {}\n", encoding="utf-8")
    (repo / "src" / "service.go").write_text("func SendWebhook() {}\n", encoding="utf-8")
    (repo / "src" / "native.c").write_text("int open_native_channel(void) {\n  return 1;\n}\n", encoding="utf-8")
    config = {
        "include_extensions": [".rs", ".go", ".c"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 120,
    }
    db_path = tmp_path / "generic_symbols.db"
    _build_db(repo, config, db_path)

    rust = select_context("transmit_packet", str(db_path), budget=4000, limit=3)
    go = select_context("send webhook", str(db_path), budget=4000, limit=3)
    c = select_context("open_native_channel", str(db_path), budget=4000, limit=3)

    assert rust["nodes"][0]["id"] == "src/lib.rs"
    assert go["nodes"][0]["id"] == "src/service.go"
    assert c["nodes"][0]["id"] == "src/native.c"


def test_generic_test_file_is_typed_as_test(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "generic_test_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "reply.spec.ts").write_text("function checksReply() {}\n", encoding="utf-8")
    config = {
        "include_extensions": [".ts"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 120,
    }
    db_path = tmp_path / "generic_test.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["src/reply.spec.ts"]

    assert node["type"] == "test"
    assert "test" in node["tags"]


def test_generic_underscore_test_file_is_typed_as_test(tmp_path: Path) -> None:
    from mdex.store import list_nodes

    repo = tmp_path / "generic_go_test_repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "worker_test.go").write_text("func TestWorker() {}\n", encoding="utf-8")
    config = {
        "include_extensions": [".go"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 120,
    }
    db_path = tmp_path / "generic_go_test.db"
    _build_db(repo, config, db_path)

    node = {row["id"]: row for row in list_nodes(str(db_path))}["src/worker_test.go"]

    assert node["type"] == "test"
    assert "test" in node["tags"]


def test_actionable_digest_labels_root_test_file_as_test_entrypoint(tmp_path: Path) -> None:
    repo = tmp_path / "root_test_repo"
    repo.mkdir()
    (repo / "test_reason_code.py").write_text(
        "def test_reason_code():\n    assert True\n",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".py"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 240,
    }
    db_path = tmp_path / "root_test.db"
    _build_db(repo, config, db_path)

    result = select_context("test_reason_code", str(db_path), budget=4000, limit=3, actionable=True)
    entrypoint = result["actionable_digest"]["likely_code_entrypoints"][0]

    assert entrypoint["id"] == "test_reason_code.py"
    assert entrypoint["reason"] == "likely test entrypoint"


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


def test_select_context_suggested_rg_keeps_cjk_multiword_boundaries(tmp_path: Path) -> None:
    repo = tmp_path / "cjk_boundary_repo"
    repo.mkdir()
    (repo / "unrelated.md").write_text("# Unrelated\n\nNo matching context here.\n", encoding="utf-8")
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "cjk_boundary.db"
    _build_db(repo, config, db_path)

    result = select_context("リンク グラフ 知識", str(db_path), budget=4000, limit=3, actionable=True)
    suggestion = result["actionable_digest"]["suggested_rg"][0]

    assert suggestion["pattern"] == "リンク|グラフ|知識"
    assert "クグ" not in suggestion["pattern"]


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


def test_select_context_expands_repo_local_synonyms(tmp_path: Path) -> None:
    repo = tmp_path / "synonym_repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "self_post.md").write_text(
        """---
type: design
status: active
search_terms:
  - self_post
---
# Self Post

Spontaneous post pipeline notes.
""",
        encoding="utf-8",
    )
    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"design": ["docs"]},
        "synonyms": {"自発投稿": ["self_post", "spontaneous post"]},
    }
    db_path = tmp_path / "synonym.db"
    _build_db(repo, config, db_path)
    scoring, source = resolve_context_scoring_config(scan_config=config)

    result = select_context("自発投稿", str(db_path), budget=4000, limit=3, scoring_config=scoring, scoring_config_source=source)

    assert result["nodes"][0]["id"] == "docs/self_post.md"
    assert "spontaneous post" in result["nodes"][0]["score_breakdown"]["keyword"]["matched_terms"]


def test_select_context_actionable_adds_discovery_lane_without_read_order_duplicates(tmp_path: Path) -> None:
    repo = tmp_path / "discovery_repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "alpha.md").write_text(
        """---
type: design
status: active
depends_on:
  - beta.md
---
# Alpha

alpha launch policy
""",
        encoding="utf-8",
    )
    (repo / "docs" / "beta.md").write_text(
        """---
type: design
status: active
---
# Beta

beta side policy
""",
        encoding="utf-8",
    )
    (repo / "docs" / "gamma.md").write_text(
        """---
type: design
status: active
---
# Gamma

gamma adjacent policy
""",
        encoding="utf-8",
    )
    config = {"include_extensions": [".md"], "exclude_patterns": [], "node_type_map": {"design": ["docs"]}}
    db_path = tmp_path / "discovery.db"
    _build_db(repo, config, db_path)

    result = select_context("alpha launch", str(db_path), budget=4000, limit=1, actionable=True)

    read_ids = {item["id"] for item in result["recommended_read_order"]}
    discovery = result["discovery_candidates"]
    assert discovery
    assert not (read_ids & {item["id"] for item in discovery})
    assert discovery[0]["reason_code"] in {
        "shared_dependencies",
        "shared_links",
        "stale_but_related",
        "orphan_nearby",
        "recently_updated_neighbor",
        "same_type_same_project",
    }
    assert discovery[0]["score_breakdown"]["total"] == discovery[0]["score"]


def test_multi_index_context_splits_budget_across_indexes(tmp_path: Path) -> None:
    repo = tmp_path / "multi_repo"
    repo.mkdir()
    mdex_dir = repo / ".mdex"
    mdex_dir.mkdir()
    task_repo = tmp_path / "task_repo"
    task_repo.mkdir()
    large_body = "shared multi index term\n" + ("filler " * 1200)
    (repo / "repo.md").write_text(f"# Repo\n\n{large_body}\n", encoding="utf-8")
    (task_repo / "task.md").write_text(f"# Task\n\n{large_body}\n", encoding="utf-8")
    config = {"include_extensions": [".md"], "exclude_patterns": []}
    repo_db = mdex_dir / "mdex_index.db"
    task_db = mdex_dir / "task_history.db"
    _build_db(repo, config, repo_db)
    _build_db(task_repo, config, task_db)

    payload = build_multi_context_payload(
        "shared multi index term",
        {"path": str(repo_db), "source": "arg", "repo_root": str(repo), "config": {}},
        include="repo,task",
        budget=100,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )

    assert payload["budget"] == 100
    assert payload["total_tokens"] <= 100
    assert payload["budget_dropped_nodes"]
    assert set(payload["per_index_context"]) == {"repo", "task"}
    assert all(item["budget"] <= 50 for item in payload["per_index_context"].values())


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


def test_select_context_zero_hits_disclosure(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_zero_hits.db"
    _build_db(quality_repo, quality_config, db_path)

    result = select_context("qzxunmatchedterm", str(db_path), budget=4000, limit=10)
    assert result["nodes"] == []
    zero_hits = result["zero_hits"]
    assert zero_hits["lanes_searched"] == ["metadata"]
    assert zero_hits["lanes_inactive"] == {"body_text": "documented_non_goal"}
    assert "not evidence" in zero_hits["caveat"]
    assert "rg -n" in zero_hits["remediation"]
    assert "qzxunmatchedterm" in zero_hits["remediation"]
    assert "if cdex is available" in zero_hits["remediation"]


def test_select_context_does_not_claim_zero_hits_without_a_searched_zero(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quality_context_no_zero_hits.db"
    _build_db(quality_repo, quality_config, db_path)

    assert "zero_hits" not in select_context("", str(db_path), budget=4000, limit=10)
    assert "zero_hits" not in select_context("root decision", str(db_path), budget=4000, limit=10)


def test_multi_index_context_zero_hits_disclosure(tmp_path: Path) -> None:
    repo = tmp_path / "multi_zero_repo"
    repo.mkdir()
    mdex_dir = repo / ".mdex"
    mdex_dir.mkdir()
    task_repo = tmp_path / "multi_zero_task_repo"
    task_repo.mkdir()
    (repo / "repo.md").write_text("# Repo\n\nshared multi index term\n", encoding="utf-8")
    (task_repo / "task.md").write_text("# Task\n\nshared multi index term\n", encoding="utf-8")
    config = {"include_extensions": [".md"], "exclude_patterns": []}
    repo_db = mdex_dir / "mdex_index.db"
    task_db = mdex_dir / "task_history.db"
    _build_db(repo, config, repo_db)
    _build_db(task_repo, config, task_db)
    db_info = {"path": str(repo_db), "source": "arg", "repo_root": str(repo), "config": {}}

    unmatched = build_multi_context_payload(
        "qzxunmatchedterm",
        db_info,
        include="repo,task",
        budget=4000,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )
    assert unmatched["nodes"] == []
    assert unmatched["zero_hits"]["lanes_searched"] == ["metadata"]
    assert unmatched["zero_hits"]["lanes_inactive"] == {"body_text": "documented_non_goal"}

    matched = build_multi_context_payload(
        "shared multi index term",
        db_info,
        include="repo,task",
        budget=4000,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )
    assert matched["nodes"]
    assert "zero_hits" not in matched


def test_multi_index_context_budget_trimmed_hit_does_not_claim_zero_hits(tmp_path: Path) -> None:
    repo = tmp_path / "multi_trim_repo"
    repo.mkdir()
    mdex_dir = repo / ".mdex"
    mdex_dir.mkdir()
    task_repo = tmp_path / "multi_trim_task_repo"
    task_repo.mkdir()
    large_body = "alphaonly probe target\n" + ("filler " * 1200)
    (repo / "repo.md").write_text(f"# Alphaonly probe target\n\n{large_body}\n", encoding="utf-8")
    (task_repo / "task.md").write_text("# Unrelated\n\nnothing to see here\n", encoding="utf-8")
    config = {"include_extensions": [".md"], "exclude_patterns": []}
    repo_db = mdex_dir / "mdex_index.db"
    task_db = mdex_dir / "task_history.db"
    _build_db(repo, config, repo_db)
    _build_db(task_repo, config, task_db)

    payload = build_multi_context_payload(
        "alphaonly",
        {"path": str(repo_db), "source": "arg", "repo_root": str(repo), "config": {}},
        include="repo,task",
        budget=1,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )

    # One index bounded a true zero, the other matched but lost everything to
    # the budget: that is truncation accounting, not a searched zero.
    assert payload["nodes"] == []
    assert "zero_hits" in payload["per_index_context"]["task"]
    assert "zero_hits" not in payload["per_index_context"]["repo"]
    assert "zero_hits" not in payload


def test_multi_index_context_missing_index_does_not_claim_zero_hits(tmp_path: Path) -> None:
    repo = tmp_path / "multi_missing_repo"
    repo.mkdir()
    mdex_dir = repo / ".mdex"
    mdex_dir.mkdir()
    (repo / "repo.md").write_text("# Repo\n\nshared term\n", encoding="utf-8")
    config = {"include_extensions": [".md"], "exclude_patterns": []}
    repo_db = mdex_dir / "mdex_index.db"
    _build_db(repo, config, repo_db)

    payload = build_multi_context_payload(
        "qzxunmatchedterm",
        {"path": str(repo_db), "source": "arg", "repo_root": str(repo), "config": {}},
        include="repo,task",
        budget=4000,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )

    # The task index was requested but never searched, so the zero stays
    # unbounded and must not be claimed at the top level.
    assert payload["nodes"] == []
    assert payload["multi_index"]["indexes"]["task"]["ok"] is False
    assert "zero_hits" not in payload
