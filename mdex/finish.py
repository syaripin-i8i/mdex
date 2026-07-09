from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from mdex.builder import build_index
from mdex.dbresolve import RuntimeContext
from mdex.enrich import enrich_node
from mdex.gittools import GitError, collect_changed_files
from mdex.impact import build_impact_report
from mdex.indexer import ScanOutputsWriteError, write_scan_outputs
from mdex.output_paths import (
    configured_generated_output_path,
    ensure_distinct_scan_outputs,
    ensure_outputs_do_not_overwrite_sources,
)
from mdex.path_identity import canonical_path_key, capture_directory_identities
from mdex.scan_manifest import (
    ScanManifestError,
    build_scan_manifest,
    canonical_config_hash,
    config_file_hash,
    load_scan_manifest,
    normalized_index_kind,
    set_scan_manifest,
)
from mdex.store import get_node, list_index_metadata, list_nodes


class FinishError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("error", "finish failed")))
        self.payload = payload


ScanWriteError = ScanOutputsWriteError


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
    config, _identity = _load_scan_config_with_identity(path)
    return config


def _load_scan_config_with_identity(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        payload = b""
        return {}, hashlib.sha256(payload).hexdigest()
    payload = path.read_bytes()
    loaded = json.loads(payload.decode("utf-8"))
    if isinstance(loaded, dict):
        return loaded, hashlib.sha256(payload).hexdigest()
    return {}, hashlib.sha256(payload).hexdigest()


def _scan_summary(index: dict[str, Any]) -> dict[str, Any]:
    edges = index.get("edges", [])
    edge_total = len(edges) if isinstance(edges, list) else 0
    return {
        "generated": str(index.get("generated", "")),
        "nodes": len(index.get("nodes", [])) if isinstance(index.get("nodes"), list) else 0,
        "edges": edge_total,
    }


def _metadata_path(context: RuntimeContext, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = context.repo_root / candidate
    return candidate.resolve()


def _normalized_paths(paths: list[Path]) -> list[str]:
    return sorted(canonical_path_key(path) for path in paths)


def _db_scan_scope(context: RuntimeContext, db_path: str) -> tuple[Path, list[Path]]:
    metadata = list_index_metadata(db_path)
    raw_scan_root = str(metadata.get("scan_root", "") or "").strip()
    raw_scan_roots = str(metadata.get("scan_roots", "") or "").strip()

    root_values: list[str] = []
    if raw_scan_roots:
        try:
            loaded = json.loads(raw_scan_roots)
        except json.JSONDecodeError as exc:
            raise FinishError(
                {
                    "error": "scan failed",
                    "detail": "cannot verify DB scan scope: scan_roots metadata is invalid JSON",
                    "reason": "scan_scope_unverifiable",
                    "partial_update": {"occurred": False, "stage": "scan_preflight", "applied_enrichments": []},
                }
            ) from exc
        if not isinstance(loaded, list):
            raise FinishError(
                {
                    "error": "scan failed",
                    "detail": "cannot verify DB scan scope: scan_roots metadata is not an array",
                    "reason": "scan_scope_unverifiable",
                    "partial_update": {"occurred": False, "stage": "scan_preflight", "applied_enrichments": []},
                }
            )
        root_values = [str(item).strip() for item in loaded if str(item).strip()]

    if not root_values and raw_scan_root:
        root_values = [raw_scan_root]
    if not raw_scan_root or not root_values:
        raise FinishError(
            {
                "error": "scan failed",
                "detail": "cannot verify DB scan scope: scan_root/scan_roots metadata is missing",
                "reason": "scan_scope_unverifiable",
                "partial_update": {"occurred": False, "stage": "scan_preflight", "applied_enrichments": []},
            }
        )

    return _metadata_path(context, raw_scan_root), [_metadata_path(context, value) for value in root_values]


def _common_scan_root(scan_roots: list[Path]) -> Path:
    try:
        return Path(os.path.commonpath([str(path.resolve()) for path in scan_roots])).resolve()
    except (OSError, ValueError) as exc:
        raise FinishError(
            {
                "error": "scan failed",
                "detail": "cannot verify current scan scope: scan roots have no common path",
                "reason": "scan_scope_unverifiable",
                "partial_update": {"occurred": False, "stage": "scan_preflight", "applied_enrichments": []},
            }
        ) from exc


def _validate_scan_scope(
    context: RuntimeContext,
    db_path: str,
    scan_roots: list[Path],
    *,
    node_id_root: Path | None = None,
) -> None:
    repo_root = context.repo_root.resolve()
    current_scan_root = node_id_root.resolve() if node_id_root is not None else _common_scan_root(scan_roots)
    db_scan_root, db_scan_roots = _db_scan_scope(context, db_path)

    db_outside_repo: list[str] = []
    for path in [db_scan_root, *db_scan_roots]:
        try:
            path.relative_to(repo_root)
        except ValueError:
            db_outside_repo.append(path.as_posix())

    roots_match = _normalized_paths(db_scan_roots) == _normalized_paths(scan_roots)
    scan_root_matches = canonical_path_key(db_scan_root) == canonical_path_key(current_scan_root)
    if db_outside_repo or not roots_match or not scan_root_matches:
        raise FinishError(
            {
                "error": "scan failed",
                "detail": "refusing finish --scan because DB scan scope does not match the current repository scan scope",
                "reason": "scan_scope_mismatch",
                "repo_root": repo_root.as_posix(),
                "db_scan_root": db_scan_root.as_posix(),
                "db_scan_roots": [path.as_posix() for path in db_scan_roots],
                "current_scan_root": current_scan_root.as_posix(),
                "current_scan_roots": [path.as_posix() for path in scan_roots],
                "db_roots_outside_repo": db_outside_repo,
                "partial_update": {"occurred": False, "stage": "scan_preflight", "applied_enrichments": []},
            }
        )


def _manifest_preflight_error(detail: str, *, reason: str = "scan_manifest_mismatch") -> FinishError:
    return FinishError(
        {
            "error": "scan failed",
            "detail": detail,
            "reason": reason,
            "partial_update": {
                "occurred": False,
                "stage": "scan_preflight",
                "applied_enrichments": [],
            },
        }
    )


def _manifest_repo_path(context: RuntimeContext, value: Any, *, key: str) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        raise _manifest_preflight_error(f"scan manifest {key} must be an absolute path")
    resolved = path.resolve()
    try:
        resolved.relative_to(context.repo_root.resolve())
    except ValueError as exc:
        raise _manifest_preflight_error(
            f"scan manifest {key} must stay within the current repository: {resolved}"
        ) from exc
    return resolved


def _prepare_scan(context: RuntimeContext, db_path: str) -> dict[str, Any]:
    metadata = list_index_metadata(db_path)
    try:
        manifest = load_scan_manifest(metadata)
    except ScanManifestError as exc:
        raise _manifest_preflight_error(str(exc), reason="scan_manifest_unverifiable") from exc

    repo_root = context.repo_root.resolve()
    manifest_repo = Path(str(manifest["repo_root"])).resolve()
    if canonical_path_key(manifest_repo) != canonical_path_key(repo_root):
        raise _manifest_preflight_error(
            "scan manifest repository does not match the current repository"
        )
    if str(manifest["index_kind"]) != "repo":
        raise _manifest_preflight_error(
            f"finish --scan only accepts the repo index lane, not {manifest['index_kind']!r}",
            reason="scan_manifest_wrong_lane",
        )

    safe_db_path = configured_generated_output_path(repo_root, db_path, key="scan manifest output.db")
    manifest_output = manifest["output"]
    manifest_db = Path(str(manifest_output["db"])).resolve()
    if canonical_path_key(manifest_db) != canonical_path_key(safe_db_path):
        raise _manifest_preflight_error(
            "scan manifest database path does not match the requested database"
        )

    raw_config_path = Path(str(manifest["config_identity"]["path"]))
    if not raw_config_path.is_absolute():
        raise _manifest_preflight_error("scan manifest config_path must be absolute")
    if raw_config_path.is_symlink():
        raise _manifest_preflight_error("scan manifest config_path must not be a symlink")
    scan_config_path = _manifest_repo_path(context, raw_config_path, key="config_path")
    if scan_config_path.exists():
        if not scan_config_path.is_file():
            raise _manifest_preflight_error("scan manifest config_path must be a regular file")
        if scan_config_path.stat().st_size > 1024 * 1024:
            raise _manifest_preflight_error("scan manifest config_path exceeds the 1 MiB safety limit")
    config, config_file_sha256 = _load_scan_config_with_identity(scan_config_path)
    identity = manifest["config_identity"]
    if canonical_path_key(str(manifest["config_path"])) != canonical_path_key(str(identity["path"])):
        raise _manifest_preflight_error("scan manifest config path fields are inconsistent")
    if canonical_path_key(str(identity["path"])) != canonical_path_key(scan_config_path):
        raise _manifest_preflight_error("scan manifest config identity path is inconsistent")
    if str(identity["sha256"]) != config_file_sha256:
        raise _manifest_preflight_error(
            "scan configuration changed since the indexed scan; run mdex scan explicitly"
        )
    if str(manifest["config_hash"]) != canonical_config_hash(config):
        raise _manifest_preflight_error(
            "effective scan configuration does not match the indexed scan; run mdex scan explicitly"
        )
    if normalized_index_kind(config.get("index_kind"), default="repo") != str(manifest["index_kind"]):
        raise _manifest_preflight_error("scan manifest index lane does not match its configuration")

    scan_roots = [
        _manifest_repo_path(context, value, key="scan_roots")
        for value in manifest["scan_roots"]
    ]
    node_id_root = _manifest_repo_path(context, manifest["node_id_root"], key="node_id_root")
    for scan_root in scan_roots:
        if not scan_root.exists() or not scan_root.is_dir():
            raise _manifest_preflight_error(
                f"scan manifest root is missing or not a directory: {scan_root}"
            )
    if not node_id_root.exists() or not node_id_root.is_dir():
        raise _manifest_preflight_error(
            f"scan manifest node_id_root is missing or not a directory: {node_id_root}"
        )
    root_identities = capture_directory_identities([*scan_roots, node_id_root])
    _validate_scan_scope(
        context,
        str(safe_db_path),
        scan_roots,
        node_id_root=node_id_root,
    )

    output_path = configured_generated_output_path(
        repo_root,
        str(manifest_output["json"]),
        key="scan manifest output.json",
    )
    if canonical_path_key(str(manifest["output_json"])) != canonical_path_key(output_path):
        raise _manifest_preflight_error("scan manifest JSON output fields are inconsistent")
    ensure_distinct_scan_outputs(safe_db_path, output_path)

    return {
        "config": config,
        "scan_roots": scan_roots,
        "scan_root_warnings": [],
        "node_id_root": node_id_root,
        "config_path": scan_config_path,
        "config_file_sha256": config_file_sha256,
        "output_path": output_path,
        "output_origin": str(manifest["output_origin"]),
        "index_kind": str(manifest["index_kind"]),
        "strict": bool(manifest["strict"]),
        "root_identities": root_identities,
        "expected_database_state": {
            "kind": "manifest",
            "scan_id": str(manifest["scan_id"]),
        },
        "previous_manifest": manifest,
    }


def _run_scan(
    context: RuntimeContext,
    db_path: str,
    *,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_plan = plan if plan is not None else _prepare_scan(context, db_path)
    config = active_plan["config"]
    scan_roots = active_plan["scan_roots"]
    scan_root_warnings = active_plan["scan_root_warnings"]
    output_path = Path(active_plan["output_path"])
    node_id_root = Path(active_plan.get("node_id_root", _common_scan_root(scan_roots))).resolve()
    config_path = Path(active_plan.get("config_path", context.config_path)).resolve()
    raw_config_file_sha256 = active_plan.get("config_file_sha256")
    config_file_sha256 = (
        str(raw_config_file_sha256)
        if isinstance(raw_config_file_sha256, str) and raw_config_file_sha256
        else config_file_hash(config_path)
    )
    output_origin = str(active_plan.get("output_origin", "config"))
    index_kind = str(active_plan.get("index_kind", config.get("index_kind", "repo")))
    strict = bool(active_plan.get("strict", False))
    index = build_index(scan_roots, config, strict=strict, node_id_root=node_id_root)
    ensure_outputs_do_not_overwrite_sources(index, db_path, output_path)
    warnings = [item for item in index.get("warnings", []) if isinstance(item, dict)]
    for warning in scan_root_warnings:
        warnings.append({"path": "scan_config", "error": warning})
    index["warnings"] = warnings
    set_scan_manifest(
        index,
        build_scan_manifest(
            repo_root=context.repo_root,
            scan_roots=scan_roots,
            node_id_root=node_id_root,
            config_path=config_path,
            config=config,
            db_output=db_path,
            output_json=output_path,
            output_origin=output_origin,
            index_kind=index_kind,
            config_file_sha256=config_file_sha256,
            strict=strict,
        ),
    )
    write_scan_outputs(
        index,
        db_path,
        str(output_path),
        expected_database_state=active_plan.get("expected_database_state"),
        expected_root_identities=active_plan.get("root_identities"),
    )
    return {**_scan_summary(index), "output": {"db": db_path, "json": str(output_path)}}


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


def _finish_suspicion_signals(impact_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    def rows(key: str) -> list[dict[str, Any]]:
        value = impact_payload.get(key, [])
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    return {
        "suspiciously_unupdated": rows("stale_watch"),
        "likely_missing_links": rows("isolated_changes"),
        "unreviewed_neighbors": rows("unusual_neighbors"),
        "decision_gap_candidates": rows("missing_decision_links"),
    }


def _noop_state(
    *,
    dry_run: bool,
    changed_rows: list[dict[str, Any]],
    enrich_candidates: list[dict[str, Any]],
    applied_enrichments: list[dict[str, Any]],
    scan_payload: dict[str, Any],
) -> tuple[bool, str]:
    has_changed_files = bool(changed_rows)
    has_candidates = bool(enrich_candidates)
    has_applied = bool(applied_enrichments)
    scan_ran = bool(scan_payload.get("ran", False))

    if not has_changed_files and not has_candidates and not has_applied and not scan_ran:
        if dry_run:
            return True, "dry-run completed with no changed files and no enrich candidates"
        return True, "no changes were detected and no follow-up actions were applied"

    if has_candidates:
        return False, "impact produced enrich candidates"
    if has_changed_files:
        return False, "changed files were detected"
    if has_applied:
        return False, "enrich updates were applied"
    if scan_ran:
        return False, "scan follow-up was executed"
    return False, "non-noop finish path"


def _scan_failure(
    exc: Exception,
    *,
    applied_enrichments: list[dict[str, Any]],
) -> FinishError:
    if isinstance(exc, FinishError):
        payload = dict(exc.payload)
    else:
        payload = {"error": "scan failed", "detail": str(exc)}

    payload.setdefault("error", "scan failed")
    original_detail = str(payload.get("detail", str(exc))).strip()
    enrich_applied = bool(applied_enrichments)
    db_written = isinstance(exc, ScanWriteError) and exc.db_written
    json_written = isinstance(exc, ScanWriteError) and exc.json_written
    output_partial = db_written != json_written
    partial = enrich_applied or output_partial
    if enrich_applied:
        payload["detail"] = (
            "scan failed after enrich updates were applied; the DB contains those enrich updates but the requested "
            f"rescan did not complete: {original_detail}"
        )
    elif output_partial:
        payload["detail"] = (
            "scan updated the SQLite index but failed to update the JSON index; run mdex scan to resynchronize "
            f"outputs: {original_detail}"
        )
    else:
        payload["detail"] = original_detail or "requested rescan did not complete"
    if output_partial:
        stage = "scan_json_after_enrich" if enrich_applied else "scan_json"
    else:
        stage = "scan_after_enrich" if enrich_applied else "scan"
    payload["partial_update"] = {
        "occurred": partial,
        "stage": stage,
        "applied_enrichments": [dict(item) for item in applied_enrichments],
        "scan_outputs": {
            "db_written": db_written,
            "json_written": json_written,
            "db": exc.db_path if isinstance(exc, ScanWriteError) else None,
            "json": exc.json_path if isinstance(exc, ScanWriteError) else None,
        },
    }
    return FinishError(payload)


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

    scan_plan: dict[str, Any] | None = None
    if bool(scan) and not dry_run:
        try:
            scan_plan = _prepare_scan(context, db_path)
        except FinishError:
            raise
        except Exception as exc:
            raise _scan_failure(exc, applied_enrichments=[]) from exc

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
            scan_result = _run_scan(context, db_path, plan=scan_plan)
            scan_payload["ran"] = True
            scan_payload["result"] = scan_result
        except Exception as exc:
            raise _scan_failure(exc, applied_enrichments=applied_enrichments) from exc

    recommended = _next_actions(task, changed_files, enrich_candidates, requires_manual_targeting)
    changed_rows = [{"path": path, "source": "git"} for path in changed_files]
    noop, noop_reason = _noop_state(
        dry_run=bool(dry_run),
        changed_rows=changed_rows,
        enrich_candidates=enrich_candidates,
        applied_enrichments=applied_enrichments,
        scan_payload=scan_payload,
    )
    return {
        "status": "success",
        "noop": bool(noop),
        "noop_reason": noop_reason,
        "task": task,
        "dry_run": bool(dry_run),
        "db": {
            "path": db_path,
            "source": db_source,
        },
        "changed_files": changed_rows,
        "impact": impact_payload,
        "suspicion_signals": _finish_suspicion_signals(impact_payload),
        "enrich_candidates": enrich_candidates,
        "applied_enrichments": applied_enrichments,
        "scan": scan_payload,
        "recommended_next_actions": recommended,
        "requires_manual_targeting": bool(requires_manual_targeting),
    }
