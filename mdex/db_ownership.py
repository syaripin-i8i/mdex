from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping


_REQUIRED_COLUMNS = {
    "nodes": {
        "id",
        "title",
        "type",
        "project",
        "status",
        "summary",
        "summary_source",
        "summary_updated",
        "estimated_tokens",
        "tags_json",
        "updated",
        "links_to_json",
        "depends_on_json",
        "relates_to_json",
    },
    "edges": {"src", "dst", "type", "resolved"},
    "index_metadata": {"key", "value"},
}


def _table_info(conn: sqlite3.Connection, table: str) -> dict[str, dict[str, int]]:
    return {
        str(row[1]): {"not_null": int(row[3]), "primary_key": int(row[5])}
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def has_mdex_schema(conn: sqlite3.Connection) -> bool:
    """Recognize the full mdex schema rather than similarly named graph tables."""

    existing = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if not set(_REQUIRED_COLUMNS).issubset(existing):
        return False

    columns = {table: _table_info(conn, table) for table in _REQUIRED_COLUMNS}
    if any(not required.issubset(columns[table]) for table, required in _REQUIRED_COLUMNS.items()):
        return False
    if columns["nodes"]["id"]["primary_key"] != 1:
        return False
    if columns["index_metadata"]["key"]["primary_key"] != 1:
        return False
    if columns["index_metadata"]["value"]["not_null"] != 1:
        return False
    if [columns["edges"][name]["primary_key"] for name in ("src", "dst", "type")] != [1, 2, 3]:
        return False
    return all(columns["edges"][name]["not_null"] == 1 for name in _REQUIRED_COLUMNS["edges"])


def has_legacy_mdex_metadata(metadata: Mapping[str, str]) -> bool:
    if not metadata.get("generated") or not metadata.get("scan_root"):
        return False
    try:
        scan_roots = json.loads(metadata.get("scan_roots", ""))
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(scan_roots, list) and bool(scan_roots) and all(
        isinstance(item, str) and bool(item.strip()) for item in scan_roots
    )
