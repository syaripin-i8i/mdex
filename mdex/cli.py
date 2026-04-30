from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mdex.builder import build_index
from mdex.contract import with_contract_metadata
from mdex.context import resolve_context_scoring_config, select_context
from mdex.dbresolve import (
    DbResolutionError,
    RuntimeContext,
    load_runtime_context,
    resolve_db_path,
    resolve_scan_config_path,
    resolve_scan_roots,
)
from mdex.doctor import build_doctor_report
from mdex.enrich import enrich_node, resolve_node_id
from mdex.finish import FinishError, run_finish
from mdex.gittools import GitError, collect_changed_files
from mdex.impact import build_impact_report
from mdex.indexer import write_json, write_sqlite
from mdex.reader import NodePathError, read_node_text, validate_node_id
from mdex.scaffold import create_decision_file, create_task_file, stamp_updated
from mdex.start import build_start_payload
from mdex.store import (
    get_node,
    get_scan_root,
    list_edges,
    list_nodes,
    list_orphan_nodes,
    list_stale_nodes,
    search_nodes,
)


def _force_utf8_stdio() -> None:
    # Windows pipes can default to locale encodings (for example cp932/cp1252),
    # which breaks non-ASCII JSON output. Force UTF-8 when reconfigure is available.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="strict")
        except Exception:
            # Keep CLI behavior intact even in exotic stream environments.
            continue


def _emit_payload(payload: Any, *, stderr: bool = False, pretty: bool = False) -> None:
    if pretty:
        output = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        output = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if stderr:
        print(output, file=sys.stderr)
    else:
        print(output)


def _emit_error(error: str, **details: Any) -> None:
    payload: dict[str, Any] = {"error": error}
    payload.update(details)
    _emit_payload(with_contract_metadata(payload, "error"), stderr=True)


def _emit_error_payload(payload: dict[str, Any]) -> None:
    _emit_payload(with_contract_metadata(payload, "error"), stderr=True)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _emit_error("invalid arguments", detail=message)
        raise SystemExit(2)


def _load_json(path: str, *, optional: bool = False) -> dict[str, Any]:
    source = Path(path)
    if optional and not source.exists():
        return {}
    data = json.loads(source.read_text(encoding="utf-8"))
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
        _emit_payload(sorted_nodes, pretty=True)


def _resolve_context_scoring(
    db_info: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    runtime_config = db_info.get("config", {})
    if not isinstance(runtime_config, dict):
        runtime_config = {}

    scan_config: dict[str, Any] = {}
    repo_root_raw = str(db_info.get("repo_root", "") or "").strip()
    config_path_raw = str(db_info.get("config_path", "") or "").strip()
    if repo_root_raw:
        repo_root = Path(repo_root_raw)
        runtime_context = RuntimeContext(
            repo_root=repo_root,
            config_path=Path(config_path_raw) if config_path_raw else (repo_root / ".mdex" / "config.json"),
            config=runtime_config,
        )
        try:
            scan_config_path = resolve_scan_config_path(runtime_context)
            scan_config = _load_json(str(scan_config_path), optional=True)
        except Exception:
            scan_config = {}

    return resolve_context_scoring_config(
        runtime_config=runtime_config,
        scan_config=scan_config,
    )


def _resolve_db(args: argparse.Namespace, *, must_exist: bool) -> dict[str, Any] | None:
    explicit = getattr(args, "db", None)
    try:
        resolved = resolve_db_path(explicit, cwd=Path.cwd(), must_exist=must_exist)
    except DbResolutionError as exc:
        _emit_error_payload(exc.payload)
        return None
    except Exception as exc:
        _emit_error("db resolution failed", detail=str(exc))
        return None
    return resolved


def _resolve_scan_json_path(db_info: dict[str, Any], explicit_json: str | None) -> Path | None:
    if explicit_json and explicit_json.strip():
        return Path(explicit_json).resolve()

    repo_root_raw = str(db_info.get("repo_root", "") or "").strip()
    if not repo_root_raw:
        return None

    repo_root = Path(repo_root_raw)
    runtime_config = db_info.get("config", {})
    if not isinstance(runtime_config, dict):
        runtime_config = {}
    config_path_raw = str(db_info.get("config_path", "") or "").strip()
    runtime_context = RuntimeContext(
        repo_root=repo_root,
        config_path=Path(config_path_raw) if config_path_raw else (repo_root / ".mdex" / "config.json"),
        config=runtime_config,
    )

    try:
        scan_config_path = resolve_scan_config_path(runtime_context)
        scan_config = _load_json(str(scan_config_path), optional=True)
    except Exception:
        scan_config = {}

    output_setting = scan_config.get("output_file")
    if isinstance(output_setting, str) and output_setting.strip():
        return (repo_root / output_setting.strip()).resolve()
    return (repo_root / ".mdex" / "mdex_index.json").resolve()


def _cmd_scan(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=False)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    try:
        context = load_runtime_context(Path.cwd())
        config_path = Path(args.config).resolve() if args.config else resolve_scan_config_path(context)
        config = _load_json(str(config_path), optional=not bool(args.config))

        scan_root_warnings: list[str] = []
        if args.root:
            scan_roots: list[Path] = [Path(args.root).resolve()]
        else:
            has_scan_roots = isinstance(config.get("scan_roots"), list) or (
                isinstance(config.get("scan_root"), str) and str(config.get("scan_root")).strip()
            )
            if has_scan_roots:
                scan_roots, scan_root_warnings = resolve_scan_roots(context, config=config)
            else:
                scan_roots, scan_root_warnings = resolve_scan_roots(context)

        if args.output:
            output_path = Path(args.output).resolve()
        else:
            output_setting = config.get("output_file")
            if isinstance(output_setting, str) and output_setting.strip():
                output_path = (context.repo_root / output_setting.strip()).resolve()
            else:
                output_path = (context.repo_root / ".mdex" / "mdex_index.json").resolve()

        index = build_index(scan_roots, config, strict=bool(args.strict))
        index_warnings = [item for item in index.get("warnings", []) if isinstance(item, dict)]
        for warning in scan_root_warnings:
            index_warnings.append({"path": "scan_config", "error": warning})
        index["warnings"] = index_warnings
        write_sqlite(index, db_path)
        write_json(index, str(output_path))
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
            "json": str(output_path),
            "db": db_path,
        },
        "warnings": [item for item in index.get("warnings", []) if isinstance(item, dict)],
    }
    _emit_payload(with_contract_metadata(payload, "scan"), pretty=True)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))
    repo_root_raw = str(db_info.get("repo_root", "") or "").strip()
    repo_root = Path(repo_root_raw) if repo_root_raw else None
    json_index_path = _resolve_scan_json_path(db_info, getattr(args, "json_index", None))

    try:
        payload = build_doctor_report(
            db_path,
            repo_root=repo_root,
            json_index_path=json_index_path,
        )
    except Exception as exc:
        _emit_error("doctor failed", detail=str(exc))
        return 2

    _emit_payload(with_contract_metadata(payload, "doctor"), pretty=True)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    try:
        nodes = list_nodes(
            db_path,
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
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))
    node_id = str(args.node or "").strip().replace("\\", "/")
    try:
        validate_node_id(node_id)
    except NodePathError as exc:
        _emit_error(exc.error, node_id=exc.node_id, detail=exc.detail)
        return 2

    if get_node(db_path, node_id) is None:
        _emit_error("node not indexed", node_id=node_id)
        return 2
    root = get_scan_root(db_path, default=args.root)

    try:
        text = read_node_text(root, node_id)
    except NodePathError as exc:
        _emit_error(exc.error, node_id=exc.node_id, detail=exc.detail)
        return 2
    except FileNotFoundError:
        _emit_error("node file not found", node_id=node_id)
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
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    try:
        node_map = _node_map_from_rows(list_nodes(db_path))
        edges = list_edges(db_path)
    except Exception as exc:
        _emit_error("failed to load graph", detail=str(exc))
        return 2

    start_id = args.node
    if start_id not in node_map:
        loaded_node = get_node(db_path, start_id)
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
    _emit_payload(output, pretty=True)
    return 0


def _cmd_related(args: argparse.Namespace) -> int:
    from mdex.resolver import related_nodes

    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    node = get_node(db_path, args.node)
    if node is None:
        _emit_error("node not found", node_id=args.node)
        return 2

    results = related_nodes(args.node, db_path, limit=int(args.limit))
    output = {
        "node": node,
        "related": results,
    }
    _emit_payload(output, pretty=True)
    return 0


def _cmd_first(args: argparse.Namespace) -> int:
    from mdex.resolver import prerequisite_order

    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    node = get_node(db_path, args.node)
    if node is None:
        _emit_error("node not found", node_id=args.node)
        return 2

    prerequisites = prerequisite_order(args.node, db_path, limit=int(args.limit))
    output = {
        "node": node,
        "prerequisites": prerequisites,
    }
    _emit_payload(output, pretty=True)
    return 0


def _cmd_find(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    matched = search_nodes(db_path, args.query, limit=int(args.limit))
    _print_nodes(matched, args.format)
    return 0


def _cmd_orphans(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    orphans = list_orphan_nodes(db_path)
    _print_nodes(orphans, args.format)
    return 0


def _print_stale_table(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        print(
            "\t".join(
                [
                    str(row.get("id", "")),
                    str(row.get("title", "")),
                    str(row.get("type", "")).strip().lower() or "unknown",
                    str(row.get("status", "")).strip().lower() or "unknown",
                    str(row.get("summary_source", "")),
                    str(row.get("updated", "")),
                ]
            )
        )


def _cmd_stale(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    try:
        rows = list_stale_nodes(db_path, days=int(args.days))
    except Exception as exc:
        _emit_error("failed to load stale nodes", detail=str(exc))
        return 2

    if args.format == "table":
        _print_stale_table(rows)
    else:
        _emit_payload(rows, pretty=True)
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))
    scoring_config, scoring_source = _resolve_context_scoring(db_info)

    try:
        result = select_context(
            args.query,
            db_path,
            budget=int(args.budget),
            limit=int(args.limit),
            include_content=bool(args.include_content),
            actionable=bool(args.actionable),
            digest=str(args.digest),
            scoring_config=scoring_config,
            scoring_config_source=scoring_source,
        )
    except Exception as exc:
        _emit_error("context selection failed", detail=str(exc))
        return 2

    _emit_payload(with_contract_metadata(result, "context"), pretty=True)
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))
    scoring_config, scoring_source = _resolve_context_scoring(db_info)

    try:
        payload = build_start_payload(
            args.task,
            db_path,
            db_source=str(db_info.get("source", "unknown")),
            budget=int(args.budget),
            limit=int(args.limit),
            include_content=bool(args.include_content),
            digest=str(args.digest),
            scoring_config=scoring_config,
            scoring_config_source=scoring_source,
        )
    except Exception as exc:
        _emit_error("start failed", detail=str(exc))
        return 2

    _emit_payload(with_contract_metadata(payload, "start"), pretty=True)
    return 0


def _cmd_enrich(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

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
        node_id = resolve_node_id(args.path, db_path, path_mode=True)
        if node_id is None:
            _emit_error("node not found", path=args.path)
            return 2
    else:
        node_id = resolve_node_id(args.node, db_path, path_mode=False)
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

    result = enrich_node(node_id, db_path, summary_text, force=bool(args.force))
    if result.get("status") == "error":
        details = {key: value for key, value in result.items() if key not in {"status", "error"}}
        _emit_error(str(result.get("error", "enrich failed")), **details)
        return 2

    _emit_payload(result, pretty=True)
    return 0


def _cmd_impact(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    if bool(args.changed_files_from_git) and args.paths:
        _emit_error("invalid arguments", detail="use either paths or --changed-files-from-git")
        return 2

    if args.changed_files_from_git:
        try:
            runtime_context = load_runtime_context(Path.cwd())
            changed = collect_changed_files(runtime_context.repo_root, require_git=True)
        except GitError:
            _emit_error("not a git repository")
            return 2
        except Exception as exc:
            _emit_error("failed to collect git changed files", detail=str(exc))
            return 2
    else:
        changed = [str(path).replace("\\", "/") for path in args.paths if str(path).strip()]

    if not changed:
        _emit_error("invalid arguments", detail="specify paths or --changed-files-from-git")
        return 2

    try:
        payload = build_impact_report(db_path, changed, limit=int(args.limit))
    except Exception as exc:
        _emit_error("impact failed", detail=str(exc))
        return 2

    _emit_payload(with_contract_metadata(payload, "impact"), pretty=True)
    return 0


def _cmd_finish(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    try:
        context = load_runtime_context(Path.cwd())
        payload = run_finish(
            task=args.task,
            db_path=db_path,
            db_source=str(db_info.get("source", "unknown")),
            context=context,
            changed_files_from_git=bool(args.changed_files_from_git),
            dry_run=bool(args.dry_run),
            summary_file=args.summary_file,
            scan=bool(args.scan),
            limit=int(args.limit),
        )
    except FinishError as exc:
        _emit_error_payload(exc.payload)
        return 2
    except Exception as exc:
        _emit_error("finish failed", detail=str(exc))
        return 2

    _emit_payload(with_contract_metadata(payload, "finish"), pretty=True)
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    try:
        context = load_runtime_context(Path.cwd())
    except Exception as exc:
        _emit_error("failed to load runtime config", detail=str(exc))
        return 2

    title = str(args.title).strip()
    if not title:
        _emit_error("title is required")
        return 2

    try:
        if args.kind == "task":
            payload = create_task_file(context, title)
        else:
            payload = create_decision_file(context, title)
    except Exception as exc:
        _emit_error("new failed", detail=str(exc))
        return 2

    _emit_payload(payload, pretty=True)
    return 0


def _cmd_stamp(args: argparse.Namespace) -> int:
    db_info = _resolve_db(args, must_exist=True)
    if db_info is None:
        return 2
    db_path = str(Path(str(db_info["path"])))

    result = stamp_updated(args.target, db_path=db_path)
    if result.get("status") == "error":
        details = {key: value for key, value in result.items() if key not in {"status", "error"}}
        _emit_error(str(result.get("error", "stamp failed")), **details)
        return 2
    _emit_payload(result, pretty=True)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="mdex CLI")
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)

    scan_parser = subparsers.add_parser("scan", help="Scan indexable files and build an index")
    scan_parser.add_argument("--root", help="Directory to scan")
    scan_parser.add_argument("--output", help="Output JSON file path")
    scan_parser.add_argument("--db", help="Output SQLite file path")
    scan_parser.add_argument("--config", help="Path to scan config JSON")
    scan_parser.add_argument("--strict", action="store_true", help="Fail fast when any indexed file cannot be parsed")
    scan_parser.set_defaults(func=_cmd_scan)

    doctor_parser = subparsers.add_parser("doctor", help="Inspect index hygiene and generated artifact health")
    doctor_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    doctor_parser.add_argument("--json-index", help="Scan JSON output path to compare with SQLite metadata")
    doctor_parser.set_defaults(func=_cmd_doctor)

    list_parser = subparsers.add_parser("list", help="List nodes with optional filters")
    list_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    list_parser.add_argument("--type", help="Filter by type")
    list_parser.add_argument("--project", help="Filter by project")
    list_parser.add_argument("--status", help="Filter by status")
    list_parser.add_argument("--format", choices=["table", "json"], default="json")
    list_parser.set_defaults(func=_cmd_list)

    open_parser = subparsers.add_parser("open", help="Print source file content for a node id")
    open_parser.add_argument("node", help="Node id, for example docs/proposal.md")
    open_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    open_parser.add_argument("--root", default=".", help="Fallback root when metadata is absent")
    open_parser.set_defaults(func=_cmd_open)

    query_parser = subparsers.add_parser("query", help="Query one node and its neighbors")
    query_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    query_parser.add_argument("--node", required=True, help="Node id")
    query_parser.set_defaults(func=_cmd_query)

    find_parser = subparsers.add_parser("find", help="Find nodes by keyword")
    find_parser.add_argument("query", help="Search query")
    find_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    find_parser.add_argument("--limit", type=int, default=20, help="Maximum number of results")
    find_parser.add_argument("--format", choices=["table", "json"], default="json")
    find_parser.set_defaults(func=_cmd_find)

    orphan_parser = subparsers.add_parser("orphans", help="List nodes with no resolved edges")
    orphan_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    orphan_parser.add_argument("--format", choices=["table", "json"], default="json")
    orphan_parser.set_defaults(func=_cmd_orphans)

    stale_parser = subparsers.add_parser("stale", help="List stale seed summaries for enrich planning")
    stale_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    stale_parser.add_argument("--days", type=int, default=30, help="Minimum age in days")
    stale_parser.add_argument("--format", choices=["table", "json"], default="json")
    stale_parser.set_defaults(func=_cmd_stale)

    related_parser = subparsers.add_parser("related", help="Recommend related nodes to read next")
    related_parser.add_argument("node", help="Node id")
    related_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    related_parser.add_argument("--limit", type=int, default=10, help="Maximum number of results")
    related_parser.set_defaults(func=_cmd_related)

    first_parser = subparsers.add_parser("first", help="Return prerequisite nodes to read first")
    first_parser.add_argument("node", help="Node id")
    first_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    first_parser.add_argument("--limit", type=int, default=10, help="Maximum number of results")
    first_parser.set_defaults(func=_cmd_first)

    context_parser = subparsers.add_parser("context", help="Select context nodes for a query")
    context_parser.add_argument("query", help="Query text")
    context_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    context_parser.add_argument("--budget", type=int, default=4000, help="Token budget (soft)")
    context_parser.add_argument("--limit", type=int, default=10, help="Maximum nodes to return")
    context_parser.add_argument("--actionable", action="store_true", help="Return action-oriented output")
    context_parser.add_argument(
        "--digest",
        choices=["minimal", "full"],
        default="full",
        help="Actionable digest verbosity",
    )
    context_parser.add_argument(
        "--include-content",
        action="store_true",
        help="Include full node content in output",
    )
    context_parser.set_defaults(func=_cmd_context)

    start_parser = subparsers.add_parser("start", help="Task-start entry point with reading plan")
    start_parser.add_argument("task", help="Task description")
    start_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    start_parser.add_argument("--budget", type=int, default=4000, help="Token budget (soft)")
    start_parser.add_argument("--limit", type=int, default=10, help="Maximum nodes to consider")
    start_parser.add_argument(
        "--digest",
        choices=["minimal", "full"],
        default="full",
        help="Actionable digest verbosity",
    )
    start_parser.add_argument(
        "--include-content",
        action="store_true",
        help="Include full node content in output",
    )
    start_parser.set_defaults(func=_cmd_start)

    impact_parser = subparsers.add_parser("impact", help="Classify impacted docs from changed files")
    impact_parser.add_argument("paths", nargs="*", help="Changed paths to inspect")
    impact_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    impact_parser.add_argument("--limit", type=int, default=10, help="Maximum rows per category")
    impact_parser.add_argument(
        "--changed-files-from-git",
        action="store_true",
        help="Collect changed files from current git repository",
    )
    impact_parser.set_defaults(func=_cmd_impact)

    finish_parser = subparsers.add_parser("finish", help="Task-finish planner and optional apply step")
    finish_parser.add_argument("--task", required=True, help="Task description")
    finish_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    finish_parser.add_argument(
        "--changed-files-from-git",
        action="store_true",
        help="Require changed files from git (error when not in git repository)",
    )
    finish_parser.add_argument("--summary-file", help="Summary text file used for enrich apply")
    finish_parser.add_argument("--scan", action="store_true", help="Run scan after apply")
    finish_parser.add_argument("--dry-run", action="store_true", help="Return plan without writes")
    finish_parser.add_argument("--limit", type=int, default=10, help="Maximum rows per impact category")
    finish_parser.set_defaults(func=_cmd_finish)

    enrich_parser = subparsers.add_parser("enrich", help="Update node summary from provided text")
    enrich_parser.add_argument("node", nargs="?", help="Node id to enrich")
    enrich_parser.add_argument("--path", help="Absolute indexed file path to resolve as node id")
    enrich_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    enrich_parser.add_argument("--summary", help="Summary text to store")
    enrich_parser.add_argument("--summary-file", help="Path to file containing summary text")
    enrich_parser.add_argument("--force", action="store_true", help="Overwrite existing agent summary")
    enrich_parser.set_defaults(func=_cmd_enrich)

    new_parser = subparsers.add_parser("new", help="Create a task/decision document scaffold")
    new_subparsers = new_parser.add_subparsers(dest="kind", required=True, parser_class=JsonArgumentParser)
    new_task = new_subparsers.add_parser("task", help="Create task scaffold")
    new_task.add_argument("title", help="Task title")
    new_task.set_defaults(func=_cmd_new, kind="task")
    new_decision = new_subparsers.add_parser("decision", help="Create decision scaffold")
    new_decision.add_argument("title", help="Decision title")
    new_decision.set_defaults(func=_cmd_new, kind="decision")

    stamp_parser = subparsers.add_parser("stamp", help="Update frontmatter updated date for an indexed node id")
    stamp_parser.add_argument("target", help="Indexed node id")
    stamp_parser.add_argument("--db", help="Index SQLite file (auto-resolved when omitted)")
    stamp_parser.set_defaults(func=_cmd_stamp)

    return parser


def main() -> int:
    _force_utf8_stdio()
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
