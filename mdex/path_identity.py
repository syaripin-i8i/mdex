from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Any, Iterable


def canonical_path_key(value: str | Path) -> str:
    """Return a conservative cross-platform identity key for a path.

    Case and Unicode-equivalent names are treated as aliases even on a
    case-sensitive host so a scan plan remains safe when moved to Windows or a
    case-insensitive macOS volume.
    """

    resolved = Path(value).resolve()
    normalized = unicodedata.normalize("NFC", os.path.normcase(str(resolved)))
    return normalized.casefold()


def paths_have_same_identity(first: str | Path, second: str | Path) -> bool:
    if canonical_path_key(first) == canonical_path_key(second):
        return True
    try:
        return Path(first).samefile(Path(second))
    except OSError:
        return False


def deduplicate_directory_paths(paths: Iterable[str | Path]) -> list[Path]:
    """Deduplicate true aliases and reject ambiguous portable-name collisions."""

    result: list[Path] = []
    canonical_paths: dict[str, Path] = {}
    for value in paths:
        path = Path(value).resolve()
        key = canonical_path_key(path)
        previous = canonical_paths.get(key)
        if previous is None:
            canonical_paths[key] = path
            result.append(path)
            continue
        try:
            same_directory = path.samefile(previous)
        except OSError:
            same_directory = path == previous
        if not same_directory:
            raise OSError(
                "scan roots contain case- or Unicode-equivalent paths that name "
                f"different directories: {previous} and {path}"
            )
    return result


def validated_mutable_resource(value: str | Path, *, label: str) -> Path:
    """Reject aliases that make replace/in-place mutation ownership ambiguous."""

    raw = Path(value)
    if raw.is_symlink():
        raise OSError(f"{label} must not be a symlink: {raw}")
    resolved = raw.resolve()
    if not resolved.exists():
        return resolved
    if not resolved.is_file():
        raise OSError(f"{label} must be a regular file: {resolved}")
    if resolved.stat().st_nlink > 1:
        raise OSError(f"{label} must not have multiple hard links: {resolved}")
    return resolved


def capture_directory_identities(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    identities: list[dict[str, Any]] = []
    for path in deduplicate_directory_paths(paths):
        if not path.exists() or not path.is_dir():
            raise OSError(f"scan root is missing or not a directory: {path}")
        file_stat = path.stat()
        identities.append({
            "path": path,
            "device": int(file_stat.st_dev),
            "inode": int(file_stat.st_ino),
        })
    return identities


def validate_directory_identities(identities: Iterable[dict[str, Any]]) -> None:
    for identity in identities:
        path = Path(identity["path"])
        if not path.exists() or not path.is_dir():
            raise OSError(f"scan root changed or disappeared while building the index: {path}")
        file_stat = path.stat()
        if (
            int(file_stat.st_dev) != int(identity["device"])
            or int(file_stat.st_ino) != int(identity["inode"])
        ):
            raise OSError(f"scan root identity changed while building the index: {path}")
