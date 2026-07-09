from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mdex.path_identity import deduplicate_directory_paths

DEFAULT_INDEX_EXTENSIONS = (".md", ".json", ".jsonl")
DEFAULT_EXCLUDE_PATTERNS = (
    ".git/**",
    ".github/locks/**",
    ".mdex/**",
    ".venv/**",
    "venv/**",
    "env/**",
    ".tox/**",
    ".nox/**",
    "**/site-packages/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "__pycache__/**",
    "*.egg-info/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "outputs/**",
    "tmp/**",
    "**/.tmp/**",
    "**/.pytest_cache/**",
    ".env*",
    "**/.env*",
    "*.local.md",
    "*.local.json",
    "*.local.jsonl",
    "**/*.local.md",
    "**/*.local.json",
    "**/*.local.jsonl",
    "secrets.*",
    "**/secrets.*",
    "credentials.*",
    "**/credentials.*",
)


def _to_posix(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _pattern_variants(pattern: str) -> list[str]:
    normalized = _to_posix(pattern.strip())
    if not normalized:
        return []

    variants = {normalized}
    if normalized.startswith("**/"):
        variants.add(normalized[len("**/") :])
    if not normalized.startswith("**/"):
        variants.add(f"**/{normalized}")

    if normalized.endswith("/**"):
        base = normalized[: -len("/**")].rstrip("/")
        if base:
            variants.add(base)
            if base.startswith("**/"):
                variants.add(base[len("**/") :])
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


def _is_excluded_directory(relative_path: str, exclude_patterns: list[str]) -> bool:
    """Return whether every descendant is excluded by a ``.../**`` pattern.

    File patterns such as ``*.md`` must not prune a directory named
    ``archive.md`` because those patterns did not exclude its descendants in
    the previous rglob-based implementation. Restrict pruning to patterns that
    explicitly cover a directory subtree.
    """

    path = _to_posix(relative_path).rstrip("/")
    if not path:
        return False

    for pattern in exclude_patterns:
        normalized = _to_posix(pattern.strip()).rstrip("/")
        if not normalized.endswith("/**"):
            continue
        directory_pattern = normalized[: -len("/**")].rstrip("/")
        if not directory_pattern:
            continue
        candidates = _pattern_variants(directory_pattern)
        if any(fnmatch.fnmatch(path, candidate) for candidate in candidates):
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
    use_default_exclude_patterns: bool = True,
) -> list[Path]:
    patterns = [
        *(DEFAULT_EXCLUDE_PATTERNS if use_default_exclude_patterns else ()),
        *(exclude_patterns or []),
    ]
    allowed_extensions = set(_normalize_extensions(include_extensions))

    raw_roots: list[Any]
    if isinstance(root, (str, Path)):
        raw_roots = [root]
    else:
        raw_roots = list(root)

    roots = deduplicate_directory_paths(raw_roots)

    indexed_files: list[Path] = []

    def _raise_walk_error(error: OSError) -> None:
        raise error

    for root_path in roots:
        for current_dir, directory_names, file_names in os.walk(
            root_path,
            topdown=True,
            followlinks=False,
            onerror=_raise_walk_error,
        ):
            current_path = Path(current_dir)

            kept_directories: list[str] = []
            for directory_name in directory_names:
                directory_path = current_path / directory_name
                if directory_path.is_symlink():
                    continue
                relative_directory = _to_posix(str(directory_path.relative_to(root_path)))
                if _is_excluded_directory(relative_directory, patterns):
                    continue
                kept_directories.append(directory_name)
            directory_names[:] = kept_directories

            for file_name in file_names:
                file_path = current_path / file_name
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
    use_default_exclude_patterns: bool = True,
) -> list[Path]:
    return list_indexable_files(
        root,
        include_extensions=[".md"],
        exclude_patterns=exclude_patterns,
        use_default_exclude_patterns=use_default_exclude_patterns,
    )
