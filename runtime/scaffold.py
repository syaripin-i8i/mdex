from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.dbresolve import RuntimeContext, resolve_decision_dir, resolve_task_dir
from runtime.reader import NodePathError, validate_node_id
from runtime.store import get_node, get_scan_root

FRONTMATTER_BOUNDARY = "---"
UPDATED_RE = re.compile(r"^updated\s*:\s*.+$", re.IGNORECASE)
SLUG_RE = re.compile(r"[^a-z0-9]+")


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _project_name(context: RuntimeContext) -> str:
    raw = context.config.get("project")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return context.repo_root.name.strip() or "unknown"


def _relative_to_repo(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _task_template(title: str, project: str) -> str:
    today = _utc_today()
    return (
        "---\n"
        "type: task\n"
        f"project: {project}\n"
        "status: pending\n"
        f"updated: {today}\n"
        "---\n\n"
        f"# Task: {title}\n\n"
        f"{title} の実装タスク。\n\n"
        "## 実施内容\n\n"
        "## 結果\n\n"
        "## 残課題\n"
    )


def _slugify(title: str) -> str:
    lowered = title.strip().lower()
    slug = SLUG_RE.sub("-", lowered).strip("-")
    if not slug:
        slug = "decision"
    return slug


def _decision_template(title: str, project: str) -> str:
    today = _utc_today()
    return (
        "---\n"
        "type: decision\n"
        f"project: {project}\n"
        "status: active\n"
        f"updated: {today}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{title} に関する決定記録。\n\n"
        "## 決定内容\n\n"
        "## 理由\n\n"
        "## 却下した代替案\n\n"
        "## 影響範囲\n"
    )


def create_task_file(context: RuntimeContext, title: str) -> dict[str, Any]:
    task_dir = resolve_task_dir(context)
    task_dir.mkdir(parents=True, exist_ok=True)
    filename = f"T{_utc_stamp()}.md"
    output = (task_dir / filename).resolve()
    output.write_text(_task_template(title, _project_name(context)), encoding="utf-8")
    return {
        "status": "created",
        "kind": "task",
        "title": title,
        "path": output.as_posix(),
        "node_id": _relative_to_repo(output, context.repo_root),
    }


def create_decision_file(context: RuntimeContext, title: str) -> dict[str, Any]:
    decision_dir = resolve_decision_dir(context)
    decision_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify(title)}.md"
    output = (decision_dir / filename).resolve()
    counter = 2
    while output.exists():
        output = (decision_dir / f"{_slugify(title)}-{counter}.md").resolve()
        counter += 1
    output.write_text(_decision_template(title, _project_name(context)), encoding="utf-8")
    return {
        "status": "created",
        "kind": "decision",
        "title": title,
        "path": output.as_posix(),
        "node_id": _relative_to_repo(output, context.repo_root),
    }


def _update_frontmatter_updated(text: str, today: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == FRONTMATTER_BOUNDARY:
        end_idx = None
        for index in range(1, len(lines)):
            if lines[index].strip() == FRONTMATTER_BOUNDARY:
                end_idx = index
                break
        if end_idx is not None:
            replaced = False
            for idx in range(1, end_idx):
                if UPDATED_RE.match(lines[idx].strip()):
                    lines[idx] = f"updated: {today}"
                    replaced = True
                    break
            if not replaced:
                lines.insert(end_idx, f"updated: {today}")
            return "\n".join(lines).rstrip() + "\n"

    body = text.rstrip()
    if body:
        body = "\n\n" + body
    return f"---\nupdated: {today}\n---{body}\n"


class StampTargetError(ValueError):
    def __init__(
        self,
        *,
        error: str,
        detail: str,
        target: str,
        node_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.error = error
        self.detail = detail
        self.target = target
        self.node_id = node_id


def _resolve_stamp_path(target: str, db_path: str | None) -> tuple[Path, str]:
    if not db_path:
        raise StampTargetError(
            error="db required",
            detail="stamp requires an index database",
            target=target,
        )

    normalized = str(target or "").strip().replace("\\", "/")
    try:
        relative_node = validate_node_id(normalized)
    except NodePathError as exc:
        raise StampTargetError(
            error=exc.error,
            detail=exc.detail,
            target=target,
            node_id=exc.node_id,
        ) from exc

    if get_node(db_path, normalized) is None:
        raise StampTargetError(
            error="node not indexed",
            detail="stamp accepts only indexed node ids",
            target=target,
            node_id=normalized,
        )

    scan_root = Path(get_scan_root(db_path, default=".")).resolve()
    node_path = (scan_root / relative_node).resolve()
    try:
        node_path.relative_to(scan_root)
    except ValueError as exc:
        raise StampTargetError(
            error="path containment violation",
            detail="resolved path escapes scan_root",
            target=target,
            node_id=normalized,
        ) from exc

    if not node_path.exists() or not node_path.is_file():
        raise StampTargetError(
            error="node file not found",
            detail="indexed node does not resolve to an existing file under scan_root",
            target=target,
            node_id=normalized,
        )
    return node_path, normalized


def stamp_updated(target: str, *, db_path: str | None = None) -> dict[str, Any]:
    try:
        resolved, node_id = _resolve_stamp_path(target, db_path)
    except StampTargetError as exc:
        payload: dict[str, Any] = {
            "status": "error",
            "error": exc.error,
            "detail": exc.detail,
            "target": exc.target,
        }
        if exc.node_id:
            payload["node_id"] = exc.node_id
        return payload

    today = _utc_today()
    original = resolved.read_text(encoding="utf-8")
    updated = _update_frontmatter_updated(original, today)
    resolved.write_text(updated, encoding="utf-8")
    return {
        "status": "stamped",
        "node_id": node_id,
        "path": resolved.as_posix(),
        "updated": today,
    }
