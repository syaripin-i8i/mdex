from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from runtime.builder import build_index
from runtime.indexer import write_sqlite
from runtime.parser import parse_file
from runtime.resolver import prerequisite_order, related_nodes


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
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )


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


def test_type_inference(build_config: dict[str, object], fixture_repo: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    node_map = _node_map(index)

    assert node_map["docs/source.md"]["type"] == "design"
    assert node_map["docs/inline_task.md"]["type"] == "task"
    assert node_map["specs/requirement.md"]["type"] == "spec"


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


def test_query_direction_preserved(build_config: dict[str, object], fixture_repo: Path, tmp_path: Path) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_test.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("query", "--db", str(db_path), "--node", "docs/source.md")
    payload = json.loads(result.stdout)

    outgoing_depends_ids = [item["id"] for item in payload["outgoing"]["depends_on"]]
    incoming_depends_ids = [item["id"] for item in payload["incoming"]["depends_on"]]
    outgoing_links = payload["outgoing"]["links_to"]

    assert "docs/dep.md" in outgoing_depends_ids
    assert "docs/consumer.md" in incoming_depends_ids
    assert any(item["id"] == "missing.md" and item["resolved"] is False for item in outgoing_links)


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
    assert "docs/source.md" in title_match.stdout

    tag_match = _run_cli("find", "core", "--db", str(db_path), "--limit", "20")
    assert "docs/source.md" in tag_match.stdout
    assert "docs/tag_peer.md" in tag_match.stdout

    no_match = _run_cli("find", "no-such-keyword", "--db", str(db_path), "--limit", "20")
    assert no_match.stdout.strip() == ""


def test_orphans_lists_nodes_without_resolved_edges(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_orphans.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("orphans", "--db", str(db_path))
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    orphan_ids = [line.split("\t", 1)[0] for line in lines]

    assert "docs/no_frontmatter.md" in orphan_ids
    assert "docs/source.md" not in orphan_ids


def test_list_json_format(
    build_config: dict[str, object],
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    index = build_index(str(fixture_repo), build_config)
    db_path = tmp_path / "mdex_list_json.db"
    write_sqlite(index, str(db_path))

    result = _run_cli("list", "--db", str(db_path), "--format", "json")
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert any(item.get("id") == "docs/source.md" for item in payload)
    assert any("title" in item for item in payload)


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
    payload = json.loads(first_output.stdout)
    cli_ids = [item["id"] for item in payload["prerequisites"]]
    assert cli_ids == ["docs/first_c.md", "docs/first_b.md"]

    cycle_result = prerequisite_order("docs/cycle_x.md", str(db_path), limit=10)
    cycle_ids = [item["id"] for item in cycle_result]
    assert "docs/cycle_x.md" not in cycle_ids
    assert len(cycle_ids) <= 1
