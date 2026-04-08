from __future__ import annotations

import fnmatch
from pathlib import Path


def _to_posix(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _is_excluded(relative_path: str, exclude_patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in exclude_patterns)


def list_markdown_files(root: str, exclude_patterns: list[str] | None = None) -> list[str]:
    root_path = Path(root).resolve()
    patterns = exclude_patterns or []

    markdown_files: list[str] = []
    for file_path in root_path.rglob("*.md"):
        if not file_path.is_file():
            continue
        relative = _to_posix(str(file_path.relative_to(root_path)))
        if _is_excluded(relative, patterns):
            continue
        markdown_files.append(relative)
    return sorted(markdown_files)
