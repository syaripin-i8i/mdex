from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

try:
    from .builder import build_index as _build_index_impl
except ImportError:
    from builder import build_index as _build_index_impl  # type: ignore


def build_index(root: str, config: dict[str, Any]) -> dict[str, Any]:
    return _build_index_impl(root, config)


def write_json(index: dict[str, Any], output_path: str) -> None:
    output = Path(output_path)
    output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_sqlite(index: dict[str, Any], db_path: str) -> None:
    nodes = index.get("nodes", [])
    edges = index.get("edges", [])
    if not isinstance(nodes, list):
        nodes = []
    if not isinstance(edges, list):
        edges = []

    db = sqlite3.connect(db_path)
    try:
        cur = db.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                title TEXT,
                type TEXT,
                project TEXT,
                status TEXT,
                summary TEXT,
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
            CREATE TABLE IF NOT EXISTS edges (
                src TEXT NOT NULL,
                dst TEXT NOT NULL,
                type TEXT NOT NULL,
                PRIMARY KEY (src, dst, type)
            )
            """
        )
        cur.execute("DELETE FROM nodes")
        cur.execute("DELETE FROM edges")

        for node in nodes:
            if not isinstance(node, dict):
                continue
            cur.execute(
                """
                INSERT OR REPLACE INTO nodes (
                    id, title, type, project, status, summary, tags_json, updated,
                    links_to_json, depends_on_json, relates_to_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(node.get("id", "")),
                    str(node.get("title", "")),
                    str(node.get("type", "")),
                    str(node.get("project", "")),
                    str(node.get("status", "")),
                    str(node.get("summary", "")),
                    json.dumps(node.get("tags", []), ensure_ascii=False),
                    str(node.get("updated", "")),
                    json.dumps(node.get("links_to", []), ensure_ascii=False),
                    json.dumps(node.get("depends_on", []), ensure_ascii=False),
                    json.dumps(node.get("relates_to", []), ensure_ascii=False),
                ),
            )

        for edge in edges:
            if not isinstance(edge, dict):
                continue
            cur.execute(
                """
                INSERT OR REPLACE INTO edges (src, dst, type)
                VALUES (?, ?, ?)
                """,
                (
                    str(edge.get("from", "")),
                    str(edge.get("to", "")),
                    str(edge.get("type", "")),
                ),
            )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status)")

        db.commit()
    finally:
        db.close()
