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
        if args == ("diff", "--name-only", "--cached", "-z"):
            return _completed(["git", *args], 0, "src/日本語 file.py\0src/shared.py\0")
        if args == ("diff", "--name-only", "-z"):
            return _completed(["git", *args], 0, "src/shared.py\0notes/quote'name.md\0")
        if args == ("ls-files", "--others", "--exclude-standard", "-z"):
            return _completed(["git", *args], 0, "docs/new file.md\0")
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(gittools, "_run_git", _fake_run)
    changed = collect_changed_files(repo)
    assert changed == [
        "src/日本語 file.py",
        "src/shared.py",
        "notes/quote'name.md",
        "docs/new file.md",
    ]


def test_collect_paths_preserves_newlines_spaces_and_quotes() -> None:
    result = _completed(
        ["git", "diff", "--name-only", "-z"],
        0,
        "docs/line\nbreak.md\0docs/space file.md\0notes/quote'name.md\0",
    )

    assert gittools._collect_paths(result) == [
        "docs/line\nbreak.md",
        "docs/space file.md",
        "notes/quote'name.md",
    ]


def test_collect_changed_files_round_trips_real_git_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def _git(*args: str) -> None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        except FileNotFoundError:
            pytest.skip("git is not installed")
        assert result.returncode == 0, result.stderr

    _git("init")
    _git("config", "user.name", "mdex test")
    _git("config", "user.email", "mdex@example.invalid")
    _git("config", "commit.gpgsign", "false")

    tracked = repo / "src" / "日本語 space.py"
    tracked.parent.mkdir()
    tracked.write_text("before\n", encoding="utf-8")
    _git("add", "--", tracked.relative_to(repo).as_posix())
    _git("commit", "--no-verify", "-m", "baseline")

    tracked.write_text("after\n", encoding="utf-8")
    staged = repo / "docs" / "追加 staged.md"
    staged.parent.mkdir()
    staged.write_text("staged\n", encoding="utf-8")
    _git("add", "--", staged.relative_to(repo).as_posix())
    untracked = repo / "notes" / "quote'name.md"
    untracked.parent.mkdir()
    untracked.write_text("untracked\n", encoding="utf-8")

    assert collect_changed_files(repo) == [
        "docs/追加 staged.md",
        "src/日本語 space.py",
        "notes/quote'name.md",
    ]


def test_collect_changed_files_raises_when_git_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gittools, "git_top_level", lambda _base: None)
    with pytest.raises(GitError, match="not a git repository"):
        collect_changed_files(tmp_path, require_git=True)

    assert collect_changed_files(tmp_path, require_git=False) == []
