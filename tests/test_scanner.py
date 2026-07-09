from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdex import path_identity, scanner
from mdex.scanner import list_indexable_files


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _relative_files(root: Path, patterns: list[str]) -> list[str]:
    return [
        path.relative_to(root).as_posix()
        for path in list_indexable_files(root, include_extensions=[".md"], exclude_patterns=patterns)
    ]


def test_double_star_directory_exclude_matches_repo_root_directory(tmp_path: Path) -> None:
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")

    assert _relative_files(tmp_path, ["**/.pytest_cache/**"]) == ["docs/keep.md"]


def test_double_star_directory_exclude_still_matches_nested_directory(tmp_path: Path) -> None:
    (tmp_path / "pkg" / ".pytest_cache").mkdir(parents=True)
    (tmp_path / "pkg" / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")
    (tmp_path / "pkg" / "keep.md").write_text("keep\n", encoding="utf-8")

    assert _relative_files(tmp_path, ["**/.pytest_cache/**"]) == ["pkg/keep.md"]


def test_default_excludes_skip_local_config_files(tmp_path: Path) -> None:
    (tmp_path / "control").mkdir()
    (tmp_path / "control" / "scan_config.local.json").write_text("{}", encoding="utf-8")
    (tmp_path / "control" / "scan_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "secrets.json").write_text("{}", encoding="utf-8")
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")

    files = [
        path.relative_to(tmp_path).as_posix()
        for path in list_indexable_files(tmp_path, include_extensions=[".json"], exclude_patterns=[])
    ]

    assert files == ["control/scan_config.json"]


def test_default_excludes_skip_generated_release_lock_catalog(tmp_path: Path) -> None:
    lock_dir = tmp_path / ".github" / "locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "pypi_release_hashes.json").write_text("{}", encoding="utf-8")
    (tmp_path / "keep.json").write_text("{}", encoding="utf-8")

    files = [
        path.relative_to(tmp_path).as_posix()
        for path in list_indexable_files(tmp_path, include_extensions=[".json"])
    ]

    assert files == ["keep.json"]


def test_default_excludes_can_be_disabled_explicitly(tmp_path: Path) -> None:
    (tmp_path / "scan_config.local.json").write_text("{}", encoding="utf-8")

    files = [
        path.relative_to(tmp_path).as_posix()
        for path in list_indexable_files(
            tmp_path,
            include_extensions=[".json"],
            exclude_patterns=[],
            use_default_exclude_patterns=False,
        )
    ]

    assert files == ["scan_config.local.json"]


def test_default_excludes_skip_environment_cache_and_build_directories(tmp_path: Path) -> None:
    excluded_directories = (
        ".venv",
        "venv",
        "env",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "package.egg-info",
        "dist",
        "build",
    )
    for directory in excluded_directories:
        path = tmp_path / directory
        path.mkdir()
        (path / "ignored.md").write_text("ignored\n", encoding="utf-8")

    site_packages = tmp_path / "lib" / "python" / "site-packages" / "dependency"
    site_packages.mkdir(parents=True)
    (site_packages / "ignored.md").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")

    assert _relative_files(tmp_path, []) == ["docs/keep.md"]


def test_excluded_directory_is_pruned_before_walk_descends(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.md").write_text("keep\n", encoding="utf-8")
    directories_after_pruning: list[str] = []

    def _walk(
        _root: Path,
        *,
        topdown: bool,
        followlinks: bool,
        onerror: object,
    ) -> object:
        assert topdown is True
        assert followlinks is False
        assert callable(onerror)
        directory_names = [".venv", "docs"]
        yield str(tmp_path), directory_names, []
        directories_after_pruning.extend(directory_names)
        yield str(tmp_path / "docs"), [], ["keep.md"]

    monkeypatch.setattr(scanner.os, "walk", _walk)

    assert _relative_files(tmp_path, []) == ["docs/keep.md"]
    assert directories_after_pruning == ["docs"]


def test_walk_permission_error_is_not_treated_as_an_empty_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _walk(
        _root: Path,
        *,
        topdown: bool,
        followlinks: bool,
        onerror: object,
    ) -> list[object]:
        assert topdown is True
        assert followlinks is False
        assert callable(onerror)
        onerror(PermissionError("scan denied"))
        return []

    monkeypatch.setattr(scanner.os, "walk", _walk)

    with pytest.raises(PermissionError, match="scan denied"):
        list_indexable_files(tmp_path)


def test_portable_root_aliases_are_rejected_before_deduplication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "Docs"
    second = tmp_path / "docs-other"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(path_identity, "canonical_path_key", lambda _path: "same-key")

    with pytest.raises(OSError, match="case- or Unicode-equivalent"):
        list_indexable_files([first, second])


def test_file_glob_does_not_prune_same_named_directory(tmp_path: Path) -> None:
    directory = tmp_path / "archive.md"
    directory.mkdir()
    (directory / "keep.txt").write_text("keep\n", encoding="utf-8")

    files = [
        path.relative_to(tmp_path).as_posix()
        for path in list_indexable_files(
            tmp_path,
            include_extensions=[".txt"],
            exclude_patterns=["*.md"],
            use_default_exclude_patterns=False,
        )
    ]

    assert files == ["archive.md/keep.txt"]


def test_repo_and_task_scan_configs_keep_task_history_in_separate_lane() -> None:
    repo_config = json.loads(
        (PROJECT_ROOT / "control" / "scan_config.json").read_text(encoding="utf-8")
    )
    task_config = json.loads(
        (PROJECT_ROOT / "control" / "task_scan_config.json").read_text(encoding="utf-8")
    )

    assert "tasks/**" in repo_config["exclude_patterns"]
    assert repo_config["index_kind"] == "repo"
    assert task_config["index_kind"] == "task"
    assert task_config["scan_roots"] == ["tasks"]
    assert task_config["include_extensions"] == [".md"]
    assert task_config["use_default_exclude_patterns"] is True
    assert task_config["output_file"] == ".mdex/task_history.json"
