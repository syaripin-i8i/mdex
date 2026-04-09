from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from runtime.builder import build_index
from runtime.indexer import write_json, write_sqlite
from runtime.reader import read_node_text
from runtime.resolver import prerequisite_order, related_nodes
from runtime.store import (
    get_node,
    get_scan_root,
    list_edges,
    list_nodes,
    list_orphan_nodes,
    search_nodes,
)


def _emit_error(error: str, **details: Any) -> None:
    payload: dict[str, Any] = {"error": error}
    payload.update(details)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def _load_json(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return data


def _node_map_from_rows(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    node_map: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            continue
        node_map[node_id] = node
    return node_map


def _count_edge_resolution(edges: list[dict[str, Any]]) -> tuple[int, int, int, float]:
    total = len(edges)
    resolved = sum(1 for edge in edges if bool(edge.get("resolved", False)))
    unresolved = total - resolved
    rate = (resolved / total * 100.0) if total else 0.0
    return total, resolved, unresolved, rate


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        config = _load_json(args.config)
        index = build_index(args.root, config)
        write_json(index, args.output)
        write_sqlite(index, args.db)
    except Exception as exc:
        _emit_error("scan failed", detail=str(exc))
        return 2

    node_count = len(index.get("nodes", []))
    edges = [edge for edge in index.get("edges", []) if isinstance(edge, dict)]
    total_edges, resolved_edges, unresolved_edges, rate = _count_edge_resolution(edges)

    print(
        f"[mdex] {node_count} nodes, {total_edges} edges -> {args.output}, {args.db}",
        file=sys.stderr,
    )
    print(
        (
            f"[mdex] edges: total={total_edges}, resolved={resolved_edges}, "
            f"unresolved={unresolved_edges}, resolution_rate={rate:.2f}%"
        ),
        file=sys.stderr,
    )
    return 0


def _filter_json_nodes(
    nodes: list[Any],
    *,
    node_type: str | None,
    project: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    type_filter = node_type.strip().lower() if node_type else None
    project_filter = project.strip().lower() if project else None
    status_filter = status.strip().lower() if status else None

    filtered: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        current_type = str(node.get("type", "")).strip().lower()
        current_project = str(node.get("project", "")).strip().lower()
        current_status = str(node.get("status", "")).strip().lower()

        if type_filter and current_type != type_filter:
            continue
        if project_filter and current_project != project_filter:
            continue
        if status_filter and current_status != status_filter:
            continue
        filtered.append(node)
    return filtered


def _load_nodes_for_list(args: argparse.Namespace) -> list[dict[str, Any]]:
    db_path = Path(args.db)
    if db_path.exists():
        return list_nodes(
            str(db_path),
            node_type=args.type,
            project=args.project,
            status=args.status,
        )

    if args.index:
        index = _load_json(args.index)
        nodes = index.get("nodes", [])
        if not isinstance(nodes, list):
            raise ValueError("invalid index format")
        return _filter_json_nodes(
            nodes,
            node_type=args.type,
            project=args.project,
            status=args.status,
        )

    raise FileNotFoundError(f"SQLite DB not found: {args.db}")


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        nodes = _load_nodes_for_list(args)
    except FileNotFoundError as exc:
        _emit_error("db not found", db_path=str(args.db), detail=str(exc))
        return 2
    except Exception as exc:
        _emit_error("failed to load nodes", detail=str(exc))
        return 2

    sorted_nodes = sorted(nodes, key=lambda item: str(item.get("id", "")))
    if args.format == "table":
        for node in sorted_nodes:
            node_id = str(node.get("id", ""))
            title = str(node.get("title", ""))
            node_type = str(node.get("type", "")).strip().lower() or "unknown"
            status = str(node.get("status", "")).strip().lower() or "unknown"
            print(f"{node_id}\t{title}\t{node_type}\t{status}")
        return 0

    print(json.dumps(sorted_nodes, ensure_ascii=False, indent=2))
    return 0


def _print_node_table(nodes: list[dict[str, Any]]) -> None:
    for node in sorted(nodes, key=lambda item: str(item.get("id", ""))):
        node_id = str(node.get("id", ""))
        title = str(node.get("title", ""))
        node_type = str(node.get("type", "")).strip().lower() or "unknown"
        status = str(node.get("status", "")).strip().lower() or "unknown"
        print(f"{node_id}\t{title}\t{node_type}\t{status}")


def _print_node_rows(nodes: list[dict[str, Any]], output_format: str) -> None:
    sorted_nodes = sorted(nodes, key=lambda item: str(item.get("id", "")))
    if output_format == "table":
        _print_node_table(sorted_nodes)
    else:
        print(json.dumps(sorted_nodes, ensure_ascii=False, indent=2))
    return None


def _cmd_open(args: argparse.Namespace) -> int:
    root = args.root

    db_path = Path(args.db)
    if db_path.exists():
        root = get_scan_root(str(db_path), default=root)
    elif args.index:
        try:
            index = _load_json(args.index)
            scan_root = index.get("scan_root")
            if isinstance(scan_root, str) and scan_root.strip():
                root = scan_root.strip()
        except Exception:
            pass

    try:
        text = read_node_text(root, args.node)
    except FileNotFoundError:
        _emit_error("node file not found", node_id=args.node)
        return 2

    if text.endswith("\n"):
        print(text, end="")
    else:
        print(text)
    return 0


def _load_graph_for_query(args: argparse.Namespace) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    db_path = Path(args.db)
    if db_path.exists():
        nodes = list_nodes(str(db_path))
        edges = list_edges(str(db_path))
        return _node_map_from_rows(nodes), edges

    if args.index:
        index = _load_json(args.index)
        nodes = index.get("nodes", [])
        edges = index.get("edges", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValueError("invalid index format")
        node_map = _node_map_from_rows([node for node in nodes if isinstance(node, dict)])
        edge_rows = [edge for edge in edges if isinstance(edge, dict)]
        for edge in edge_rows:
            if "resolved" not in edge:
                edge["resolved"] = True
        return node_map, edge_rows

    raise FileNotFoundError(f"SQLite DB not found: {args.db}")


def _empty_grouped_edges() -> dict[str, list[dict[str, Any]]]:
    return {"links_to": [], "depends_on": [], "relates_to": []}


def _node_brief(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(node.get("id", "")),
        "title": str(node.get("title", "")),
        "type": str(node.get("type", "")),
        "project": str(node.get("project", "")),
        "status": str(node.get("status", "")),
        "resolved": True,
    }


def _peer_entry(peer_id: str, is_resolved: bool, node_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if is_resolved and peer_id in node_map:
        return _node_brief(node_map[peer_id])
    return {"id": peer_id, "resolved": is_resolved, "missing": True}


def _cmd_query(args: argparse.Namespace) -> int:
    try:
        node_map, edges = _load_graph_for_query(args)
    except FileNotFoundError as exc:
        _emit_error("db not found", db_path=str(args.db), detail=str(exc))
        return 2
    except Exception as exc:
        _emit_error("failed to load graph", detail=str(exc))
        return 2

    start_id = args.node
    if start_id not in node_map:
        loaded_node = None
        db_path = Path(args.db)
        if db_path.exists():
            loaded_node = get_node(str(db_path), start_id)
        if loaded_node is not None:
            node_map[start_id] = loaded_node
        else:
            _emit_error("node not found", node_id=start_id)
            return 2

    outgoing = _empty_grouped_edges()
    incoming = _empty_grouped_edges()

    for edge in edges:
        edge_type = str(edge.get("type", "")).strip() or "links_to"
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        is_resolved = bool(edge.get("resolved", False))
        if not src or not dst:
            continue

        if src == start_id:
            outgoing.setdefault(edge_type, []).append(_peer_entry(dst, is_resolved, node_map))
        if dst == start_id:
            incoming.setdefault(edge_type, []).append(_peer_entry(src, is_resolved, node_map))

    for groups in (outgoing, incoming):
        for edge_type, items in list(groups.items()):
            groups[edge_type] = sorted(
                items,
                key=lambda item: (
                    0 if bool(item.get("resolved", False)) else 1,
                    str(item.get("id", "")),
                ),
            )

    output = {
        "node": node_map[start_id],
        "outgoing": outgoing,
        "incoming": incoming,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _cmd_related(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    node = get_node(str(db_path), args.node)
    if node is None:
        _emit_error("node not found", node_id=args.node)
        return 2

    results = related_nodes(args.node, str(db_path), limit=int(args.limit))
    output = {
        "node": node,
        "related": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _cmd_find(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    matched = search_nodes(str(db_path), args.query, limit=int(args.limit))
    _print_node_rows(matched, args.format)
    return 0


def _cmd_orphans(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    orphans = list_orphan_nodes(str(db_path))
    _print_node_rows(orphans, args.format)
    return 0


def _cmd_first(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    node = get_node(str(db_path), args.node)
    if node is None:
        _emit_error("node not found", node_id=args.node)
        return 2

    prerequisites = prerequisite_order(args.node, str(db_path), limit=int(args.limit))
    output = {
        "node": node,
        "prerequisites": prerequisites,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="mdex CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan markdown files and build an index")
    scan_parser.add_argument("--root", required=True, help="Directory to scan")
    scan_parser.add_argument("--output", default="mdex_index.json", help="Output JSON file path")
    scan_parser.add_argument("--db", default="mdex_index.db", help="Output SQLite file path")
    scan_parser.add_argument(
        "--config",
        default="control/scan_config.json",
        help="Path to scan config JSON",
    )
    scan_parser.set_defaults(func=_cmd_scan)

    list_parser = subparsers.add_parser("list", help="List nodes with optional filters")
    list_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    list_parser.add_argument("--index", help="Fallback JSON index file (legacy)")
    list_parser.add_argument("--type", help="Filter by type")
    list_parser.add_argument("--project", help="Filter by project")
    list_parser.add_argument("--status", help="Filter by status")
    list_parser.add_argument("--format", choices=["table", "json"], default="json")
    list_parser.set_defaults(func=_cmd_list)

    open_parser = subparsers.add_parser("open", help="Print markdown source for a node id")
    open_parser.add_argument("node", help="Node id, for example docs/proposal.md")
    open_parser.add_argument("--root", default=".", help="Root directory used to resolve node path")
    open_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    open_parser.add_argument(
        "--index",
        help="Optional fallback index JSON path. Used when SQLite is unavailable.",
    )
    open_parser.set_defaults(func=_cmd_open)

    query_parser = subparsers.add_parser("query", help="Query one node and its neighbors")
    query_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    query_parser.add_argument("--index", help="Fallback JSON index file (legacy)")
    query_parser.add_argument("--node", required=True, help="Node id")
    query_parser.set_defaults(func=_cmd_query)

    related_parser = subparsers.add_parser("related", help="Recommend related nodes to read next")
    related_parser.add_argument("node", help="Node id")
    related_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    related_parser.add_argument("--limit", type=int, default=10, help="Maximum number of results")
    related_parser.set_defaults(func=_cmd_related)

    find_parser = subparsers.add_parser("find", help="Find nodes by keyword")
    find_parser.add_argument("query", help="Search query")
    find_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    find_parser.add_argument("--limit", type=int, default=20, help="Maximum number of results")
    find_parser.add_argument("--format", choices=["table", "json"], default="json")
    find_parser.set_defaults(func=_cmd_find)

    orphan_parser = subparsers.add_parser("orphans", help="List nodes with no resolved edges")
    orphan_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    orphan_parser.add_argument("--format", choices=["table", "json"], default="json")
    orphan_parser.set_defaults(func=_cmd_orphans)

    first_parser = subparsers.add_parser("first", help="Return prerequisite nodes to read first")
    first_parser.add_argument("node", help="Node id")
    first_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    first_parser.add_argument("--limit", type=int, default=10, help="Maximum number of results")
    first_parser.set_defaults(func=_cmd_first)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
