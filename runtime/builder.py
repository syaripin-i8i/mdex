from __future__ import annotations

import fnmatch
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .parser import parse_file
except ImportError:
    from parser import parse_file  # type: ignore


URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://")


def _to_posix(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _node_id_for_path(file_path: Path, root_path: Path) -> str:
    cwd = Path.cwd().resolve()
    resolved = file_path.resolve()
    try:
        return _to_posix(str(resolved.relative_to(cwd)))
    except ValueError:
        try:
            return _to_posix(str(resolved.relative_to(root_path.resolve())))
        except ValueError:
            return _to_posix(str(resolved))


def _is_excluded(file_path: Path, root_path: Path, patterns: list[str]) -> bool:
    if not patterns:
        return False
    rel = _to_posix(str(file_path.resolve().relative_to(root_path.resolve())))
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def _iter_markdown_files(root_path: Path, exclude_patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for file_path in root_path.rglob("*.md"):
        if file_path.is_file() and not _is_excluded(file_path, root_path, exclude_patterns):
            files.append(file_path.resolve())
    return sorted(files)


def _normalize_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _resolve_type(
    file_id: str,
    frontmatter: dict[str, Any],
    node_type_map: dict[str, list[str]],
    title: str,
) -> str:
    fm_type = frontmatter.get("type")
    if isinstance(fm_type, str) and fm_type.strip():
        return fm_type.strip().lower()

    if title.strip().lower().startswith("task:"):
        return "task"

    dir_parts = [part.lower() for part in Path(file_id).parts[:-1]]
    for node_type, aliases in node_type_map.items():
        alias_set = {alias.lower() for alias in aliases}
        if any(part in alias_set for part in dir_parts):
            return node_type
    return "unknown"


def _to_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_wikilink_target(raw: str) -> str:
    clean = raw.split("|", 1)[0].split("#", 1)[0].strip()
    if not clean:
        return ""
    if not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    return clean


def _clean_frontmatter_target(raw: str) -> str:
    clean = raw.strip()
    if clean.startswith("[[") and clean.endswith("]]"):
        clean = clean[2:-2]
    clean = clean.split("|", 1)[0].split("#", 1)[0].strip()
    if clean and not clean.lower().endswith(".md"):
        clean = f"{clean}.md"
    return clean


def _clean_md_link_target(raw: str) -> str:
    clean = raw.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean:
        return ""
    if URL_SCHEME_RE.match(clean):
        return ""
    if clean.lower().endswith(".md"):
        return clean
    return ""


def _resolve_target_id(
    raw_target: str,
    source_file: Path,
    root_path: Path,
    path_to_id: dict[Path, str],
    stem_to_ids: dict[str, list[str]],
) -> str:
    target = raw_target.strip()
    if not target:
        return ""

    source_dir = source_file.parent.resolve()
    target_path = Path(target)
    if target_path.is_absolute():
        absolute_target = target_path.resolve()
    else:
        absolute_target = (source_dir / target).resolve()

    if absolute_target in path_to_id:
        return path_to_id[absolute_target]

    target_name = target_path.name
    stem = Path(target_name).stem.lower()
    if stem in stem_to_ids and len(stem_to_ids[stem]) == 1:
        return stem_to_ids[stem][0]

    if target_path.is_absolute():
        return _node_id_for_path(absolute_target, root_path)

    return _to_posix(target_name or target)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _resolve_status(file_path: Path, frontmatter: dict[str, Any]) -> str:
    raw_status = frontmatter.get("status")
    status = str(raw_status).strip().lower() if raw_status is not None and str(raw_status).strip() else "unknown"

    parent_names = {part.lower() for part in file_path.parts}
    is_done_dir = "done" in parent_names
    is_pending_dir = "pending" in parent_names

    if is_done_dir and status in {"unknown", "pending"}:
        return "done"
    if is_pending_dir and status == "unknown":
        return "pending"
    return status


def build_index(root: str, config: dict[str, Any]) -> dict[str, Any]:
    root_path = Path(root).resolve()
    exclude_patterns = _normalize_str_list(config.get("exclude_patterns"))
    node_type_map = config.get("node_type_map") or {}
    if not isinstance(node_type_map, dict):
        node_type_map = {}

    markdown_files = _iter_markdown_files(root_path, exclude_patterns)
    path_to_id = {file_path: _node_id_for_path(file_path, root_path) for file_path in markdown_files}

    stem_to_ids: dict[str, list[str]] = {}
    for node_id in path_to_id.values():
        stem = Path(node_id).stem.lower()
        stem_to_ids.setdefault(stem, []).append(node_id)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    edge_keys = set()

    for file_path in markdown_files:
        parsed = parse_file(str(file_path))
        frontmatter = parsed.get("frontmatter", {}) or {}
        if not isinstance(frontmatter, dict):
            frontmatter = {}

        node_id = path_to_id[file_path]
        title = str(parsed.get("title", ""))
        node_type = _resolve_type(node_id, frontmatter, node_type_map, title)

        project = frontmatter.get("project")
        project_str = str(project).strip() if project is not None and str(project).strip() else "unknown"
        status_str = _resolve_status(file_path, frontmatter)

        wikilink_targets = [
            _clean_wikilink_target(str(item))
            for item in parsed.get("wikilinks", [])
            if str(item).strip()
        ]
        md_link_targets = [
            _clean_md_link_target(str(item))
            for item in parsed.get("md_links", [])
            if str(item).strip()
        ]
        links_to_resolved = _dedupe_keep_order(
            [
                _resolve_target_id(target, file_path, root_path, path_to_id, stem_to_ids)
                for target in wikilink_targets + md_link_targets
                if target
            ]
        )

        depends_raw = [
            _clean_frontmatter_target(item)
            for item in _normalize_str_list(frontmatter.get("depends_on"))
        ]
        relates_raw = [
            _clean_frontmatter_target(item)
            for item in _normalize_str_list(frontmatter.get("relates_to"))
        ]

        depends_resolved = _dedupe_keep_order(
            [
                _resolve_target_id(target, file_path, root_path, path_to_id, stem_to_ids)
                for target in depends_raw
                if target
            ]
        )
        relates_resolved = _dedupe_keep_order(
            [
                _resolve_target_id(target, file_path, root_path, path_to_id, stem_to_ids)
                for target in relates_raw
                if target
            ]
        )

        task_ref_targets = _dedupe_keep_order(
            [
                _resolve_target_id(f"{str(task_id).strip()}.md", file_path, root_path, path_to_id, stem_to_ids)
                for task_id in parsed.get("task_refs", [])
                if str(task_id).strip()
            ]
        )
        relates_resolved = _dedupe_keep_order(relates_resolved + task_ref_targets)

        links_to_resolved = [target for target in links_to_resolved if target and target != node_id]
        depends_resolved = [target for target in depends_resolved if target and target != node_id]
        relates_resolved = [target for target in relates_resolved if target and target != node_id]

        node = {
            "id": node_id,
            "title": title,
            "type": node_type,
            "project": project_str,
            "status": status_str,
            "summary": parsed.get("summary", ""),
            "tags": parsed.get("tags", []),
            "updated": parsed.get("updated", ""),
            "links_to": links_to_resolved,
            "depends_on": depends_resolved,
            "relates_to": relates_resolved,
        }
        nodes.append(node)

        for edge_type, targets in (
            ("links_to", links_to_resolved),
            ("depends_on", depends_resolved),
            ("relates_to", relates_resolved),
        ):
            for target in targets:
                if not target:
                    continue
                edge_key = (node_id, target, edge_type)
                if edge_key in edge_keys:
                    continue
                edge_keys.add(edge_key)
                edges.append({"from": node_id, "to": target, "type": edge_type})

    index = {
        "generated": _to_iso_now(),
        "scan_root": _to_posix(str(root_path)),
        "nodes": nodes,
        "edges": edges,
    }

    return index


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build markdown index.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=False)
    args = parser.parse_args()

    config_path = Path(args.config).resolve() if args.config else Path("control/scan_config.json").resolve()
    config_data: dict[str, Any] = {}
    if config_path.exists():
        config_data = json.loads(config_path.read_text(encoding="utf-8"))

    result = build_index(args.root, config_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
