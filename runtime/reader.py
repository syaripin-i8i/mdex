from __future__ import annotations

from pathlib import Path


class NodePathError(ValueError):
    def __init__(self, *, error: str, detail: str, node_id: str) -> None:
        super().__init__(detail)
        self.error = error
        self.detail = detail
        self.node_id = node_id


def _normalize_node_id(node_id: str) -> str:
    return str(node_id or "").strip().replace("\\", "/")


def validate_node_id(node_id: str) -> Path:
    normalized = _normalize_node_id(node_id)
    if not normalized:
        raise NodePathError(
            error="invalid node id",
            detail="node id is required",
            node_id=normalized,
        )

    target = Path(normalized)
    if target.is_absolute():
        raise NodePathError(
            error="invalid node id",
            detail="absolute paths are not allowed",
            node_id=normalized,
        )

    if any(part == ".." for part in target.parts):
        raise NodePathError(
            error="invalid node id",
            detail="path traversal ('..') is not allowed",
            node_id=normalized,
        )
    return target


def _resolve_in_scan_root(root: Path, target: Path, *, node_id: str) -> Path:
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NodePathError(
            error="path containment violation",
            detail="resolved path escapes scan_root",
            node_id=node_id,
        ) from exc
    return candidate


def resolve_node_path(root: str, node_id: str) -> Path | None:
    root_path = Path(root).resolve()
    target = validate_node_id(node_id)
    candidate = _resolve_in_scan_root(root_path, target, node_id=_normalize_node_id(node_id))
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def read_node_text(root: str, node_id: str) -> str:
    resolved = resolve_node_path(root, node_id)
    if resolved is None:
        raise FileNotFoundError(f"Node file not found: {node_id}")
    return resolved.read_text(encoding="utf-8")
