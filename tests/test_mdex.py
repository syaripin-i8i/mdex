from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from runtime.builder import build_index
from runtime.indexer import write_sqlite
from runtime.parser import parse_file


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

    result = subprocess.run(
        [
            sys.executable,
            "runtime/cli.py",
            "query",
            "--db",
            str(db_path),
            "--node",
            "docs/source.md",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    payload = json.loads(result.stdout)

    outgoing_depends_ids = [item["id"] for item in payload["outgoing"]["depends_on"]]
    incoming_depends_ids = [item["id"] for item in payload["incoming"]["depends_on"]]
    outgoing_links = payload["outgoing"]["links_to"]

    assert "docs/dep.md" in outgoing_depends_ids
    assert "docs/consumer.md" in incoming_depends_ids
    assert any(item["id"] == "missing.md" and item["resolved"] is False for item in outgoing_links)
