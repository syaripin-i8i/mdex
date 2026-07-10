from __future__ import annotations

import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from mdex import cli
from mdex.builder import build_index
from mdex.indexer import write_sqlite
from mdex.parser import parse_file
from mdex.resolver import prerequisite_order, related_nodes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _node_map(index: dict[str, object]) -> dict[str, dict[str, object]]:
    nodes = index.get("nodes", [])
    assert isinstance(nodes, list)
    output: dict[str, dict[str, object]] = {}
    for node in nodes:
        assert isinstance(node, dict)
        output[str(node.get("id", ""))] = node
    return output


def _edges(index: dict[str, object]) -> list[dict[str, object]]:
    edges = index.get("edges", [])
    assert isinstance(edges, list)
    output: list[dict[str, object]] = []
    for edge in edges:
        assert isinstance(edge, dict)
        output.append(edge)
    return output


def _run_cli(*args: str, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    existing_pythonpath = merged_env.get("PYTHONPATH", "")
    merged_env["PYTHONPATH"] = str(PROJECT_ROOT) if not existing_pythonpath else f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
    return subprocess.run(
        [sys.executable, "-m", "mdex.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        env=merged_env,
    )


def _count_resolution(entries: list[dict[str, object]]) -> tuple[int, int]:
    resolved = sum(1 for entry in entries if bool(entry.get("resolved", False)))
    unresolved = len(entries) - resolved
    return resolved, unresolved


def test_frontmatter_with_and_without_header(fixture_repo: Path) -> None:
    with_frontmatter = parse_file(str(fixture_repo / "docs" / "source.md"))
    assert with_frontmatter["frontmatter"]["project"] == "alpha"
    assert with_frontmatter["frontmatter"]["status"] == "active"

    no_frontmatter = parse_file(str(fixture_repo / "docs" / "no_frontmatter.md"))
    assert no_frontmatter["frontmatter"] == {}


def test_inline_metadata_completion(fixture_repo: Path) -> None:
    parsed = parse_file(str(fixture_repo / "docs" / "inline_task.md"))
    frontmatter = parsed["frontmatter"]
    assert frontmatter["project"] == "beta"
    assert frontmatter["status"] == "draft"
    assert frontmatter["tags"] == ["tag1", "tag2"]
    assert frontmatter["depends_on"] == ["dep.md", "missing_dep.md"]
    assert frontmatter["relates_to"] == ["related.md"]


def test_link_path_task_and_summary_extraction(fixture_repo: Path) -> None:
    parsed = parse_file(
        str(fixture_repo / "docs" / "source.md"),
        options={"summary_max_sentences": 2, "summary_max_chars": 120},
    )
    assert parsed["wikilinks"] == ["shared", "missing-note"]
    assert parsed["md_links"] == ["dep.md", "missing.md"]
    assert "C:\\Codex\\repo\\docs\\dep.md" in parsed["path_refs"]
    assert parsed["task_refs"] == ["T20260101010101"]
    assert parsed["summary"] == "First sentence.Second sentence!"


def test_json_and_jsonl_log_parsing(fixture_repo: Path) -> None:
    session = parse_file(str(fixture_repo / "logs" / "session.json"))
    assert session["title"] == "Assistant Session"
    assert session["frontmatter"]["type"] == "log"
    assert session["frontmatter"]["project"] == "alpha"
    assert session["frontmatter"]["links_to"] == ["docs/source.md"]
    assert "assistant" in session["tags"]
    assert "docs/source.md" in session["path_refs"]
    assert "docs/dep.md" in session["path_refs"]
    assert session["summary"]

    events = parse_file(str(fixture_repo / "logs" / "events.jsonl"))
    assert events["title"] == "events"
    assert events["updated"].startswith("2026-01-03T10:00:00")
    assert "docs/source.md" in events["path_refs"]
    assert "docs/consumer.md" in events["path_refs"]
    assert events["summary"]


def test_type_inference(build_config: dict[str, object], fixture_repo: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    node_map = _node_map(index)

    assert node_map["docs/source.md"]["type"] == "design"
    assert node_map["docs/inline_task.md"]["type"] == "task"
    assert node_map["specs/requirement.md"]["type"] == "spec"


def test_json_nodes_are_indexed_and_resolved(build_config: dict[str, object], fixture_repo: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    node_map = _node_map(index)
    edges = _edges(index)

    assert "logs/session.json" in node_map
    assert "logs/events.jsonl" in node_map
    assert node_map["logs/session.json"]["type"] == "log"
    assert node_map["logs/session.json"]["project"] == "alpha"
    assert any(
        edge["from"] == "logs/session.json"
        and edge["to"] == "docs/source.md"
        and edge["type"] == "links_to"
        and edge["resolved"] is True
        for edge in edges
    )
    assert any(
        edge["from"] == "logs/events.jsonl"
        and edge["to"] == "docs/consumer.md"
        and edge["resolved"] is True
        for edge in edges
    )


def test_status_correction(build_config: dict[str, object], fixture_repo: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    node_map = _node_map(index)

    assert node_map["tasks/done/T20260101010101.md"]["status"] == "done"
    assert node_map["tasks/pending/T20260101010102.md"]["status"] == "pending"


def test_target_resolution_and_unresolved_marking(
    build_config: dict[str, object],
    fixture_repo: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    edges = _edges(index)

    assert any(
        edge["from"] == "docs/source.md"
        and edge["to"] == "docs/dep.md"
        and edge["type"] == "links_to"
        and edge["resolved"] is True
        for edge in edges
    )
    assert any(
        edge["from"] == "docs/source.md"
        and edge["to"] == "docs/shared.md"
        and edge["type"] == "links_to"
        and edge["resolved"] is True
        for edge in edges
    )
    assert any(
        edge["from"] == "docs/source.md"
        and edge["to"] == "missing.md"
        and edge["type"] == "links_to"
        and edge["resolved"] is False
        for edge in edges
    )
    assert any(
        edge["from"] == "docs/source.md"
        and edge["to"] == "tasks/done/T20260101010101.md"
        and edge["type"] == "relates_to"
        and edge["resolved"] is True
        for edge in edges
    )

    unresolved = [edge for edge in edges if edge["resolved"] is False]
    assert unresolved


def test_external_absolute_targets_are_not_added_to_edges(tmp_path: Path) -> None:
    repo = tmp_path / "external_target_repo"
    repo.mkdir()
    outside = tmp_path / "outside_target.md"
    outside.write_text("# outside\n", encoding="utf-8")

    source = repo / "source.md"
    source.write_text(
        (
            "---\n"
            "links_to:\n"
            f'  - "{outside.resolve().as_posix()}"\n'
            "---\n"
            "# Source\n\n"
            "absolute external link test\n"
        ),
        encoding="utf-8",
    )

    config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {},
        "summary_max_sentences": 2,
        "summary_max_chars": 120,
    }
    index = build_index(str(repo), config)
    edges = _edges(index)

    assert any(node.get("id") == "source.md" for node in index.get("nodes", []))
    assert edges == []


def test_scan_warns_when_local_config_is_explicitly_indexed(tmp_path: Path) -> None:
    repo = tmp_path / "local_config_repo"
    repo.mkdir()
    (repo / "settings.local.json").write_text('{"message":"local only"}\n', encoding="utf-8")

    config = {
        "include_extensions": [".json"],
        "exclude_patterns": [],
        "use_default_exclude_patterns": False,
    }
    index = build_index(str(repo), config)
    node_map = _node_map(index)
    warnings = index.get("warnings", [])

    assert "settings.local.json" in node_map
    assert any(
        isinstance(warning, dict)
        and warning.get("path") == "settings.local.json"
        and "suspicious indexed local" in str(warning.get("error", ""))
        for warning in warnings
    )


def test_query_direction_preserved(build_config: dict[str, object], fixture_repo: Path, tmp_path: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_test.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("query", "--db", str(db_path), "--node", "docs/source.md")
    assert result.returncode == 0
    payload = json.loads(result.stdout)

    outgoing_depends_ids = [item["id"] for item in payload["outgoing"]["depends_on"]]
    incoming_depends_ids = [item["id"] for item in payload["incoming"]["depends_on"]]
    outgoing_links = payload["outgoing"]["links_to"]

    assert "docs/dep.md" in outgoing_depends_ids
    assert "docs/consumer.md" in incoming_depends_ids
    assert any(item["id"] == "missing.md" and item["resolved"] is False for item in outgoing_links)

    outgoing_entries = [
        entry
        for entries in payload["outgoing"].values()
        for entry in entries
        if isinstance(entry, dict)
    ]
    incoming_entries = [
        entry
        for entries in payload["incoming"].values()
        for entry in entries
        if isinstance(entry, dict)
    ]
    outgoing_resolved, outgoing_unresolved = _count_resolution(outgoing_entries)
    incoming_resolved, incoming_unresolved = _count_resolution(incoming_entries)

    assert payload["stats"]["outgoing_resolved"] == outgoing_resolved
    assert payload["stats"]["outgoing_unresolved"] == outgoing_unresolved
    assert payload["stats"]["incoming_resolved"] == incoming_resolved
    assert payload["stats"]["incoming_unresolved"] == incoming_unresolved

    positional = _run_cli("query", "--db", str(db_path), "docs/source.md")
    assert positional.returncode == 0
    positional_payload = json.loads(positional.stdout)
    assert positional_payload["node"]["id"] == "docs/source.md"


def test_related_uses_tag_and_type_signals(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_related.db"
    write_sqlite(index, str(db_path))

    results = related_nodes("docs/source.md", str(db_path), limit=50)
    result_map = {item["id"]: item for item in results}

    assert "docs/tag_peer.md" in result_map
    assert "docs/type_peer.md" in result_map
    assert "missing.md" not in result_map
    assert any(reason.startswith("shared_tags:") for reason in result_map["docs/tag_peer.md"]["reasons"])
    assert "same_type" in result_map["docs/type_peer.md"]["reasons"]


def test_find_matches_title_summary_and_tags(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_find.db"
    write_sqlite(index, str(db_path))

    title_match = _run_cli("find", "source", "--db", str(db_path), "--limit", "20")
    assert title_match.returncode == 0
    title_payload = json.loads(title_match.stdout)
    assert any(item["id"] == "docs/source.md" for item in title_payload)

    tag_match = _run_cli("find", "core", "--db", str(db_path), "--limit", "20")
    assert tag_match.returncode == 0
    tag_payload = json.loads(tag_match.stdout)
    tag_ids = [item["id"] for item in tag_payload]
    assert "docs/source.md" in tag_ids
    assert "docs/tag_peer.md" in tag_ids

    no_match = _run_cli("find", "no-such-keyword", "--db", str(db_path), "--limit", "20")
    assert no_match.returncode == 0
    assert json.loads(no_match.stdout) == []

    table_match = _run_cli("find", "source", "--db", str(db_path), "--format", "table", "--limit", "20")
    assert table_match.returncode == 0
    assert "docs/source.md\tSource Doc\tdesign\tactive" in table_match.stdout


def test_find_zero_hits_discloses_on_stderr_and_keeps_stdout_contract(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_find_zero_hits.db"
    write_sqlite(index, str(db_path))

    no_match = _run_cli("find", "qzxunmatchedterm", "--db", str(db_path))
    assert no_match.returncode == 0
    assert json.loads(no_match.stdout) == []
    disclosure = json.loads(no_match.stderr)
    zero_hits = disclosure["zero_hits"]
    assert zero_hits["lanes_searched"] == ["metadata"]
    assert zero_hits["lanes_inactive"] == {"body_text": "documented_non_goal"}
    assert "not evidence" in zero_hits["caveat"]
    assert "qzxunmatchedterm" in zero_hits["remediation"]

    matched = _run_cli("find", "source", "--db", str(db_path), "--limit", "20")
    assert matched.returncode == 0
    assert matched.stderr.strip() == ""

    blank = _run_cli("find", "", "--db", str(db_path))
    assert blank.returncode == 0
    assert json.loads(blank.stdout) == []
    assert blank.stderr.strip() == ""


def test_orphans_lists_nodes_without_resolved_edges(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_orphans.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("orphans", "--db", str(db_path))
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    orphan_ids = [item["id"] for item in payload]

    assert "docs/no_frontmatter.md" in orphan_ids
    assert "docs/source.md" not in orphan_ids

    table_output = _run_cli("orphans", "--db", str(db_path), "--format", "table")
    assert table_output.returncode == 0
    assert "docs/no_frontmatter.md\tPlain Note\tunknown\tunknown" in table_output.stdout

    missing_output = _run_cli("orphans", "--db", str(db_path), "--missing")
    assert missing_output.returncode == 0
    missing_payload = json.loads(missing_output.stdout)
    missing_map = {item["id"]: item for item in missing_payload}
    assert missing_map["missing-note.md"]["referenced_by"] == ["docs/source.md"]
    assert missing_map["missing.md"]["referenced_by"] == ["docs/source.md"]

    missing_table = _run_cli("orphans", "--db", str(db_path), "--missing", "--format", "table")
    assert missing_table.returncode == 0
    assert "missing-note.md\t1\tdocs/source.md" in missing_table.stdout


def test_list_json_format(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_list_json.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("list", "--db", str(db_path))
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert any(item.get("id") == "docs/source.md" for item in payload)
    assert any("title" in item for item in payload)

    table_result = _run_cli("list", "--db", str(db_path), "--format", "table")
    assert table_result.returncode == 0
    assert "docs/source.md\tSource Doc\tdesign\tactive" in table_result.stdout


def test_first_prerequisite_order_and_cycle_safety(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_first.db"
    write_sqlite(index, str(db_path))

    prerequisites = prerequisite_order("docs/first_a.md", str(db_path), limit=10)
    prereq_ids = [item["id"] for item in prerequisites]
    assert prereq_ids == ["docs/first_c.md", "docs/first_b.md"]

    first_output = _run_cli("first", "docs/first_a.md", "--db", str(db_path), "--limit", "10")
    assert first_output.returncode == 0
    payload = json.loads(first_output.stdout)
    cli_ids = [item["id"] for item in payload["prerequisites"]]
    assert cli_ids == ["docs/first_c.md", "docs/first_b.md"]

    cycle_result = prerequisite_order("docs/cycle_x.md", str(db_path), limit=10)
    cycle_ids = [item["id"] for item in cycle_result]
    assert "docs/cycle_x.md" not in cycle_ids
    assert len(cycle_ids) <= 1


def test_scan_outputs_json_summary(build_config: dict[str, object], fixture_repo: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "scan_config.json"
    config_path.write_text(json.dumps(build_config), encoding="utf-8")
    output_json = tmp_path / "scan_out.json"
    output_db = tmp_path / "scan_out.db"

    result = _run_cli(
        "scan",
        "--root",
        str(fixture_repo),
        "--config",
        str(config_path),
        "--output",
        str(output_json),
        "--db",
        str(output_db),
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload.get("nodes"), int)
    assert payload["edges"]["total"] >= payload["edges"]["resolved"]
    assert payload["output"]["json"] == str(output_json)
    assert payload["output"]["db"] == str(output_db)
    assert result.stderr.strip() == ""


def test_scan_does_not_refresh_json_when_sqlite_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "scan_failure_repo"
    repo.mkdir()
    (repo / "keep.md").write_text("# Keep\n", encoding="utf-8")
    config_path = tmp_path / "scan_config.json"
    config_path.write_text(
        json.dumps({"include_extensions": [".md"], "exclude_patterns": []}),
        encoding="utf-8",
    )
    output_json = tmp_path / "scan_out.json"
    output_db = tmp_path / "scan_out.db"

    def _fail_write_scan_outputs(
        index: dict[str, object],
        db_path: str,
        json_path: str,
        **_kwargs: object,
    ) -> None:
        raise OSError("db locked")

    monkeypatch.setattr(cli, "write_scan_outputs", _fail_write_scan_outputs)

    result = cli._cmd_scan(
        Namespace(
            root=str(repo),
            config=str(config_path),
            output=str(output_json),
            db=str(output_db),
            strict=False,
            incremental=False,
            node_id_root=None,
        )
    )

    assert result == 2
    assert not output_json.exists()


def test_scan_rejects_config_changed_while_index_is_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "config_race_repo"
    repo.mkdir()
    (repo / "keep.md").write_text("# Keep\n", encoding="utf-8")
    config_path = tmp_path / "scan_config.json"
    config_path.write_text(
        json.dumps({"include_extensions": [".md"], "exclude_patterns": []}),
        encoding="utf-8",
    )
    output_json = tmp_path / "scan_out.json"
    output_db = tmp_path / "scan_out.db"
    original_build_index = cli.build_index

    def _build_then_change_config(*args: object, **kwargs: object) -> dict[str, object]:
        index = original_build_index(*args, **kwargs)
        config_path.write_text(
            json.dumps({"include_extensions": [".md"], "exclude_patterns": ["keep.md"]}),
            encoding="utf-8",
        )
        return index

    monkeypatch.setattr(cli, "build_index", _build_then_change_config)

    result = cli._cmd_scan(
        Namespace(
            root=str(repo),
            config=str(config_path),
            output=str(output_json),
            db=str(output_db),
            strict=False,
            incremental=False,
            node_id_root=None,
        )
    )

    assert result == 2
    assert not output_db.exists()
    assert not output_json.exists()


def test_scan_rejects_root_identity_changed_while_index_is_built(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "root_race_repo"
    repo.mkdir()
    docs = repo / "docs"
    docs.mkdir()
    (docs / "keep.md").write_text("# Keep\n", encoding="utf-8")
    config_path = tmp_path / "scan_config.json"
    config_path.write_text(
        json.dumps({"include_extensions": [".md"], "exclude_patterns": []}),
        encoding="utf-8",
    )
    output_json = tmp_path / "scan_out.json"
    output_db = tmp_path / "scan_out.db"
    original_build_index = cli.build_index

    def _build_then_move_root(*args: object, **kwargs: object) -> dict[str, object]:
        index = original_build_index(*args, **kwargs)
        docs.rename(repo / "moved-docs")
        return index

    monkeypatch.setattr(cli, "build_index", _build_then_move_root)

    result = cli._cmd_scan(
        Namespace(
            root=str(docs),
            config=str(config_path),
            output=str(output_json),
            db=str(output_db),
            strict=False,
            incremental=False,
            node_id_root=None,
        )
    )

    assert result == 2
    assert not output_db.exists()
    assert not output_json.exists()


def test_scan_rejects_stale_build_after_concurrent_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "scan_cas_repo"
    repo.mkdir()
    (repo / "keep.md").write_text("# Keep\n", encoding="utf-8")
    config_path = tmp_path / "scan_config.json"
    config_path.write_text(
        json.dumps({"include_extensions": [".md"], "exclude_patterns": []}),
        encoding="utf-8",
    )
    output_json = tmp_path / "scan_out.json"
    output_db = tmp_path / "scan_out.db"
    real_write = cli.write_scan_outputs
    injected = False

    def _write_after_concurrent_scan(
        index: dict[str, object],
        db_path: str,
        json_path: str,
        **kwargs: object,
    ) -> None:
        nonlocal injected
        if not injected:
            injected = True
            real_write(index, db_path, json_path)
        real_write(index, db_path, json_path, **kwargs)

    monkeypatch.setattr(cli, "write_scan_outputs", _write_after_concurrent_scan)

    result = cli._cmd_scan(
        Namespace(
            root=str(repo),
            config=str(config_path),
            output=str(output_json),
            db=str(output_db),
            strict=False,
            incremental=False,
            node_id_root=None,
        )
    )

    assert result == 2
    assert output_db.exists()
    assert output_json.exists()


def test_artifact_scan_rejects_stale_build_after_concurrent_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "artifact_scan_cas_repo"
    outputs = repo / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "result.json").write_text('{"status":"ok"}\n', encoding="utf-8")
    output_json = repo / ".mdex" / "artifacts.json"
    output_db = repo / ".mdex" / "artifacts.db"
    real_write = cli.write_scan_outputs
    injected = False

    def _write_after_concurrent_scan(
        index: dict[str, object],
        db_path: str,
        json_path: str,
        **kwargs: object,
    ) -> None:
        nonlocal injected
        if not injected:
            injected = True
            real_write(index, db_path, json_path)
        real_write(index, db_path, json_path, **kwargs)

    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "write_scan_outputs", _write_after_concurrent_scan)

    result = cli._cmd_scan_artifacts(
        Namespace(
            root=[str(outputs)],
            output=str(output_json),
            db=str(output_db),
        )
    )

    assert result == 2
    assert output_db.exists()
    assert output_json.exists()


def test_context_include_content(build_config: dict[str, object], fixture_repo: Path, tmp_path: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_context.db"
    write_sqlite(index, str(db_path))

    slim = _run_cli("context", "source core", "--db", str(db_path), "--limit", "3")
    assert slim.returncode == 0
    slim_payload = json.loads(slim.stdout)
    assert slim_payload["nodes"]
    assert "content" not in slim_payload["nodes"][0]
    assert set(["id", "priority", "score", "estimated_tokens"]).issubset(slim_payload["nodes"][0].keys())

    full = _run_cli(
        "context",
        "source core",
        "--db",
        str(db_path),
        "--limit",
        "3",
        "--include-content",
    )
    assert full.returncode == 0
    full_payload = json.loads(full.stdout)
    assert full_payload["nodes"]
    assert "content" in full_payload["nodes"][0]
    assert isinstance(full_payload["nodes"][0]["content"], str)


def test_context_respects_soft_budget(build_config: dict[str, object], fixture_repo: Path, tmp_path: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_context_budget.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("context", "source core", "--db", str(db_path), "--limit", "10", "--budget", "100")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["total_tokens"] <= 120


def test_enrich_path_reverse_lookup_with_summary_file(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_enrich.db"
    write_sqlite(index, str(db_path))

    absolute_path = str((fixture_repo / "docs" / "source.md").resolve())
    summary_file = tmp_path / "summary.txt"
    summary_text = "Agent-authored summary for cross-session reuse."
    summary_file.write_text(summary_text, encoding="utf-8")

    ok = _run_cli(
        "enrich",
        "--path",
        absolute_path,
        "--db",
        str(db_path),
        "--summary-file",
        str(summary_file),
    )
    assert ok.returncode == 0
    payload = json.loads(ok.stdout)
    assert payload["status"] == "enriched"
    assert payload["node_id"] == "docs/source.md"
    assert payload["summary_source"] == "agent"
    assert payload["skipped"] is False
    assert payload["new_summary"] == summary_text
    assert isinstance(payload["previous_summary"], str)

    bad = _run_cli(
        "enrich",
        "--path",
        str((tmp_path / "no_such.md").resolve()),
        "--db",
        str(db_path),
        "--summary",
        "fallback summary",
    )
    assert bad.returncode == 2
    error_payload = json.loads(bad.stderr)
    assert error_payload["error"] == "node not found"
    assert "path" in error_payload


def test_enrich_skip_and_force_are_source_based(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_enrich_force.db"
    write_sqlite(index, str(db_path))

    first_summary = "Agent summary v1."
    first = _run_cli("enrich", "docs/source.md", "--db", str(db_path), "--summary", first_summary)
    assert first.returncode == 0
    first_payload = json.loads(first.stdout)
    assert first_payload["status"] == "enriched"
    assert first_payload["summary_source"] == "agent"
    assert first_payload["skipped"] is False

    skipped = _run_cli(
        "enrich",
        "docs/source.md",
        "--db",
        str(db_path),
        "--summary",
        "Agent summary v2.",
    )
    assert skipped.returncode == 0
    skipped_payload = json.loads(skipped.stdout)
    assert skipped_payload["status"] == "skipped"
    assert skipped_payload["reason"] == "agent summary already exists"
    assert skipped_payload["summary_source"] == "agent"
    assert skipped_payload["skipped"] is True
    assert skipped_payload["previous_summary"] == first_summary
    assert skipped_payload["new_summary"] == first_summary

    forced_summary = "Agent summary v3."
    forced = _run_cli(
        "enrich",
        "docs/source.md",
        "--db",
        str(db_path),
        "--summary",
        forced_summary,
        "--force",
    )
    assert forced.returncode == 0
    forced_payload = json.loads(forced.stdout)
    assert forced_payload["status"] == "enriched"
    assert forced_payload["summary_source"] == "agent"
    assert forced_payload["skipped"] is False
    assert forced_payload["previous_summary"] == first_summary
    assert forced_payload["new_summary"] == forced_summary


def test_enrich_requires_summary_argument(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_enrich_missing_summary.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("enrich", "docs/source.md", "--db", str(db_path))
    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "invalid arguments"
    assert "summary" in payload["detail"]


def test_stale_lists_seed_nodes_by_age(tmp_path: Path) -> None:
    repo = tmp_path / "stale_cli_repo"
    repo.mkdir()
    config = {
        "exclude_patterns": [],
        "node_type_map": {"design": ["design"]},
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
    db_path = tmp_path / "mdex_stale.db"

    (repo / "stale.md").write_text(
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
    (repo / "fresh.md").write_text(
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
    (repo / "agent.md").write_text(
        """---
type: design
status: active
updated: 2000-01-01
---
# Agent Seed

agent candidate
""",
        encoding="utf-8",
    )

    index = build_index(str(repo), config)
    write_sqlite(index, str(db_path))

    enriched = _run_cli("enrich", "agent.md", "--db", str(db_path), "--summary", "agent override")
    assert enriched.returncode == 0

    stale = _run_cli("stale", "--db", str(db_path), "--days", "30")
    assert stale.returncode == 0
    payload = json.loads(stale.stdout)
    stale_ids = [item["id"] for item in payload]
    assert "stale.md" in stale_ids
    assert "fresh.md" not in stale_ids
    assert "agent.md" not in stale_ids
    assert payload[0]["summary_source"] == "seed"
    assert "updated" in payload[0]

    table = _run_cli("stale", "--db", str(db_path), "--days", "30", "--format", "table")
    assert table.returncode == 0
    assert "stale.md\tStale Seed\tdesign\tactive\tseed\t" in table.stdout
    assert "agent.md\t" not in table.stdout


def test_index_option_removed_from_help() -> None:
    commands = ["list", "query", "open", "related", "stale"]
    for command in commands:
        result = _run_cli(command, "--help")
        assert result.returncode == 0
        assert "--index" not in result.stdout


def test_error_output_is_json(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_error.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("query", "--db", str(db_path), "--node", "docs/no_such_node.md")
    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "node not found"
    assert payload["node_id"] == "docs/no_such_node.md"
