from __future__ import annotations

import fnmatch
from pathlib import Path


def _to_posix(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _pattern_variants(pattern: str) -> list[str]:
    normalized = _to_posix(pattern.strip())
    if not normalized:
        return []

    variants = {normalized}
    if not normalized.startswith("**/"):
        variants.add(f"**/{normalized}")

    if normalized.endswith("/**"):
        base = normalized[: -len("/**")].rstrip("/")
        if base:
            variants.add(base)
            if not base.startswith("**/"):
                variants.add(f"**/{base}")

    return sorted(variants)


def _is_excluded(relative_path: str, exclude_patterns: list[str]) -> bool:
    path = _to_posix(relative_path)
    for pattern in exclude_patterns:
        for candidate in _pattern_variants(pattern):
            if fnmatch.fnmatch(path, candidate):
                return True
    return False


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
