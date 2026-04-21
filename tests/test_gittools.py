from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mdex import gittools
from mdex.gittools import GitError, collect_changed_files, git_top_level, is_git_repo


def _completed(args: list[str], returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr="")


def test_git_top_level_and_is_git_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(
        gittools,
        "_run_git",
        lambda _base, *args: _completed(["git", *args], 0, f"{repo}\n"),
    )
    assert git_top_level(repo) == repo.resolve()
    assert is_git_repo(repo) is True

    monkeypatch.setattr(
        gittools,
        "_run_git",
        lambda _base, *args: _completed(["git", *args], 1, ""),
    )
    assert git_top_level(repo) is None
    assert is_git_repo(repo) is False


def test_collect_changed_files_combines_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def _fake_run(_base: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args == ("rev-parse", "--show-toplevel"):
            return _completed(["git", *args], 0, f"{repo}\n")
        if args == ("diff", "--name-only", "--cached"):
            return _completed(["git", *args], 0, "src\\a.py\nsrc\\b.py\n")
        if args == ("diff", "--name-only"):
            return _completed(["git", *args], 0, "src/b.py\n")
        if args == ("ls-files", "--others", "--exclude-standard"):
            return _completed(["git", *args], 0, "docs/new.md\n")
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(gittools, "_run_git", _fake_run)
    changed = collect_changed_files(repo)
    assert changed == ["src/a.py", "src/b.py", "docs/new.md"]


def test_collect_changed_files_raises_when_git_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gittools, "git_top_level", lambda _base: None)
    with pytest.raises(GitError, match="not a git repository"):
        collect_changed_files(tmp_path, require_git=True)

    assert collect_changed_files(tmp_path, require_git=False) == []
