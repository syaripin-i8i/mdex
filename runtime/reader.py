from __future__ import annotations

from pathlib import Path


def _candidate_paths(root: Path, node_id: str) -> list[Path]:
    target = Path(node_id)
    candidates: list[Path] = []

    if target.is_absolute():
        candidates.append(target)
    else:
        candidates.append((root / target).resolve())
        candidates.append((Path.cwd() / target).resolve())

    return candidates


def resolve_node_path(root: str, node_id: str) -> Path | None:
    root_path = Path(root).resolve()
    for candidate in _candidate_paths(root_path, node_id):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def read_node_text(root: str, node_id: str) -> str:
    resolved = resolve_node_path(root, node_id)
    if resolved is None:
        raise FileNotFoundError(f"Node file not found: {node_id}")
    return resolved.read_text(encoding="utf-8")
