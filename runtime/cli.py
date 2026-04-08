from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

try:
    from .builder import build_index
except ImportError:
    from builder import build_index  # type: ignore


def _load_json(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return data


def _save_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _cmd_scan(args: argparse.Namespace) -> int:
    config = _load_json(args.config)
    index = build_index(args.root, config)
    _save_json(args.output, index)

    node_count = len(index.get("nodes", []))
    edge_count = len(index.get("edges", []))
    print(f"[mdex] {node_count} nodes, {edge_count} edges -> {args.output}", file=sys.stderr)
    return 0


def _build_adjacency(edges: list[dict[str, Any]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        if not src or not dst:
            continue
        adjacency.setdefault(src, set()).add(dst)
        adjacency.setdefault(dst, set()).add(src)
    return adjacency


def _cmd_query(args: argparse.Namespace) -> int:
    index = _load_json(args.index)
    nodes = index.get("nodes", [])
    edges = index.get("edges", [])

    if not isinstance(nodes, list) or not isinstance(edges, list):
        print("[mdex] invalid index format", file=sys.stderr)
        return 2

    node_map: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and "id" in node:
            node_map[str(node["id"])] = node

    start_id = args.node
    if start_id not in node_map:
        print(f"[mdex] node not found: {start_id}", file=sys.stderr)
        return 2

    depth = max(0, int(args.depth))
    adjacency = _build_adjacency([edge for edge in edges if isinstance(edge, dict)])

    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    visited: set[str] = {start_id}
    neighbors: list[dict[str, Any]] = []

    while queue:
        node_id, current_depth = queue.popleft()
        if current_depth >= depth:
            continue

        for linked_id in sorted(adjacency.get(node_id, set())):
            if linked_id in visited:
                continue
            visited.add(linked_id)
            queue.append((linked_id, current_depth + 1))
            if linked_id in node_map:
                neighbors.append(node_map[linked_id])
            else:
                neighbors.append({"id": linked_id, "missing": True})

    output = {
        "node": node_map[start_id],
        "depth": depth,
        "neighbors": neighbors,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    index = _load_json(args.index)
    nodes = index.get("nodes", [])
    if not isinstance(nodes, list):
        print("[mdex] invalid index format", file=sys.stderr)
        return 2

    type_filter = args.type.strip().lower() if args.type else None
    project_filter = args.project.strip().lower() if args.project else None
    status_filter = args.status.strip().lower() if args.status else None

    for node in sorted((n for n in nodes if isinstance(n, dict)), key=lambda item: str(item.get("id", ""))):
        node_type = str(node.get("type", "")).strip().lower()
        project = str(node.get("project", "")).strip().lower()
        status = str(node.get("status", "")).strip().lower()

        if type_filter and node_type != type_filter:
            continue
        if project_filter and project != project_filter:
            continue
        if status_filter and status != status_filter:
            continue

        node_id = str(node.get("id", ""))
        title = str(node.get("title", ""))
        print(f"{node_id}\t{title}\t{node_type or 'unknown'}\t{status or 'unknown'}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="mdex CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan markdown files and build an index")
    scan_parser.add_argument("--root", required=True, help="Directory to scan")
    scan_parser.add_argument("--output", default="mdex_index.json", help="Output JSON file path")
    scan_parser.add_argument(
        "--config",
        default="control/scan_config.json",
        help="Path to scan config JSON",
    )
    scan_parser.set_defaults(func=_cmd_scan)

    query_parser = subparsers.add_parser("query", help="Query one node and its neighbors")
    query_parser.add_argument("--index", default="mdex_index.json", help="Index JSON file")
    query_parser.add_argument("--node", required=True, help="Node id")
    query_parser.add_argument("--depth", type=int, default=1, help="Hop depth")
    query_parser.set_defaults(func=_cmd_query)

    list_parser = subparsers.add_parser("list", help="List nodes with optional filters")
    list_parser.add_argument("--index", default="mdex_index.json", help="Index JSON file")
    list_parser.add_argument("--type", help="Filter by type")
    list_parser.add_argument("--project", help="Filter by project")
    list_parser.add_argument("--status", help="Filter by status")
    list_parser.set_defaults(func=_cmd_list)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
