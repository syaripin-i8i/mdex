from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from mdex.store import list_index_metadata, list_node_override_ids, list_nodes

LOCAL_SECRET_PATTERNS = (
    ".env*",
    "**/.env*",
    "*.local.md",
    "*.local.json",
    "*.local.jsonl",
    "**/*.local.md",
    "**/*.local.json",
    "**/*.local.jsonl",
    "secrets.*",
    "**/secrets.*",
    "credentials.*",
    "**/credentials.*",
)

REVIEW_DIRECTORY_NAMES = {"old", "archive", "archives", "backup", "backups"}

SEVERITY_RANK = {"ok": 0, "info": 1, "warning": 2, "error": 3}


def _to_posix(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _pattern_variants(pattern: str) -> list[str]:
    normalized = _to_posix(pattern.strip())
    if not normalized:
        return []

    variants = {normalized}
    if normalized.startswith("**/"):
        variants.add(normalized[len("**/") :])
    if not normalized.startswith("**/"):
        variants.add(f"**/{normalized}")
    return sorted(variants)


def _matches_any(path_value: str, patterns: tuple[str, ...]) -> bool:
    path = _to_posix(path_value)
    return any(
        fnmatch.fnmatch(path, variant)
        for pattern in patterns
        for variant in _pattern_variants(pattern)
    )


def _has_review_directory(path_value: str) -> bool:
    parts = [part.strip().lower() for part in Path(_to_posix(path_value)).parts]
    return any(part in REVIEW_DIRECTORY_NAMES for part in parts)


def _safe_json_list(raw_value: str) -> list[dict[str, Any]]:
    if not raw_value.strip():
        return []
    try:
        loaded = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _check_result(name: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    status = "ok"
    if findings:
        status = max(
            (str(item.get("severity", "warning")) for item in findings),
            key=lambda value: SEVERITY_RANK.get(value, 2),
        )
    return {"name": name, "status": status, "findings": findings}


def _scan_warning_findings(metadata: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for warning in _safe_json_list(str(metadata.get("warnings", ""))):
        path = str(warning.get("path", "")).strip()
        error = str(warning.get("error", "")).strip()
        findings.append(
            {
                "severity": "warning",
                "path": path,
                "message": error or "scan warning is present",
            }
        )
    return findings


def _indexed_path_findings(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            continue
        if _matches_any(node_id, LOCAL_SECRET_PATTERNS):
            findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "message": "local or secret-like file is indexed",
                }
            )
        elif _has_review_directory(node_id):
            findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "message": "old/archive-style path is indexed; verify it still belongs in active context",
                }
            )
    return findings


def _override_findings(nodes: list[dict[str, Any]], override_ids: list[str]) -> list[dict[str, Any]]:
    indexed_ids = {str(node.get("id", "")).strip() for node in nodes if str(node.get("id", "")).strip()}
    stale_ids = [node_id for node_id in override_ids if node_id not in indexed_ids]
    return [
        {
            "severity": "warning",
            "path": node_id,
            "message": "node override exists for a node that is no longer indexed; run mdex scan to prune it",
        }
        for node_id in stale_ids
    ]


def _json_sync_findings(metadata: dict[str, str], json_index_path: Path | None) -> list[dict[str, Any]]:
    if json_index_path is None:
        return []

    findings: list[dict[str, Any]] = []
    db_generated = str(metadata.get("generated", "")).strip()
    if not json_index_path.exists():
        return [
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "scan JSON output is missing; run mdex scan to refresh generated artifacts",
            }
        ]

    try:
        loaded = json.loads(json_index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": f"scan JSON output could not be read: {exc}",
            }
        ]

    json_generated = str(loaded.get("generated", "") if isinstance(loaded, dict) else "").strip()
    if db_generated and json_generated and db_generated != json_generated:
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "scan JSON and SQLite generated timestamps differ; run mdex scan",
            }
        )
    return findings


def _legacy_artifact_findings(repo_root: Path | None, db_path: Path) -> list[dict[str, Any]]:
    if repo_root is None:
        return []

    findings: list[dict[str, Any]] = []
    legacy_candidates = [
        repo_root / "mdex_index.db",
        repo_root / "mdex_index.json",
    ]
    current_db = db_path.resolve()
    for candidate in legacy_candidates:
        resolved = candidate.resolve()
        if resolved == current_db:
            continue
        if candidate.exists():
            findings.append(
                {
                    "severity": "warning",
                    "path": str(candidate),
                    "message": "legacy generated artifact exists outside .mdex; remove it if unused",
                }
            )
    return findings


def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for check in checks:
        for finding in check.get("findings", []):
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity", "warning"))
            if severity in counts:
                counts[severity] += 1
    return counts


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = [str(check.get("status", "ok")) for check in checks]
    highest = max(statuses or ["ok"], key=lambda value: SEVERITY_RANK.get(value, 0))
    return highest if highest != "info" else "ok"


def _recommended_next_actions(summary: dict[str, int]) -> list[str]:
    actions: list[str] = []
    if summary.get("warning", 0) or summary.get("error", 0):
        actions.append("review mdex doctor findings")
    if summary.get("warning", 0):
        actions.append("run mdex scan after updating exclude_patterns or removing stale artifacts")
    if summary.get("error", 0):
        actions.append("repair mdex index artifacts before relying on context selection")
    return actions


def build_doctor_report(
    db_path: str,
    *,
    repo_root: Path | None = None,
    json_index_path: Path | None = None,
) -> dict[str, Any]:
    nodes = list_nodes(db_path)
    metadata = list_index_metadata(db_path)
    override_ids = list_node_override_ids(db_path)
    db_path_obj = Path(db_path)

    checks = [
        _check_result("scan_warnings", _scan_warning_findings(metadata)),
        _check_result("indexed_path_hygiene", _indexed_path_findings(nodes)),
        _check_result("orphan_overrides", _override_findings(nodes, override_ids)),
        _check_result("json_sqlite_sync", _json_sync_findings(metadata, json_index_path)),
        _check_result("legacy_artifacts", _legacy_artifact_findings(repo_root, db_path_obj)),
    ]
    summary = _summary(checks)
    status = _overall_status(checks)
    return {
        "status": status,
        "summary": summary,
        "checks": checks,
        "recommended_next_actions": _recommended_next_actions(summary),
    }
