from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_RELATIVE = ".mdex/config.json"
DEFAULT_DB_RELATIVE = ".mdex/mdex_index.db"
FALLBACK_DB_RELATIVE = "mdex_index.db"
DEFAULT_SCAN_ROOT = "."
DEFAULT_SCAN_CONFIG = "control/scan_config.json"
DEFAULT_TASK_DIR = "tasks/pending"
DEFAULT_DECISION_DIR = "decision"


@dataclass(frozen=True)
class RuntimeContext:
    repo_root: Path
    config_path: Path
    config: dict[str, Any]


class DbResolutionError(RuntimeError):
    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("error", "db resolution failed")))
        self.payload = payload


def _to_display_path(path: Path) -> str:
    return path.resolve().as_posix()


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"config root must be object: {_to_display_path(path)}")
    return loaded


def _ensure_within_repo(repo_root: Path, candidate: Path, *, key: str) -> Path:
    repo_resolved = repo_root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repo_resolved)
    except ValueError as exc:
        raise ValueError(
            f"{key} must stay within repo: {_to_display_path(resolved)}"
        ) from exc
    return resolved


def _walk_parents(start: Path) -> list[Path]:
    current = start.resolve()
    parents = [current]
    parents.extend(current.parents)
    return parents


def detect_repo_root(start_dir: str | Path | None = None) -> Path:
    origin = Path(start_dir or Path.cwd()).resolve()
    parents = _walk_parents(origin)

    for candidate in parents:
        config_path = candidate / DEFAULT_CONFIG_RELATIVE
        if config_path.exists():
            return candidate

    for candidate in parents:
        git_dir = candidate / ".git"
        if git_dir.exists():
            return candidate

    return origin


def load_runtime_context(start_dir: str | Path | None = None) -> RuntimeContext:
    repo_root = detect_repo_root(start_dir)
    config_path = (repo_root / DEFAULT_CONFIG_RELATIVE).resolve()
    config = _read_config(config_path)
    return RuntimeContext(repo_root=repo_root, config_path=config_path, config=config)


def _as_path(
    repo_root: Path,
    value: str,
    *,
    key: str,
    allow_outside_repo: bool = False,
) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (repo_root / candidate).resolve()
    if allow_outside_repo:
        return resolved
    return _ensure_within_repo(repo_root, resolved, key=key)


def resolve_config_path(
    context: RuntimeContext,
    key: str,
    *,
    default_relative: str,
) -> Path:
    raw_value = context.config.get(key)
    if isinstance(raw_value, str) and raw_value.strip():
        return _as_path(context.repo_root, raw_value.strip(), key=key)
    return _as_path(context.repo_root, default_relative, key=key)


def resolve_task_dir(context: RuntimeContext) -> Path:
    return resolve_config_path(context, "task_dir", default_relative=DEFAULT_TASK_DIR)


def resolve_decision_dir(context: RuntimeContext) -> Path:
    raw_value = context.config.get("decision_dir")
    if isinstance(raw_value, str) and raw_value.strip():
        return _as_path(context.repo_root, raw_value.strip(), key="decision_dir")

    preferred = _as_path(context.repo_root, "decision", key="decision_dir")
    if preferred.exists():
        return preferred
    alternative = _as_path(context.repo_root, "decisions", key="decision_dir")
    if alternative.exists():
        return alternative
    return preferred


def resolve_scan_root(context: RuntimeContext) -> Path:
    return resolve_config_path(context, "scan_root", default_relative=DEFAULT_SCAN_ROOT)


def resolve_scan_config_path(context: RuntimeContext) -> Path:
    return resolve_config_path(context, "scan_config", default_relative=DEFAULT_SCAN_CONFIG)


def _candidate_rows(
    context: RuntimeContext,
    explicit_db: str | None,
) -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []

    if explicit_db is not None and explicit_db.strip():
        candidates.append(
            (
                "arg",
                _as_path(
                    context.repo_root,
                    explicit_db.strip(),
                    key="db",
                    allow_outside_repo=True,
                ),
            )
        )
        return candidates

    env_db = os.environ.get("MDEX_DB", "").strip()
    if env_db:
        candidates.append(
            (
                "env",
                _as_path(
                    context.repo_root,
                    env_db,
                    key="db",
                    allow_outside_repo=True,
                ),
            )
        )

    config_db = context.config.get("db")
    if isinstance(config_db, str) and config_db.strip():
        candidates.append(("config", _as_path(context.repo_root, config_db.strip(), key="db")))

    candidates.append(("repo_default", _as_path(context.repo_root, DEFAULT_DB_RELATIVE, key="db")))
    candidates.append(("repo_default", _as_path(context.repo_root, FALLBACK_DB_RELATIVE, key="db")))
    return candidates


def _append_attempt(attempts: list[dict[str, Any]], source: str, path: Path) -> None:
    attempts.append(
        {
            "source": source,
            "path": _to_display_path(path),
            "exists": path.exists(),
        }
    )


def _ensure_parent(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    return True


def resolve_db_path(
    explicit_db: str | None,
    *,
    cwd: str | Path | None = None,
    must_exist: bool = True,
) -> dict[str, Any]:
    context = load_runtime_context(cwd)
    candidates = _candidate_rows(context, explicit_db)
    attempts: list[dict[str, Any]] = []

    for source, candidate in candidates:
        _append_attempt(attempts, source, candidate)

        if must_exist:
            if candidate.exists():
                return {
                    "path": _to_display_path(candidate),
                    "source": source,
                    "repo_root": _to_display_path(context.repo_root),
                    "config_path": _to_display_path(context.config_path),
                    "config": context.config,
                    "resolution_attempts": attempts,
                }
            continue

        if candidate.exists():
            return {
                "path": _to_display_path(candidate),
                "source": source,
                "repo_root": _to_display_path(context.repo_root),
                "config_path": _to_display_path(context.config_path),
                "config": context.config,
                "resolution_attempts": attempts,
            }

        if _ensure_parent(candidate):
            return {
                "path": _to_display_path(candidate),
                "source": source,
                "repo_root": _to_display_path(context.repo_root),
                "config_path": _to_display_path(context.config_path),
                "config": context.config,
                "resolution_attempts": attempts,
            }

    hint_db = _to_display_path(_as_path(context.repo_root, DEFAULT_DB_RELATIVE, key="db"))
    payload = {
        "error": "db not found",
        "resolution_attempts": attempts,
        "hint": f"run mdex scan --root {_to_display_path(context.repo_root)} --db {hint_db}",
    }
    raise DbResolutionError(payload)
