from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

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
            CREATE TABLE IF NOT EXISTS node_overrides (
                id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                summary_source TEXT NOT NULL,
                summary_updated TEXT NOT NULL
            )
            """
        )

        cur.execute("DROP TABLE IF EXISTS nodes")
        cur.execute("DROP TABLE IF EXISTS edges")
        cur.execute("DROP TABLE IF EXISTS index_metadata")

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

        for node in nodes:
            if not isinstance(node, dict):
                continue
            cur.execute(
                """
                INSERT OR REPLACE INTO nodes (
                    id, title, type, project, status, summary, summary_source, summary_updated,
                    tags_json, updated, links_to_json, depends_on_json, relates_to_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        metadata_rows = {
            "generated": str(index.get("generated", "")),
            "scan_root": str(index.get("scan_root", "")),
        }
        for key, value in metadata_rows.items():
            cur.execute(
                """
                INSERT OR REPLACE INTO index_metadata (key, value)
                VALUES (?, ?)
                """,
                (key, value),
            )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_resolved ON edges(resolved)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status)")

        db.commit()
    finally:
        db.close()
