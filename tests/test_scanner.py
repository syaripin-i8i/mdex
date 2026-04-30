from __future__ import annotations

from pathlib import Path

from mdex.scanner import list_indexable_files


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
