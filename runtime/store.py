from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any


def _as_json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(loaded, list):
        return [str(item) for item in loaded]
    return []


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _build_where_clauses(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in filters.items():
        if value is None:
            continue
        clauses.append(f"{column} = ?")
        params.append(value)
    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


def _normalize_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def list_nodes(
    db_path: str,
    *,
    node_type: str | None = None,
    project: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    filters = {
        "LOWER(type)": _normalize_filter(node_type),
        "LOWER(project)": _normalize_filter(project),
        "LOWER(status)": _normalize_filter(status),
    }
    where_sql, params = _build_where_clauses(filters)

    query = (
        "SELECT id, title, type, project, status, summary, tags_json, updated, "
        "links_to_json, depends_on_json, relates_to_json "
        f"FROM nodes{where_sql} ORDER BY id"
    )

    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    nodes: list[dict[str, Any]] = []
    for row in rows:
        nodes.append(
            {
                "id": str(row["id"] or ""),
                "title": str(row["title"] or ""),
                "type": str(row["type"] or ""),
                "project": str(row["project"] or ""),
                "status": str(row["status"] or ""),
                "summary": str(row["summary"] or ""),
                "tags": _as_json_list(row["tags_json"]),
                "updated": str(row["updated"] or ""),
                "links_to": _as_json_list(row["links_to_json"]),
                "depends_on": _as_json_list(row["depends_on_json"]),
                "relates_to": _as_json_list(row["relates_to_json"]),
            }
        )
    return nodes


def get_node(db_path: str, node_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, title, type, project, status, summary, tags_json, updated,
                   links_to_json, depends_on_json, relates_to_json
            FROM nodes
            WHERE id = ?
            """,
            (node_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": str(row["id"] or ""),
        "title": str(row["title"] or ""),
        "type": str(row["type"] or ""),
        "project": str(row["project"] or ""),
        "status": str(row["status"] or ""),
        "summary": str(row["summary"] or ""),
        "tags": _as_json_list(row["tags_json"]),
        "updated": str(row["updated"] or ""),
        "links_to": _as_json_list(row["links_to_json"]),
        "depends_on": _as_json_list(row["depends_on_json"]),
        "relates_to": _as_json_list(row["relates_to_json"]),
    }


def list_edges(
    db_path: str,
    *,
    src: str | None = None,
    dst: str | None = None,
    edge_type: str | None = None,
    resolved: bool | None = None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []

    if src is not None:
        filters.append("src = ?")
        params.append(src)
    if dst is not None:
        filters.append("dst = ?")
        params.append(dst)
    if edge_type is not None:
        filters.append("type = ?")
        params.append(edge_type)
    if resolved is not None:
        filters.append("resolved = ?")
        params.append(1 if resolved else 0)

    where_sql = ""
    if filters:
        where_sql = " WHERE " + " AND ".join(filters)

    query = f"SELECT src, dst, type, resolved FROM edges{where_sql} ORDER BY src, type, dst"

    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "from": str(row["src"] or ""),
            "to": str(row["dst"] or ""),
            "type": str(row["type"] or ""),
            "resolved": bool(int(row["resolved"] or 0)),
        }
        for row in rows
    ]


def list_index_metadata(db_path: str) -> dict[str, str]:
    with _connect(db_path) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_metadata'"
        ).fetchone()
        if table_exists is None:
            return {}
        rows = conn.execute("SELECT key, value FROM index_metadata").fetchall()

    metadata: dict[str, str] = {}
    for row in rows:
        key = str(row["key"] or "").strip()
        if not key:
            continue
        metadata[key] = str(row["value"] or "")
    return metadata


def get_index_metadata(db_path: str, key: str, default: str | None = None) -> str | None:
    metadata = list_index_metadata(db_path)
    if key in metadata:
        return metadata[key]
    return default


def get_scan_root(db_path: str, default: str = ".") -> str:
    value = get_index_metadata(db_path, "scan_root", default)
    if value is None:
        return default
    cleaned = value.strip()
    return cleaned or default


def count_edges(edge_rows: Iterable[dict[str, Any]]) -> tuple[int, int, int]:
    total = 0
    resolved = 0
    for edge in edge_rows:
        total += 1
        if bool(edge.get("resolved", False)):
            resolved += 1
    unresolved = total - resolved
    return total, resolved, unresolved
