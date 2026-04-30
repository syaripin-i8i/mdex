from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mdex.dbresolve import detect_repo_root
from mdex import __version__

DEFAULT_TELEMETRY_RELATIVE = ".mdex/telemetry.jsonl"
TELEMETRY_SCHEMA = "https://github.com/syaripin-i8i/mdex/schemas/telemetry_event.schema.json"

TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_runtime_config(repo_root: Path) -> dict[str, Any]:
    config_path = repo_root / ".mdex" / "config.json"
    if not config_path.exists():
        return {}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def telemetry_enabled(cwd: Path | None = None) -> bool:
    root = detect_repo_root(cwd or Path.cwd())
    raw = os.environ.get("MDEX_TELEMETRY", "").strip().lower()
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    return bool(_read_runtime_config(root).get("telemetry", False))


def telemetry_log_path(cwd: Path | None = None) -> Path:
    return (detect_repo_root(cwd or Path.cwd()) / DEFAULT_TELEMETRY_RELATIVE).resolve()


def _safe_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _suggested_rg_count(payload: dict[str, Any]) -> int:
    digest = payload.get("actionable_digest")
    if not isinstance(digest, dict):
        return 0
    rows = digest.get("suggested_rg")
    if not isinstance(rows, list):
        return 0
    return sum(1 for row in rows if isinstance(row, dict))


def _result_size(command: str, payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    if command == "scan":
        return int(payload.get("nodes", 0) or 0)
    for key in ("nodes", "inputs", "checks", "changed_files", "related", "prerequisites"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _summarize_dict(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}

    if command == "scan":
        edges = payload.get("edges", {}) if isinstance(payload.get("edges"), dict) else {}
        summary.update(
            {
                "nodes": int(payload.get("nodes", 0) or 0),
                "edges_total": int(edges.get("total", 0) or 0),
                "edges_unresolved": int(edges.get("unresolved", 0) or 0),
                "warnings": _safe_len(payload.get("warnings")),
            }
        )
    elif command in {"start", "context"}:
        suggested_count = _suggested_rg_count(payload)
        summary.update(
            {
                "confidence": float(payload.get("confidence", 0.0) or 0.0)
                if "confidence" in payload
                else None,
                "low_confidence": float(payload.get("confidence", 1.0) or 0.0) < 0.6
                if "confidence" in payload
                else None,
                "nodes": _safe_len(payload.get("nodes")),
                "total_tokens": int(payload.get("total_tokens", 0) or 0),
                "budget": int(payload.get("budget", 0) or 0),
                "recommended_read_order": _safe_len(payload.get("recommended_read_order")),
                "recommended_next_actions_v2": _safe_len(payload.get("recommended_next_actions_v2")),
                "suggested_rg_count": suggested_count,
            }
        )
        if command == "start":
            index_status = payload.get("index_status", {})
            if isinstance(index_status, dict):
                summary["index_fresh"] = bool(index_status.get("fresh", False))
                summary["entrypoint_reason"] = str(payload.get("entrypoint_reason", ""))
    elif command == "doctor":
        summary["status"] = str(payload.get("status", ""))
        doctor_summary = payload.get("summary")
        if isinstance(doctor_summary, dict):
            summary["findings"] = dict(doctor_summary)
    elif command == "impact":
        summary.update(
            {
                "inputs": _safe_len(payload.get("inputs")),
                "read_first": _safe_len(payload.get("read_first")),
                "related_tasks": _safe_len(payload.get("related_tasks")),
                "decision_records": _safe_len(payload.get("decision_records")),
                "stale_watch": _safe_len(payload.get("stale_watch")),
            }
        )
    elif command == "finish":
        summary.update(
            {
                "noop": bool(payload.get("noop", False)),
                "noop_reason": str(payload.get("noop_reason", "")),
                "dry_run": bool(payload.get("dry_run", False)),
                "changed_files": _safe_len(payload.get("changed_files")),
                "enrich_candidates": _safe_len(payload.get("enrich_candidates")),
                "applied_enrichments": _safe_len(payload.get("applied_enrichments")),
                "requires_manual_targeting": bool(payload.get("requires_manual_targeting", False)),
            }
        )
    elif "error" in payload:
        summary.update(
            {
                "error": str(payload.get("error", "")),
                "error_code": str(payload.get("code", "")),
            }
        )
    elif isinstance(payload.get("status"), str):
        summary["status"] = str(payload.get("status", ""))

    return {key: value for key, value in summary.items() if value is not None}


def summarize_payload(command: str, payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return _summarize_dict(command, payload)
    if isinstance(payload, list):
        return {"items": len(payload)}
    return {}


def _argv_shape(argv: list[str]) -> dict[str, Any]:
    flags: list[str] = []
    positional_count = 0
    for item in argv[1:]:
        text = str(item).strip()
        if not text:
            continue
        if text.startswith("-"):
            flags.append(text.split("=", 1)[0])
        else:
            positional_count += 1
    return {
        "flags": sorted(set(flags)),
        "positional_count": positional_count,
    }


def _git_tracked(repo_root: Path, path: Path) -> bool:
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return False
    try:
        relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return False
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return result.returncode == 0


def telemetry_health_findings(repo_root: Path | None) -> list[dict[str, Any]]:
    if repo_root is None:
        return []
    root = repo_root.resolve()
    if not telemetry_enabled(root):
        return []

    path = telemetry_log_path(root)
    findings: list[dict[str, Any]] = []
    parent = path.parent
    if parent.exists() and not os.access(parent, os.W_OK):
        findings.append(
            {
                "severity": "error",
                "path": str(parent),
                "message": "telemetry is enabled but the telemetry directory is not writable",
            }
        )
    if _git_tracked(root, path):
        findings.append(
            {
                "severity": "error",
                "path": path.relative_to(root).as_posix(),
                "message": "telemetry log is tracked by git; remove it from version control",
            }
        )
    return findings


def record_command_event(
    *,
    command: str,
    argv: list[str],
    exit_code: int,
    duration_ms: int,
    payload: Any,
    stream: str | None,
    cwd: Path | None = None,
) -> None:
    cwd_path = (cwd or Path.cwd()).resolve()
    if not telemetry_enabled(cwd_path):
        return
    path = telemetry_log_path(cwd_path)

    try:
        summary = summarize_payload(command, payload)
        event: dict[str, Any] = {
            "event": "command_completed",
            "event_schema": TELEMETRY_SCHEMA,
            "timestamp": _now_utc(),
            "command": command,
            "args": _argv_shape(argv),
            "exit_code": int(exit_code),
            "duration_ms": int(duration_ms),
            "stream": stream or "none",
            "result_size": _result_size(command, payload),
            "contract_version": __version__,
            "summary": summary,
        }
        if isinstance(payload, dict):
            if "confidence" in payload:
                event["confidence"] = float(payload.get("confidence", 0.0) or 0.0)
            if "actionable_digest" in payload:
                event["suggested_rg_count"] = _suggested_rg_count(payload)
            for key in ("code",):
                if key in payload:
                    event[key] = payload[key]

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        return
