from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_INDEX_EXTENSIONS = (".md", ".json", ".jsonl")


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


def _normalize_extensions(include_extensions: Iterable[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    seen = set()

    raw_values = include_extensions or DEFAULT_INDEX_EXTENSIONS
    for raw_value in raw_values:
        extension = str(raw_value).strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension}"
        if extension in seen:
            continue
        seen.add(extension)
        normalized.append(extension)

    if not normalized:
        return DEFAULT_INDEX_EXTENSIONS
    return tuple(normalized)


def list_indexable_files(
    root: str | Path | Iterable[str | Path],
    include_extensions: Iterable[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[Path]:
    patterns = exclude_patterns or []
    allowed_extensions = set(_normalize_extensions(include_extensions))

    raw_roots: list[Any]
    if isinstance(root, (str, Path)):
        raw_roots = [root]
    else:
        raw_roots = list(root)

    roots: list[Path] = []
    seen_roots = set()
    for raw in raw_roots:
        path = Path(raw).resolve()
        key = path.as_posix().lower()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        roots.append(path)

    indexed_files: list[Path] = []
    for root_path in roots:
        for file_path in root_path.rglob("*"):
            if file_path.is_symlink():
                continue
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in allowed_extensions:
                continue
            relative = _to_posix(str(file_path.relative_to(root_path)))
            if _is_excluded(relative, patterns):
                continue
            resolved = file_path.resolve()
            indexed_files.append(resolved)
    return sorted(indexed_files, key=lambda item: item.as_posix())


def list_markdown_files(
    root: str | Path | Iterable[str | Path],
    exclude_patterns: list[str] | None = None,
) -> list[Path]:
    return list_indexable_files(root, include_extensions=[".md"], exclude_patterns=exclude_patterns)
