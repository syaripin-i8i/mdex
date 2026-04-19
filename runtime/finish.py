from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.builder import build_index
from runtime.dbresolve import RuntimeContext, resolve_scan_config_path, resolve_scan_roots
from runtime.enrich import enrich_node
from runtime.gittools import GitError, collect_changed_files
from runtime.impact import build_impact_report
from runtime.indexer import write_sqlite
from runtime.store import get_node, list_nodes


class FinishError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("error", "finish failed")))
        self.payload = payload


def _to_posix_path(value: str) -> str:
    return value.replace("\\", "/")


def _read_summary_file(path: str) -> str:
    summary_path = Path(path)
    if not summary_path.exists():
        raise FinishError({"error": "summary file not found", "path": str(summary_path)})
    try:
        text = summary_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise FinishError(
            {"error": "failed to read summary file", "path": str(summary_path), "detail": str(exc)}
        ) from exc
    if not text:
        raise FinishError({"error": "summary is required", "path": str(summary_path)})
    return text


def _candidate_rows(impact_payload: dict[str, Any]) -> list[dict[str, Any]]:
    collected: dict[str, dict[str, Any]] = {}
    for key in ("read_first", "stale_watch"):
        rows = impact_payload.get(key, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            node_id = str(row.get("id", "")).strip()
            if not node_id:
                continue
            score = float(row.get("score", 0.0) or 0.0)
            reason = str(row.get("reason", "")).strip() or "impact proximity"
            prior = collected.get(node_id)
            if prior is None or score > float(prior.get("score", 0.0)):
                collected[node_id] = {"id": node_id, "score": score, "reason": reason}
    return sorted(collected.values(), key=lambda item: (-float(item["score"]), item["id"]))


def _has_stem_match(changed_paths: list[str], node_id: str) -> bool:
    node_stem = Path(node_id).stem.lower()
    if not node_stem:
        return False
    stems = {Path(path).stem.lower() for path in changed_paths if Path(path).stem}
    return node_stem in stems


def _primary_ids(
    ranked: list[dict[str, Any]],
    *,
    changed_paths: list[str],
    node_map: dict[str, dict[str, Any]],
) -> set[str]:
    primary: set[str] = set()
    if not ranked:
        return primary

    for row in ranked:
        node_id = str(row.get("id", ""))
        reason = str(row.get("reason", "")).lower()
        node = node_map.get(node_id, {})
        node_type = str(node.get("type", "")).strip().lower()
        if "exact path match" in reason or "direct path reference" in reason:
            primary.add(node_id)
        if node_type in {"design", "reference"} and _has_stem_match(changed_paths, node_id):
            primary.add(node_id)

    if ranked:
        top = float(ranked[0].get("score", 0.0) or 0.0)
        second = float(ranked[1].get("score", 0.0) or 0.0) if len(ranked) > 1 else 0.0
        if second <= 0:
            if top > 0:
                primary.add(str(ranked[0].get("id", "")))
        elif top >= second * 1.5:
            primary.add(str(ranked[0].get("id", "")))

    return primary


def _build_enrich_candidates(
    impact_payload: dict[str, Any],
    *,
    changed_paths: list[str],
    node_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    ranked = _candidate_rows(impact_payload)
    primary = _primary_ids(ranked, changed_paths=changed_paths, node_map=node_map)

    output: list[dict[str, Any]] = []
    ordered_primary: list[str] = []
    for row in ranked:
        node_id = str(row.get("id", ""))
        kind = "primary" if node_id in primary else "secondary"
        if kind == "primary":
            ordered_primary.append(node_id)
        output.append(
            {
                "id": node_id,
                "kind": kind,
                "reason": str(row.get("reason", "")),
                "score": round(float(row.get("score", 0.0) or 0.0), 3),
            }
        )
    return output, ordered_primary


def _load_scan_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        return loaded
    return {}


def _scan_summary(index: dict[str, Any]) -> dict[str, Any]:
    edges = index.get("edges", [])
    edge_total = len(edges) if isinstance(edges, list) else 0
    return {
        "generated": str(index.get("generated", "")),
        "nodes": len(index.get("nodes", [])) if isinstance(index.get("nodes"), list) else 0,
        "edges": edge_total,
    }


def _run_scan(context: RuntimeContext, db_path: str) -> dict[str, Any]:
    scan_config_path = resolve_scan_config_path(context)
    config = _load_scan_config(scan_config_path)
    scan_roots, scan_root_warnings = resolve_scan_roots(context, config=config)
    index = build_index(scan_roots, config)
    warnings = [item for item in index.get("warnings", []) if isinstance(item, dict)]
    for warning in scan_root_warnings:
        warnings.append({"path": "scan_config", "error": warning})
    index["warnings"] = warnings
    write_sqlite(index, db_path)
    return _scan_summary(index)


def _next_actions(
    task: str,
    changed_files: list[str],
    enrich_candidates: list[dict[str, Any]],
    requires_manual_targeting: bool,
) -> list[str]:
    actions: list[str] = []
    if enrich_candidates:
        actions.append(f"review {enrich_candidates[0]['id']} before closing the task")
    if changed_files:
        actions.append(f"confirm impacted files for task '{task}'")
    if requires_manual_targeting:
        actions.append("run mdex enrich <node-id> --summary-file <path> after selecting a target")
    else:
        primary = next((item for item in enrich_candidates if item.get("kind") == "primary"), None)
        if primary is not None:
            actions.append(f"prepare summary text for {primary['id']}")
    if not changed_files:
        actions.append("run mdex finish --changed-files-from-git to inspect git-based impact")
    return actions[:5]


def run_finish(
    *,
    task: str,
    db_path: str,
    db_source: str,
    context: RuntimeContext,
    changed_files_from_git: bool,
    dry_run: bool,
    summary_file: str | None,
    scan: bool,
    limit: int = 10,
) -> dict[str, Any]:
    try:
        changed = collect_changed_files(
            context.repo_root,
            require_git=bool(changed_files_from_git),
        )
    except GitError:
        raise FinishError({"error": "not a git repository", "hint": "omit --changed-files-from-git"})

    changed_files = [_to_posix_path(path) for path in changed]
    impact_payload = build_impact_report(db_path, changed_files, limit=limit)
    node_map = {str(node.get("id", "")): node for node in list_nodes(db_path)}
    enrich_candidates, primary_ids = _build_enrich_candidates(
        impact_payload,
        changed_paths=changed_files,
        node_map=node_map,
    )

    applied_enrichments: list[dict[str, Any]] = []
    requires_manual_targeting = False

    summary_text = None
    if summary_file:
        summary_text = _read_summary_file(summary_file)

    if not dry_run and summary_text is not None:
        if len(primary_ids) != 1:
            requires_manual_targeting = True
        else:
            target_id = primary_ids[0]
            if get_node(db_path, target_id) is None:
                requires_manual_targeting = True
            else:
                result = enrich_node(target_id, db_path, summary_text, force=False)
                if result.get("status") == "error":
                    raise FinishError(
                        {
                            "error": "enrich failed",
                            "node_id": target_id,
                            "detail": str(result.get("error", "unknown")),
                        }
                    )
                applied_enrichments.append(result)

    scan_payload: dict[str, Any] = {"requested": bool(scan), "ran": False}
    if bool(scan) and not dry_run:
        try:
            scan_result = _run_scan(context, db_path)
            scan_payload["ran"] = True
            scan_payload["result"] = scan_result
        except Exception as exc:
            scan_payload["ran"] = False
            scan_payload["error"] = str(exc)

    recommended = _next_actions(task, changed_files, enrich_candidates, requires_manual_targeting)
    changed_rows = [{"path": path, "source": "git"} for path in changed_files]
    return {
        "task": task,
        "dry_run": bool(dry_run),
        "db": {
            "path": db_path,
            "source": db_source,
        },
        "changed_files": changed_rows,
        "impact": impact_payload,
        "enrich_candidates": enrich_candidates,
        "applied_enrichments": applied_enrichments,
        "scan": scan_payload,
        "recommended_next_actions": recommended,
        "requires_manual_targeting": bool(requires_manual_targeting),
    }
