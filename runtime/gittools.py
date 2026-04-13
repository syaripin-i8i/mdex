from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _run_git(base_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(base_dir), *args],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def git_top_level(base_dir: str | Path) -> Path | None:
    root = Path(base_dir).resolve()
    probe = _run_git(root, "rev-parse", "--show-toplevel")
    if probe.returncode != 0:
        return None
    text = probe.stdout.strip()
    if not text:
        return None
    return Path(text).resolve()


def is_git_repo(base_dir: str | Path) -> bool:
    return git_top_level(base_dir) is not None


def _collect_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    if result.returncode != 0:
        return []
    rows: list[str] = []
    for raw in result.stdout.splitlines():
        clean = raw.strip().replace("\\", "/")
        if clean:
            rows.append(clean)
    return rows


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def collect_changed_files(base_dir: str | Path, *, require_git: bool = False) -> list[str]:
    root = Path(base_dir).resolve()
    git_root = git_top_level(root)
    if git_root is None:
        if require_git:
            raise GitError("not a git repository")
        return []

    staged = _run_git(git_root, "diff", "--name-only", "--cached")
    unstaged = _run_git(git_root, "diff", "--name-only")
    untracked = _run_git(git_root, "ls-files", "--others", "--exclude-standard")
    combined = _collect_lines(staged) + _collect_lines(unstaged) + _collect_lines(untracked)
    return _dedupe_keep_order(combined)
