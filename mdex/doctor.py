from __future__ import annotations

import fnmatch
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mdex.health import evaluate_index_health, index_status_from_health
from mdex.observe import telemetry_health_findings
from mdex.path_identity import canonical_path_key
from mdex.store import list_index_metadata, list_missing_links, list_node_override_ids, list_node_overrides, list_nodes

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
WAREHOUSE_DIRECTORY_NAMES = {
    "dump",
    "dumps",
    "eval",
    "evals",
    "evaluation",
    "evaluations",
    "fixture",
    "fixtures",
    "log",
    "logs",
    "raw",
    "raw_logs",
    "runtime_state",
}

SEVERITY_RANK = {"ok": 0, "info": 1, "warning": 2, "error": 3}

DEFAULT_DOCTOR_POLICY: dict[str, Any] = {
    "allowlist_patterns": [],
    "generated_path_patterns": [
        ".mdex/**",
        "**/.mdex/**",
        ".cache/**",
        "**/.cache/**",
        "cache/**",
        "**/cache/**",
        "generated/**",
        "**/generated/**",
        "output/**",
        "**/output/**",
        "outputs/**",
        "**/outputs/**",
        "dist/**",
        "**/dist/**",
        "build/**",
        "**/build/**",
        "coverage/**",
        "**/coverage/**",
    ],
    "text_extensions": [".md", ".markdown", ".txt", ".rst", ".adoc"],
    "max_text_document_tokens": 20_000,
    "max_node_tokens": 50_000,
    "max_index_tokens": 1_000_000,
    "max_index_files": 10_000,
}


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


def _matches_any(path_value: str, patterns: tuple[str, ...] | list[str]) -> bool:
    path = _to_posix(path_value)
    return any(
        fnmatch.fnmatch(path, variant)
        for pattern in patterns
        for variant in _pattern_variants(pattern)
    )


def _has_review_directory(path_value: str) -> bool:
    parts = [part.strip().lower() for part in Path(_to_posix(path_value)).parts]
    return any(part in REVIEW_DIRECTORY_NAMES for part in parts)


def _has_warehouse_directory(path_value: str) -> bool:
    parts = [part.strip().lower() for part in Path(_to_posix(path_value)).parts]
    return any(part in WAREHOUSE_DIRECTORY_NAMES for part in parts)


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
    normalized_findings: list[dict[str, Any]] = []
    for finding in findings:
        normalized = dict(finding)
        normalized.setdefault("check", name)
        normalized.setdefault("reason", name)
        normalized.setdefault("path", "")
        normalized.setdefault("recommended_action", "review mdex doctor finding")
        normalized_findings.append(normalized)
    status = "ok"
    if normalized_findings:
        status = max(
            (str(item.get("severity", "warning")) for item in normalized_findings),
            key=lambda value: SEVERITY_RANK.get(value, 2),
        )
    return {"name": name, "status": status, "findings": normalized_findings}


def _positive_policy_int(policy: dict[str, Any], key: str) -> int:
    default = int(DEFAULT_DOCTOR_POLICY[key])
    try:
        value = int(policy.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _load_doctor_policy(
    metadata: dict[str, str], config_path: Path | None
) -> tuple[dict[str, Any], list[str]]:
    policy = dict(DEFAULT_DOCTOR_POLICY)
    exclude_patterns: list[str] = []
    candidates: list[Path] = []
    raw_manifest = str(metadata.get("scan_manifest", "") or "").strip()
    if raw_manifest:
        try:
            manifest = json.loads(raw_manifest)
        except json.JSONDecodeError:
            manifest = None
        if isinstance(manifest, dict) and str(manifest.get("config_path", "")).strip():
            candidates.append(Path(str(manifest["config_path"])))
    if config_path is not None:
        candidates.append(config_path)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve())
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(loaded, dict):
            continue
        exclude_patterns.extend(
            str(item).strip()
            for item in list(loaded.get("exclude_patterns", []) or [])
            if str(item).strip()
        )
        configured = loaded.get("doctor_policy", {})
        if isinstance(configured, dict):
            for name in DEFAULT_DOCTOR_POLICY:
                if name in configured:
                    policy[name] = configured[name]
        break

    for key in (
        "max_text_document_tokens",
        "max_node_tokens",
        "max_index_tokens",
        "max_index_files",
    ):
        policy[key] = _positive_policy_int(policy, key)
    for key in ("allowlist_patterns", "generated_path_patterns", "text_extensions"):
        raw = policy.get(key, [])
        policy[key] = [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else list(DEFAULT_DOCTOR_POLICY[key])
    return policy, exclude_patterns


def _policy_disposition(
    path: str, policy: dict[str, Any], exclude_patterns: list[str]
) -> str:
    if _matches_any(path, list(policy.get("allowlist_patterns", []))):
        return "allowlisted"
    if _matches_any(path, exclude_patterns):
        return "excluded"
    return "indexed"


def _git_index_state(repo_root: Path) -> dict[str, Any]:
    try:
        probe = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        return {"status": "unavailable", "detail": str(exc)}
    if probe.returncode != 0:
        return {"status": "unavailable", "detail": probe.stderr.strip() or "not a git repository"}
    git_root = Path(probe.stdout.strip()).resolve()
    try:
        repo_prefix = repo_root.resolve().relative_to(git_root).as_posix()
    except ValueError:
        return {"status": "unavailable", "detail": "repository root is outside Git top level"}

    def paths(*args: str) -> set[str] | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        except (OSError, UnicodeError):
            return None
        if result.returncode != 0:
            return None
        values = {_to_posix(item) for item in result.stdout.split("\0") if item}
        if repo_prefix in {"", "."}:
            return values
        prefix = f"{repo_prefix}/"
        return {item[len(prefix) :] for item in values if item.startswith(prefix)}

    tracked = paths("ls-files", "-z", "--cached")
    if tracked is None:
        return {"status": "unavailable", "detail": "git ls-files failed"}
    return {"status": "available", "tracked": tracked}


def _untracked_file_findings(
    nodes: list[dict[str, Any]],
    repo_root: Path | None,
    node_id_root: Path | None,
    policy: dict[str, Any],
    exclude_patterns: list[str],
) -> list[dict[str, Any]]:
    if repo_root is None:
        return [
            {
                "severity": "warning",
                "path": "",
                "reason": "git_state_unavailable",
                "message": "Git repository root is unavailable; indexed file tracking state is unknown",
                "recommended_action": "run mdex doctor from the indexed Git repository",
                "state": "unavailable",
            }
        ]
    state = _git_index_state(repo_root)
    if state.get("status") != "available":
        return [
            {
                "severity": "warning",
                "path": str(repo_root),
                "reason": "git_state_unavailable",
                "message": "Git tracking state is unavailable; indexed files may be untracked",
                "recommended_action": "make Git available and rerun mdex doctor",
                "state": "unavailable",
                "detail": str(state.get("detail", "")),
            }
        ]
    tracked = set(state.get("tracked", set()))
    if node_id_root is not None:
        try:
            anchor = node_id_root.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return [
                {
                    "severity": "warning",
                    "path": str(node_id_root),
                    "reason": "git_state_unavailable",
                    "message": "node id root is outside the Git repository; tracking state is unknown",
                    "recommended_action": "use a node id root within the indexed Git repository",
                    "state": "unavailable",
                }
            ]
        if anchor not in {"", "."}:
            prefix = f"{anchor}/"
            tracked = {item[len(prefix) :] for item in tracked if item.startswith(prefix)}
    findings: list[dict[str, Any]] = []
    for node in nodes:
        node_id = _to_posix(str(node.get("id", "")).strip())
        if not node_id or node_id in tracked:
            continue
        disposition = _policy_disposition(node_id, policy, exclude_patterns)
        if disposition != "indexed":
            findings.append(
                {
                    "severity": "info",
                    "path": node_id,
                    "reason": f"untracked_path_{disposition}",
                    "message": f"untracked indexed path is explicitly {disposition} by policy",
                    "recommended_action": "no action required while the policy exemption is intentional",
                    "policy_disposition": disposition,
                }
            )
            continue
        findings.append(
            {
                "severity": "warning",
                "path": node_id,
                "reason": "indexed_file_untracked",
                "message": "Git-untracked file is present in the repository index",
                "recommended_action": "track the file or add it to scan exclude_patterns/doctor_policy.allowlist_patterns",
                "policy_disposition": disposition,
            }
        )
    return findings


def _generated_path_findings(
    nodes: list[dict[str, Any]], policy: dict[str, Any], exclude_patterns: list[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    patterns = list(policy.get("generated_path_patterns", []))
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        if not node_id or not _matches_any(node_id, patterns):
            continue
        disposition = _policy_disposition(node_id, policy, exclude_patterns)
        severity = "warning" if disposition == "indexed" else "info"
        findings.append(
            {
                "severity": severity,
                "path": node_id,
                "reason": "generated_path_indexed" if severity == "warning" else f"generated_path_{disposition}",
                "message": "generated/cache/output-style path is indexed" if severity == "warning" else f"generated-style path is explicitly {disposition} by policy",
                "recommended_action": "exclude generated output or add a deliberate doctor allowlist entry" if severity == "warning" else "no action required while the policy exemption is intentional",
                "policy_disposition": disposition,
            }
        )
    return findings


def _surface_budget_findings(
    nodes: list[dict[str, Any]],
    policy: dict[str, Any],
    exclude_patterns: list[str],
    repo_root: Path | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    text_findings: list[dict[str, Any]] = []
    node_findings: list[dict[str, Any]] = []
    total_findings: list[dict[str, Any]] = []
    text_extensions = {str(item).lower() for item in policy.get("text_extensions", [])}
    max_text = int(policy["max_text_document_tokens"])
    max_node = int(policy["max_node_tokens"])
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        tokens = int(node.get("estimated_tokens", 0) or 0)
        disposition = _policy_disposition(node_id, policy, exclude_patterns)
        if disposition != "indexed":
            continue
        if Path(node_id).suffix.lower() in text_extensions and tokens > max_text:
            text_findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "reason": "oversized_text_document",
                    "message": "Markdown/text document exceeds the configured token threshold",
                    "recommended_action": "split or exclude the document, or raise doctor_policy.max_text_document_tokens deliberately",
                    "estimated_tokens": tokens,
                    "threshold_tokens": max_text,
                }
            )
        if tokens > max_node:
            node_findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "reason": "single_node_token_budget_exceeded",
                    "message": "single indexed node exceeds the configured token budget",
                    "recommended_action": "split or exclude the node, or raise doctor_policy.max_node_tokens deliberately",
                    "estimated_tokens": tokens,
                    "threshold_tokens": max_node,
                }
            )
    total_tokens = sum(int(node.get("estimated_tokens", 0) or 0) for node in nodes)
    file_count = len(nodes)
    surface_path = str(repo_root or "")
    if total_tokens > int(policy["max_index_tokens"]):
        total_findings.append(
            {
                "severity": "warning",
                "path": surface_path,
                "reason": "index_token_budget_exceeded",
                "message": "total index estimated tokens exceed the configured budget",
                "recommended_action": "narrow scan roots/excludes or raise doctor_policy.max_index_tokens deliberately",
                "estimated_tokens": total_tokens,
                "threshold_tokens": int(policy["max_index_tokens"]),
            }
        )
    if file_count > int(policy["max_index_files"]):
        total_findings.append(
            {
                "severity": "warning",
                "path": surface_path,
                "reason": "index_file_budget_exceeded",
                "message": "indexed file count exceeds the configured budget",
                "recommended_action": "narrow scan roots/excludes or raise doctor_policy.max_index_files deliberately",
                "indexed_files": file_count,
                "threshold_files": int(policy["max_index_files"]),
            }
        )
    return text_findings, node_findings, total_findings


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
        elif _has_warehouse_directory(node_id):
            findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "message": "fixture/eval/log/dump-style path is indexed; prefer a separate index or direct reads",
                }
            )
        elif Path(node_id).name == ".DS_Store":
            findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "message": ".DS_Store is indexed; add it to exclude_patterns",
                }
            )
        elif node_id.lower().endswith((".json", ".jsonl")) and int(node.get("estimated_tokens", 0) or 0) > 20000:
            findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "message": "large JSON/JSONL node is indexed; prefer excluding generated or raw data",
                    "estimated_tokens": int(node.get("estimated_tokens", 0) or 0),
                }
            )
        elif (node_id.startswith("tasks/archive/") or "/tasks/archive/" in node_id) and str(node.get("status", "")).strip().lower() not in {"done", "archived"}:
            findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "message": "task archive path is indexed without done/archived status",
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


def _override_freshness_findings(nodes: list[dict[str, Any]], overrides: list[dict[str, str]]) -> list[dict[str, Any]]:
    node_map = {str(node.get("id", "")).strip(): node for node in nodes if str(node.get("id", "")).strip()}
    findings: list[dict[str, Any]] = []
    for override in overrides:
        node_id = str(override.get("id", "")).strip()
        node = node_map.get(node_id)
        if not node:
            continue
        node_updated = _parse_utc_timestamp(str(node.get("updated", "")))
        summary_updated = _parse_utc_timestamp(str(override.get("summary_updated", "")))
        if node_updated is None or summary_updated is None:
            continue
        if node_updated > summary_updated:
            findings.append(
                {
                    "severity": "warning",
                    "path": node_id,
                    "message": "agent summary override is older than the indexed source update",
                    "node_updated": str(node.get("updated", "")),
                    "summary_updated": str(override.get("summary_updated", "")),
                }
            )
    return findings


def _json_sync_findings(
    metadata: dict[str, str],
    json_index_path: Path | None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    db_generated = str(metadata.get("generated", "")).strip()
    db_manifest_raw = metadata.get("scan_manifest")
    db_manifest_present = isinstance(db_manifest_raw, str) and bool(db_manifest_raw.strip())
    db_manifest: dict[str, Any] | None = None
    if db_manifest_present:
        try:
            decoded = json.loads(str(db_manifest_raw))
            if isinstance(decoded, dict):
                db_manifest = decoded
            else:
                raise ValueError("manifest is not an object")
        except (json.JSONDecodeError, ValueError):
            findings.append(
                {
                    "severity": "warning",
                    "path": str(db_path or ""),
                    "message": "SQLite scan manifest is invalid; run mdex scan",
                }
            )

    if json_index_path is None:
        if db_manifest_present:
            findings.append(
                {
                    "severity": "warning",
                    "path": str(db_path or ""),
                    "message": "manifest JSON output path is unavailable or unsafe; run mdex scan",
                }
            )
        return findings
    if not json_index_path.exists():
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "scan JSON output is missing; run mdex scan to refresh generated artifacts",
            }
        )
        return findings

    if json_index_path.is_symlink() or not json_index_path.is_file():
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "scan JSON output is not a safe regular file",
            }
        )
        return findings
    if json_index_path.stat().st_size > 256 * 1024 * 1024:
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "scan JSON output exceeds the 256 MiB doctor safety limit",
            }
        )
        return findings

    try:
        loaded = json.loads(json_index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": f"scan JSON output could not be read: {exc}",
            }
        )
        return findings

    json_generated = str(loaded.get("generated", "") if isinstance(loaded, dict) else "").strip()
    if db_generated and json_generated and db_generated != json_generated:
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "scan JSON and SQLite generated timestamps differ; run mdex scan",
            }
        )
    json_manifest_declared = isinstance(loaded, dict) and "scan_manifest" in loaded
    json_manifest_raw = loaded.get("scan_manifest") if isinstance(loaded, dict) else None
    json_manifest = json_manifest_raw if isinstance(json_manifest_raw, dict) else None
    if json_manifest_declared and json_manifest is None:
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "JSON scan manifest is invalid; run mdex scan",
            }
        )
    if db_manifest_present != json_manifest_declared:
        findings.append(
            {
                "severity": "warning",
                "path": str(json_index_path),
                "message": "scan manifest is missing from one generated output; run mdex scan",
            }
        )
        return findings
    if (db_manifest_present and db_manifest is None) or (json_manifest_declared and json_manifest is None):
        return findings
    if db_manifest is not None and json_manifest is not None:
        db_scan_id = str(db_manifest.get("scan_id", ""))
        json_scan_id = str(json_manifest.get("scan_id", ""))
        if not db_scan_id or not json_scan_id or db_scan_id != json_scan_id:
            findings.append(
                {
                    "severity": "warning",
                    "path": str(json_index_path),
                    "message": "scan JSON and SQLite manifest generations differ; run mdex scan",
                }
            )

        expected_db = db_path.resolve() if db_path is not None else None
        expected_json = json_index_path.resolve()
        for label, manifest in (("SQLite", db_manifest), ("JSON", json_manifest)):
            output = manifest.get("output")
            pair_matches = isinstance(output, dict)
            if pair_matches:
                try:
                    pair_matches = (
                        expected_db is not None
                        and canonical_path_key(str(output.get("db", "")))
                        == canonical_path_key(expected_db)
                        and canonical_path_key(str(output.get("json", "")))
                        == canonical_path_key(expected_json)
                    )
                except (OSError, RuntimeError, ValueError):
                    pair_matches = False
            if not pair_matches:
                findings.append(
                    {
                        "severity": "warning",
                        "path": str(json_index_path),
                        "message": f"{label} scan manifest names a different output pair; run mdex scan",
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


def _unresolved_link_findings(db_path: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in list_missing_links(db_path, limit=10):
        target = str(row.get("id", "")).strip()
        referenced_by = [str(item) for item in row.get("referenced_by", []) if str(item).strip()]
        findings.append(
            {
                "severity": "info",
                "path": target,
                "message": "unresolved links_to target is referenced by indexed nodes",
                "count": int(row.get("count", 0) or 0),
                "referenced_by": referenced_by,
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


def _parse_utc_timestamp(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def index_freshness_status(
    db_path: str,
    *,
    alias: str = "repo",
    source: str = "unknown",
    config_path: str = "",
    stale_after_hours: int = 24,
) -> dict[str, Any]:
    health = evaluate_index_health(
        db_path,
        alias=alias,
        source=source,
        stale_after_hours=stale_after_hours,
    )
    return {
        "alias": alias,
        "db": str(Path(db_path)),
        "source": source,
        "config_path": config_path,
        **index_status_from_health(health),
    }


def build_doctor_report(
    db_path: str,
    *,
    repo_root: Path | None = None,
    json_index_path: Path | None = None,
    config_path: Path | None = None,
    db_source: str = "unknown",
    alias: str = "repo",
    borrowed: bool = False,
    health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = list_nodes(db_path)
    metadata = list_index_metadata(db_path)
    override_ids = list_node_override_ids(db_path)
    overrides = list_node_overrides(db_path)
    db_path_obj = Path(db_path)
    node_id_root: Path | None = None
    try:
        raw_manifest = json.loads(str(metadata.get("scan_manifest", "") or ""))
    except json.JSONDecodeError:
        raw_manifest = None
    if isinstance(raw_manifest, dict) and str(raw_manifest.get("node_id_root", "")).strip():
        node_id_root = Path(str(raw_manifest["node_id_root"]))
    elif str(metadata.get("scan_root", "")).strip():
        node_id_root = Path(str(metadata["scan_root"]))
    policy, exclude_patterns = _load_doctor_policy(metadata, config_path)
    text_findings, node_budget_findings, total_budget_findings = _surface_budget_findings(
        nodes, policy, exclude_patterns, repo_root
    )

    checks = [
        _check_result("scan_warnings", _scan_warning_findings(metadata)),
        _check_result("indexed_path_hygiene", _indexed_path_findings(nodes)),
        _check_result(
            "indexed_untracked_files",
            _untracked_file_findings(
                nodes, repo_root, node_id_root, policy, exclude_patterns
            ),
        ),
        _check_result(
            "generated_paths",
            _generated_path_findings(nodes, policy, exclude_patterns),
        ),
        _check_result("oversized_text_documents", text_findings),
        _check_result("single_node_token_budget", node_budget_findings),
        _check_result("index_surface_budget", total_budget_findings),
        _check_result("orphan_overrides", _override_findings(nodes, override_ids)),
        _check_result("override_freshness", _override_freshness_findings(nodes, overrides)),
        _check_result(
            "json_sqlite_sync",
            _json_sync_findings(metadata, json_index_path, db_path_obj),
        ),
        _check_result("legacy_artifacts", _legacy_artifact_findings(repo_root, db_path_obj)),
        _check_result("unresolved_links", _unresolved_link_findings(db_path)),
        _check_result("telemetry_health", telemetry_health_findings(repo_root)),
    ]
    summary = _summary(checks)
    status = _overall_status(checks)
    official_health = health or evaluate_index_health(
        db_path,
        alias=alias,
        source=db_source,
        borrowed=borrowed,
    )
    freshness = {
        "alias": alias,
        "db": str(db_path_obj),
        "source": db_source,
        "config_path": str(config_path) if config_path is not None else "",
        **index_status_from_health(official_health),
    }
    return {
        "status": status,
        "summary": summary,
        "health": official_health,
        "index_health": freshness,
        "config": {
            "repo_root": str(repo_root) if repo_root is not None else "",
            "config_path": str(config_path) if config_path is not None else "",
            "json_index_path": str(json_index_path) if json_index_path is not None else "",
            "doctor_policy": policy,
            "exclude_patterns": exclude_patterns,
        },
        "checks": checks,
        "recommended_next_actions": _recommended_next_actions(summary),
    }
