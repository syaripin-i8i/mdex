from __future__ import annotations

from pathlib import Path

import pytest

from mdex.reader import NodePathError, read_node_text, resolve_node_path, validate_node_id


def test_validate_node_id_rejects_empty_absolute_and_parent() -> None:
    with pytest.raises(NodePathError, match="node id is required"):
        validate_node_id("")

    absolute = str(Path(__file__).resolve())
    with pytest.raises(NodePathError, match="absolute paths are not allowed"):
        validate_node_id(absolute)

    with pytest.raises(NodePathError, match="path traversal"):
        validate_node_id("../outside.md")


def test_resolve_node_path_and_read_node_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    note = root / "docs" / "a.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("hello\n", encoding="utf-8")

    resolved = resolve_node_path(str(root), "docs/a.md")
    assert resolved == note.resolve()
    assert read_node_text(str(root), "docs/a.md") == "hello\n"
    assert resolve_node_path(str(root), "docs/missing.md") is None


def test_resolve_node_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    link_path = root / "linked.md"

    try:
        link_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available in this environment")

    with pytest.raises(NodePathError) as exc_info:
        resolve_node_path(str(root), "linked.md")

    assert exc_info.value.error == "path containment violation"
    assert exc_info.value.detail == "resolved path escapes scan_root"
