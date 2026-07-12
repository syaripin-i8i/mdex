from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mdex.path_identity import canonical_path_key, deduplicate_directory_paths

DEFAULT_CONFIG_RELATIVE = ".mdex/config.json"
DEFAULT_DB_RELATIVE = ".mdex/mdex_index.db"
FALLBACK_DB_RELATIVE = "mdex_index.db"
DEFAULT_SCAN_ROOT = "."
DEFAULT_SCAN_CONFIG = "control/scan_config.json"
DEFAULT_TASK_DIR = "tasks"
DEFAULT_DECISION_DIR = "decision"
WORKTREE_COMMON_ROOT_SOURCE = "worktree_common_root"


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


def _worktree_gitdir(repo_root: Path) -> Path | None:
    git_marker = repo_root / ".git"
    if not git_marker.is_file():
        return None
    try:
        marker_text = git_marker.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in marker_text.splitlines():
        if not line.startswith("gitdir:"):
            continue
        value = line[len("gitdir:") :].strip()
        if not value:
            return None
        gitdir = Path(value)
        if not gitdir.is_absolute():
            gitdir = repo_root / gitdir
        return gitdir.resolve()
    return None


def worktree_common_root(repo_root: Path) -> Path | None:
    """Return the main checkout root when repo_root is a linked git worktree.

    A linked worktree marks its root with a `.git` FILE pointing at
    `<main>/.git/worktrees/<name>`. The common git dir is resolved from that
    gitdir's `commondir` file (git's own indirection, written for every linked
    worktree). Submodules (`.git/modules/...`, no commondir), bare
    repositories, and stale markers whose gitdir no longer exists resolve to
    None so resolution stays fail-closed instead of guessing a main checkout.
    """
    gitdir = _worktree_gitdir(repo_root)
    if gitdir is None:
        return None

    commondir_file = gitdir / "commondir"
    if not commondir_file.is_file():
        return None
    try:
        raw_common = commondir_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not raw_common:
        return None
    common_path = Path(raw_common)
    if not common_path.is_absolute():
        common_path = gitdir / common_path
    common_dir = common_path.resolve()

    if common_dir.name != ".git" or not common_dir.is_dir():
        return None
    main_root = common_dir.parent
    if canonical_path_key(main_root) == canonical_path_key(repo_root):
        return None
    return main_root


def worktree_mirror_candidate(main_root: Path, value: str, *, key: str) -> Path | None:
    """Mirror a repo-relative candidate under the main checkout root.

    Absolute values are never re-anchored, and a mirror escaping the main
    checkout (e.g. through a symlinked `.mdex`) returns None so the caller
    skips the candidate instead of aborting resolution.
    """
    if Path(value).is_absolute():
        return None
    try:
        return _as_path(main_root, value, key=key)
    except ValueError:
        return None


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
    roots, _ = resolve_scan_roots(context)
    return roots[0]


def _raw_scan_root_values(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    raw_roots = config.get("scan_roots")
    has_scan_roots = isinstance(raw_roots, list)
    values: list[str] = []

    if has_scan_roots:
        for item in raw_roots:
            text = str(item).strip()
            if text:
                values.append(text)

    raw_single = config.get("scan_root")
    if isinstance(raw_single, str) and raw_single.strip():
        if has_scan_roots:
            warnings.append("scan_root is deprecated when scan_roots is present; scan_roots takes precedence")
        elif not values:
            values.append(raw_single.strip())
    return values, warnings


def resolve_scan_roots(
    context: RuntimeContext,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[Path], list[str]]:
    source = config if config is not None else context.config
    values, warnings = _raw_scan_root_values(source)
    if not values:
        values = [DEFAULT_SCAN_ROOT]

    resolved = deduplicate_directory_paths(
        _as_path(context.repo_root, value, key="scan_roots") for value in values
    )

    if not resolved:
        resolved = [_as_path(context.repo_root, DEFAULT_SCAN_ROOT, key="scan_roots")]
    return resolved, warnings


def resolve_scan_config_path(context: RuntimeContext) -> Path:
    return resolve_config_path(context, "scan_config", default_relative=DEFAULT_SCAN_CONFIG)


def _candidate_rows(
    context: RuntimeContext,
    explicit_db: str | None,
    *,
    main_root: Path | None,
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

    local_rows: list[tuple[str, str]] = []
    config_db = context.config.get("db")
    if isinstance(config_db, str) and config_db.strip():
        local_rows.append(("config", config_db.strip()))
    local_rows.append(("repo_default", DEFAULT_DB_RELATIVE))
    local_rows.append(("repo_default", FALLBACK_DB_RELATIVE))
    for source, value in local_rows:
        candidates.append((source, _as_path(context.repo_root, value, key="db")))

    if main_root is not None:
        seen_mirrors: set[str] = set()
        for _, value in local_rows:
            mirrored = worktree_mirror_candidate(main_root, value, key="db")
            if mirrored is None:
                continue
            mirror_key = canonical_path_key(mirrored)
            if mirror_key in seen_mirrors:
                continue
            seen_mirrors.add(mirror_key)
            candidates.append((WORKTREE_COMMON_ROOT_SOURCE, mirrored))
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


def _ensure_generated_db_path(repo_root: Path, candidate: Path, *, key: str) -> Path:
    resolved = _ensure_within_repo(repo_root, candidate, key=key)
    generated_root = (repo_root.resolve() / ".mdex").resolve()
    try:
        relative = resolved.relative_to(generated_root)
    except ValueError as exc:
        if resolved.exists():
            raise ValueError(
                f"{key} must not overwrite existing repository file outside .mdex: {_to_display_path(resolved)}"
            ) from exc
        raise ValueError(
            f"{key} must stay within the repo .mdex directory: {_to_display_path(resolved)}"
        ) from exc
    if not relative.parts:
        raise ValueError(f"{key} must name a file within the repo .mdex directory")
    return resolved


def resolve_db_path(
    explicit_db: str | None,
    *,
    cwd: str | Path | None = None,
    must_exist: bool = True,
    allow_worktree_fallback: bool = True,
) -> dict[str, Any]:
    # The worktree fallback is read-only: it never applies when the resolved
    # path may be created (must_exist=False) or when the caller intends to
    # write (allow_worktree_fallback=False), so a worktree cannot target the
    # main checkout's db.
    context = load_runtime_context(cwd)
    main_root = (
        worktree_common_root(context.repo_root)
        if must_exist and allow_worktree_fallback
        else None
    )
    candidates = _candidate_rows(context, explicit_db, main_root=main_root)
    attempts: list[dict[str, Any]] = []

    def _resolved(source: str, candidate: Path) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": _to_display_path(candidate),
            "source": source,
            "repo_root": _to_display_path(context.repo_root),
            "config_path": _to_display_path(context.config_path),
            "config": context.config,
            "resolution_attempts": attempts,
        }
        if main_root is not None:
            # Surfaced so downstream resolution (multi-index aliases, manifest
            # JSON confinement) can reuse the same borrow root without
            # re-deriving it from the git worktree marker.
            payload["worktree_common_root"] = _to_display_path(main_root)
        return payload

    for source, candidate in candidates:
        _append_attempt(attempts, source, candidate)

        if must_exist:
            if candidate.exists():
                return _resolved(source, candidate)
            continue

        if candidate.exists():
            return _resolved(source, candidate)

        if source in {"config", "repo_default"}:
            candidate = _ensure_generated_db_path(context.repo_root, candidate, key="db")

        if _ensure_parent(candidate):
            return _resolved(source, candidate)

    hint_db = _to_display_path(_as_path(context.repo_root, DEFAULT_DB_RELATIVE, key="db"))
    payload = {
        "error": "db not found",
        "resolution_attempts": attempts,
        "hint": f"run mdex scan --root {_to_display_path(context.repo_root)} --db {hint_db}",
    }
    raise DbResolutionError(payload)
