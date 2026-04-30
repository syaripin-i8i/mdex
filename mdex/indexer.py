from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


def write_json(index: dict[str, Any], output_path: str) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_nodes(index: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = index.get("nodes", [])
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _normalize_edges(index: dict[str, Any]) -> list[dict[str, Any]]:
    edges = index.get("edges", [])
    if not isinstance(edges, list):
        return []
    return [edge for edge in edges if isinstance(edge, dict)]


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _fetch_node_overrides(cur: sqlite3.Cursor) -> list[tuple[str, str, str, str]]:
    if not _table_exists(cur, "node_overrides"):
        return []
    rows = cur.execute(
        "SELECT id, summary, summary_source, summary_updated FROM node_overrides"
    ).fetchall()
    return [
        (
            str(row[0] or ""),
            str(row[1] or ""),
            str(row[2] or ""),
            str(row[3] or ""),
        )
        for row in rows
        if str(row[0] or "").strip()
    ]


def _create_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            title TEXT,
            type TEXT,
            project TEXT,
            status TEXT,
            summary TEXT,
            summary_source TEXT,
            summary_updated TEXT,
            estimated_tokens INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT,
            updated TEXT,
            links_to_json TEXT,
            depends_on_json TEXT,
            relates_to_json TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE edges (
            src TEXT NOT NULL,
            dst TEXT NOT NULL,
            type TEXT NOT NULL,
            resolved INTEGER NOT NULL,
            PRIMARY KEY (src, dst, type)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE node_overrides (
            id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            summary_source TEXT NOT NULL,
            summary_updated TEXT NOT NULL
        )
        """
    )


def _restore_node_overrides(
    cur: sqlite3.Cursor,
    rows: list[tuple[str, str, str, str]],
    indexed_node_ids: set[str],
) -> None:
    for row in rows:
        node_id = str(row[0] or "").strip()
        if node_id not in indexed_node_ids:
            continue
        cur.execute(
            """
            INSERT OR REPLACE INTO node_overrides (id, summary, summary_source, summary_updated)
            VALUES (?, ?, ?, ?)
            """,
            row,
        )


def _insert_nodes(cur: sqlite3.Cursor, nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        cur.execute(
            """
            INSERT OR REPLACE INTO nodes (
                id, title, type, project, status, summary, summary_source, summary_updated,
                estimated_tokens, tags_json, updated, links_to_json, depends_on_json, relates_to_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(node.get("id", "")),
                str(node.get("title", "")),
                str(node.get("type", "")),
                str(node.get("project", "")),
                str(node.get("status", "")),
                str(node.get("summary", "")),
                "seed",
                str(node.get("updated", "")),
                int(node.get("estimated_tokens", 0) or 0),
                json.dumps(node.get("tags", []), ensure_ascii=False),
                str(node.get("updated", "")),
                json.dumps(node.get("links_to", []), ensure_ascii=False),
                json.dumps(node.get("depends_on", []), ensure_ascii=False),
                json.dumps(node.get("relates_to", []), ensure_ascii=False),
            ),
        )


def _insert_edges(cur: sqlite3.Cursor, edges: list[dict[str, Any]]) -> None:
    for edge in edges:
        cur.execute(
            """
            INSERT OR REPLACE INTO edges (src, dst, type, resolved)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(edge.get("from", "")),
                str(edge.get("to", "")),
                str(edge.get("type", "")),
                1 if bool(edge.get("resolved", True)) else 0,
            ),
        )


def _insert_metadata(cur: sqlite3.Cursor, index: dict[str, Any]) -> None:
    metadata_rows = {
        "generated": str(index.get("generated", "")),
        "scan_root": str(index.get("scan_root", "")),
        "scan_roots": json.dumps(index.get("scan_roots", []), ensure_ascii=False),
    }
    for key, value in metadata_rows.items():
        cur.execute(
            """
            INSERT OR REPLACE INTO index_metadata (key, value)
            VALUES (?, ?)
            """,
            (key, value),
        )


def _create_indexes(cur: sqlite3.Cursor) -> None:
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_resolved ON edges(resolved)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status)")


def _load_existing_overrides(db_path: Path) -> list[tuple[str, str, str, str]]:
    if not db_path.exists():
        return []
    db = sqlite3.connect(str(db_path))
    try:
        cur = db.cursor()
        return _fetch_node_overrides(cur)
    finally:
        db.close()


def write_sqlite(index: dict[str, Any], db_path: str) -> None:
    output = Path(db_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    nodes = _normalize_nodes(index)
    edges = _normalize_edges(index)
    indexed_node_ids = {str(node.get("id", "")).strip() for node in nodes if str(node.get("id", "")).strip()}
    overrides = _load_existing_overrides(output)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.stem}.",
        suffix=".tmp",
        dir=str(output.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)

    db = sqlite3.connect(str(temp_path))
    try:
        cur = db.cursor()
        cur.execute("BEGIN")
        _create_schema(cur)
        _restore_node_overrides(cur, overrides, indexed_node_ids)
        _insert_nodes(cur, nodes)
        _insert_edges(cur, edges)
        _insert_metadata(cur, index)
        _create_indexes(cur)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    try:
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
