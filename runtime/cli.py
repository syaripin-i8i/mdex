from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from runtime.builder import build_index
from runtime.context import select_context
from runtime.enrich import enrich_node, resolve_node_id
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


def _print_node_table(nodes: list[dict[str, Any]]) -> None:
    for node in sorted(nodes, key=lambda item: str(item.get("id", ""))):
        node_id = str(node.get("id", ""))
        title = str(node.get("title", ""))
        node_type = str(node.get("type", "")).strip().lower() or "unknown"
        status = str(node.get("status", "")).strip().lower() or "unknown"
        print(f"{node_id}\t{title}\t{node_type}\t{status}")


def _print_nodes(nodes: list[dict[str, Any]], output_format: str) -> None:
    sorted_nodes = sorted(nodes, key=lambda item: str(item.get("id", "")))
    if output_format == "table":
        _print_node_table(sorted_nodes)
    else:
        print(json.dumps(sorted_nodes, ensure_ascii=False, indent=2))


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

    payload = {
        "nodes": node_count,
        "edges": {
            "total": total_edges,
            "resolved": resolved_edges,
            "unresolved": unresolved_edges,
            "resolution_rate": round(rate, 2),
        },
        "output": {
            "json": str(args.output),
            "db": str(args.db),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    try:
        nodes = list_nodes(
            str(db_path),
            node_type=args.type,
            project=args.project,
            status=args.status,
        )
    except Exception as exc:
        _emit_error("failed to load nodes", detail=str(exc))
        return 2

    _print_nodes(nodes, args.format)
    return 0


def _cmd_open(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    root = get_scan_root(str(db_path), default=args.root)

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
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    try:
        node_map = _node_map_from_rows(list_nodes(str(db_path)))
        edges = list_edges(str(db_path))
    except Exception as exc:
        _emit_error("failed to load graph", detail=str(exc))
        return 2

    start_id = args.node
    if start_id not in node_map:
        loaded_node = get_node(str(db_path), start_id)
        if loaded_node is not None:
            node_map[start_id] = loaded_node
        else:
            _emit_error("node not found", node_id=start_id)
            return 2

    outgoing = _empty_grouped_edges()
    incoming = _empty_grouped_edges()
    stats = {
        "outgoing_resolved": 0,
        "outgoing_unresolved": 0,
        "incoming_resolved": 0,
        "incoming_unresolved": 0,
    }

    for edge in edges:
        edge_type = str(edge.get("type", "")).strip() or "links_to"
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        is_resolved = bool(edge.get("resolved", False))
        if not src or not dst:
            continue

        if src == start_id:
            outgoing.setdefault(edge_type, []).append(_peer_entry(dst, is_resolved, node_map))
            if is_resolved:
                stats["outgoing_resolved"] += 1
            else:
                stats["outgoing_unresolved"] += 1
        if dst == start_id:
            incoming.setdefault(edge_type, []).append(_peer_entry(src, is_resolved, node_map))
            if is_resolved:
                stats["incoming_resolved"] += 1
            else:
                stats["incoming_unresolved"] += 1

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
        "stats": stats,
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


def _cmd_find(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    matched = search_nodes(str(db_path), args.query, limit=int(args.limit))
    _print_nodes(matched, args.format)
    return 0


def _cmd_orphans(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    orphans = list_orphan_nodes(str(db_path))
    _print_nodes(orphans, args.format)
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    try:
        result = select_context(
            args.query,
            str(db_path),
            budget=int(args.budget),
            limit=int(args.limit),
            include_content=bool(args.include_content),
        )
    except Exception as exc:
        _emit_error("context selection failed", detail=str(exc))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_enrich(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        _emit_error("db not found", db_path=str(args.db))
        return 2

    if bool(args.node) == bool(args.path):
        _emit_error("invalid arguments", detail="specify exactly one of node or --path")
        return 2
    if bool(args.summary) == bool(args.summary_file):
        _emit_error("invalid arguments", detail="specify exactly one of --summary or --summary-file")
        return 2

    if args.path:
        if not Path(args.path).is_absolute():
            _emit_error("path must be absolute", path=args.path)
            return 2
        node_id = resolve_node_id(args.path, str(db_path), path_mode=True)
        if node_id is None:
            _emit_error("node not found", path=args.path)
            return 2
    else:
        node_id = resolve_node_id(args.node, str(db_path), path_mode=False)
        if node_id is None:
            _emit_error("node not found", node_id=str(args.node))
            return 2

    if args.summary:
        summary_text = str(args.summary).strip()
    else:
        summary_file = Path(str(args.summary_file))
        if not summary_file.exists():
            _emit_error("summary file not found", path=str(summary_file))
            return 2
        try:
            summary_text = summary_file.read_text(encoding="utf-8").strip()
        except Exception as exc:
            _emit_error("failed to read summary file", path=str(summary_file), detail=str(exc))
            return 2

    result = enrich_node(node_id, str(db_path), summary_text, force=bool(args.force))
    if result.get("status") == "error":
        details = {key: value for key, value in result.items() if key not in {"status", "error"}}
        _emit_error(str(result.get("error", "enrich failed")), **details)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
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
    list_parser.add_argument("--type", help="Filter by type")
    list_parser.add_argument("--project", help="Filter by project")
    list_parser.add_argument("--status", help="Filter by status")
    list_parser.add_argument("--format", choices=["table", "json"], default="json")
    list_parser.set_defaults(func=_cmd_list)

    open_parser = subparsers.add_parser("open", help="Print markdown source for a node id")
    open_parser.add_argument("node", help="Node id, for example docs/proposal.md")
    open_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    open_parser.add_argument("--root", default=".", help="Fallback root when metadata is absent")
    open_parser.set_defaults(func=_cmd_open)

    query_parser = subparsers.add_parser("query", help="Query one node and its neighbors")
    query_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    query_parser.add_argument("--node", required=True, help="Node id")
    query_parser.set_defaults(func=_cmd_query)

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

    related_parser = subparsers.add_parser("related", help="Recommend related nodes to read next")
    related_parser.add_argument("node", help="Node id")
    related_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    related_parser.add_argument("--limit", type=int, default=10, help="Maximum number of results")
    related_parser.set_defaults(func=_cmd_related)

    first_parser = subparsers.add_parser("first", help="Return prerequisite nodes to read first")
    first_parser.add_argument("node", help="Node id")
    first_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    first_parser.add_argument("--limit", type=int, default=10, help="Maximum number of results")
    first_parser.set_defaults(func=_cmd_first)

    context_parser = subparsers.add_parser("context", help="Select context nodes for a query")
    context_parser.add_argument("query", help="Query text")
    context_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    context_parser.add_argument("--budget", type=int, default=4000, help="Token budget (soft)")
    context_parser.add_argument("--limit", type=int, default=10, help="Maximum nodes to return")
    context_parser.add_argument(
        "--include-content",
        action="store_true",
        help="Include full node content in output",
    )
    context_parser.set_defaults(func=_cmd_context)

    enrich_parser = subparsers.add_parser("enrich", help="Update node summary from provided text")
    enrich_parser.add_argument("node", nargs="?", help="Node id to enrich")
    enrich_parser.add_argument("--path", help="Absolute markdown file path to resolve as node id")
    enrich_parser.add_argument("--db", default="mdex_index.db", help="Index SQLite file")
    enrich_parser.add_argument("--summary", help="Summary text to store")
    enrich_parser.add_argument("--summary-file", help="Path to file containing summary text")
    enrich_parser.add_argument("--force", action="store_true", help="Overwrite existing agent summary")
    enrich_parser.set_defaults(func=_cmd_enrich)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
