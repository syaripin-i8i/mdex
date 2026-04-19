from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.parser import parse_file
from runtime.scanner import DEFAULT_INDEX_EXTENSIONS, list_indexable_files


URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://")
DEFAULT_MARKDOWN_EXTENSION = ".md"


def _to_posix(path_value: str) -> str:
    return path_value.replace("\\", "/")


def _node_id_for_path(file_path: Path, root_path: Path) -> str:
    resolved = file_path.resolve()
    root_resolved = root_path.resolve()
    return _to_posix(str(resolved.relative_to(root_resolved)))


def _normalize_root_inputs(root: str | Path | Iterable[str | Path]) -> tuple[Path, list[Path]]:
    if isinstance(root, (str, Path)):
        roots = [Path(root).resolve()]
        return roots[0], roots

    raw_items = list(root)
    if not raw_items:
        base = Path(".").resolve()
        return base, [base]

    roots: list[Path] = []
    seen = set()
    for item in raw_items:
        resolved = Path(item).resolve()
        key = resolved.as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)

    if not roots:
        base = Path(".").resolve()
        return base, [base]

    if len(roots) == 1:
        return roots[0], roots

    common = Path(os.path.commonpath([str(path) for path in roots])).resolve()
    return common, roots


def _resolve_indexed_files(root_path: Path, indexed_paths: list[Path]) -> list[Path]:
    root_resolved = root_path.resolve()
    indexed_files: list[Path] = []
    for candidate in indexed_paths:
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            continue
        indexed_files.append(candidate)
    return indexed_files


def _normalize_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_extensions(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = re.split(r"[\s,]+", value)
    elif isinstance(value, (list, tuple, set)):
        candidates = [str(item) for item in value]
    else:
        candidates = list(DEFAULT_INDEX_EXTENSIONS)

    normalized: list[str] = []
    seen = set()
    for candidate in candidates:
        extension = str(candidate).strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension}"
        if extension in seen:
            continue
        seen.add(extension)
        normalized.append(extension)

    if not normalized:
        return DEFAULT_INDEX_EXTENSIONS
    return tuple(normalized)


def _has_allowed_extension(value: str, allowed_extensions: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(lowered.endswith(extension) for extension in allowed_extensions)


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

    if Path(file_id).suffix.lower() == ".jsonl":
        return "log"
    return "unknown"


def _to_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_noise_target(value: str) -> bool:
    target = value.strip()
    if not target:
        return True

    lowered = target.lower()
    if lowered in {".md", "..md", "...md", "....md"}:
        return True
    if any(mark in target for mark in ("{", "}", "*")):
        return True
    if "__id__" in lowered or "{id}" in lowered:
        return True
    return False


def _clean_wikilink_target(raw: str, allowed_extensions: tuple[str, ...]) -> str:
    clean = raw.split("|", 1)[0].split("#", 1)[0].strip()
    if not clean:
        return ""
    clean = _to_posix(clean)
    if _has_allowed_extension(clean, allowed_extensions):
        pass
    elif Path(clean).suffix:
        return ""
    else:
        clean = f"{clean}{DEFAULT_MARKDOWN_EXTENSION}"
    if _is_noise_target(clean):
        return ""
    return clean


def _clean_frontmatter_target(raw: str, allowed_extensions: tuple[str, ...]) -> str:
    clean = raw.strip()
    if clean.startswith("[[") and clean.endswith("]]"):
        clean = clean[2:-2]
    clean = clean.split("|", 1)[0].split("#", 1)[0].strip()
    clean = _to_posix(clean)
    if Path(clean).suffix and not _has_allowed_extension(clean, allowed_extensions):
        return ""
    if _is_noise_target(clean):
        return ""
    return clean


def _clean_md_link_target(raw: str, allowed_extensions: tuple[str, ...]) -> str:
    clean = raw.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean:
        return ""
    if URL_SCHEME_RE.match(clean):
        return ""
    if _has_allowed_extension(clean, allowed_extensions):
        clean = _to_posix(clean)
        if _is_noise_target(clean):
            return ""
        return clean
    return ""


def _clean_path_reference_target(raw: str, allowed_extensions: tuple[str, ...]) -> str:
    clean = raw.strip().strip("`\"'[]()<>").rstrip(".,;:")
    clean = clean.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean:
        return ""
    if URL_SCHEME_RE.match(clean):
        return ""
    if _has_allowed_extension(clean, allowed_extensions):
        clean = _to_posix(clean)
        if _is_noise_target(clean):
            return ""
        return clean
    return ""


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


def _common_prefix_len(a: list[str], b: list[str]) -> int:
    count = 0
    for x, y in zip(a, b):
        if x != y:
            break
        count += 1
    return count


def _choose_best_candidate(candidates: list[str], source_node_id: str) -> str:
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    source_parts = [part.lower() for part in Path(source_node_id).parts]
    sorted_candidates = sorted(candidates)

    def _score(candidate_id: str) -> tuple[int, int, int, int]:
        candidate_parts = [part.lower() for part in Path(candidate_id).parts]
        same_top = int(
            bool(source_parts) and bool(candidate_parts) and source_parts[0] == candidate_parts[0]
        )
        common = _common_prefix_len(source_parts, candidate_parts)
        depth_delta = -abs(len(source_parts) - len(candidate_parts))
        path_depth = -len(candidate_parts)
        return (same_top, common, depth_delta, path_depth)

    return max(sorted_candidates, key=_score)


def _build_lookup_maps(path_to_id: dict[Path, str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    stem_to_ids: dict[str, list[str]] = {}
    name_to_ids: dict[str, list[str]] = {}

    for node_id in path_to_id.values():
        name = Path(node_id).name.lower()
        stem = Path(node_id).stem.lower()
        name_to_ids.setdefault(name, []).append(node_id)
        stem_to_ids.setdefault(stem, []).append(node_id)

    return stem_to_ids, name_to_ids


def _resolve_target_id(
    raw_target: str,
    source_file: Path,
    source_node_id: str,
    root_path: Path,
    path_to_id: dict[Path, str],
    stem_to_ids: dict[str, list[str]],
    name_to_ids: dict[str, list[str]],
    allowed_extensions: tuple[str, ...],
) -> tuple[str, bool]:
    target = raw_target.strip().strip("`\"'")
    if not target:
        return "", False

    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target:
        return "", False
    if URL_SCHEME_RE.match(target):
        return "", False

    source_dir = source_file.parent.resolve()
    target_path = Path(target)

    root_resolved = root_path.resolve()

    if target_path.is_absolute():
        absolute_target = target_path.resolve()
        if absolute_target in path_to_id:
            return path_to_id[absolute_target], True
        try:
            return _to_posix(str(absolute_target.relative_to(root_resolved))), False
        except ValueError:
            return "", False

    absolute_from_source = (source_dir / target_path).resolve()
    if absolute_from_source in path_to_id:
        return path_to_id[absolute_from_source], True

    absolute_from_root = (root_resolved / target_path).resolve()
    if absolute_from_root in path_to_id:
        return path_to_id[absolute_from_root], True

    if not target_path.suffix:
        for extension in allowed_extensions:
            source_candidate = (source_dir / f"{target}{extension}").resolve()
            if source_candidate in path_to_id:
                return path_to_id[source_candidate], True

            root_candidate = (root_path.resolve() / f"{target}{extension}").resolve()
            if root_candidate in path_to_id:
                return path_to_id[root_candidate], True

    target_name = target_path.name.lower()
    if target_name in name_to_ids:
        return _choose_best_candidate(name_to_ids[target_name], source_node_id), True

    stem = Path(target_name).stem.lower()
    if stem in stem_to_ids:
        return _choose_best_candidate(stem_to_ids[stem], source_node_id), True

    unresolved = _to_posix(target)
    if unresolved.startswith("./"):
        unresolved = unresolved[2:]
    return unresolved, False


def _resolve_targets(
    raw_targets: list[str],
    source_file: Path,
    source_node_id: str,
    root_path: Path,
    path_to_id: dict[Path, str],
    stem_to_ids: dict[str, list[str]],
    name_to_ids: dict[str, list[str]],
    allowed_extensions: tuple[str, ...],
) -> list[tuple[str, bool]]:
    resolved_targets: list[tuple[str, bool]] = []
    seen = set()
    for target in raw_targets:
        target_id, is_resolved = _resolve_target_id(
            target,
            source_file,
            source_node_id,
            root_path,
            path_to_id,
            stem_to_ids,
            name_to_ids,
            allowed_extensions,
        )
        if not target_id or target_id == source_node_id:
            continue
        key = (target_id, is_resolved)
        if key in seen:
            continue
        seen.add(key)
        resolved_targets.append((target_id, is_resolved))
    return resolved_targets


def build_index(
    root: str | Path | Iterable[str | Path],
    config: dict[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    root_path, scan_roots = _normalize_root_inputs(root)
    exclude_patterns = _normalize_str_list(config.get("exclude_patterns"))
    include_extensions = _normalize_extensions(config.get("include_extensions"))
    node_type_map = config.get("node_type_map") or {}
    if not isinstance(node_type_map, dict):
        node_type_map = {}

    parser_options = {
        "summary_max_sentences": config.get("summary_max_sentences", 3),
        "summary_max_chars": config.get("summary_max_chars", 200),
        "linkable_extensions": list(include_extensions),
    }

    indexed_paths = list_indexable_files(scan_roots, include_extensions, exclude_patterns)
    indexed_files = _resolve_indexed_files(root_path, indexed_paths)
    path_to_id: dict[Path, str] = {}
    id_to_path: dict[str, Path] = {}
    for file_path in indexed_files:
        try:
            node_id = _node_id_for_path(file_path, root_path)
        except ValueError:
            continue
        if node_id in id_to_path:
            raise ValueError(f"node_id collision detected across scan roots: {node_id}")
        id_to_path[node_id] = file_path
        path_to_id[file_path] = node_id
    indexed_files = list(path_to_id.keys())
    stem_to_ids, name_to_ids = _build_lookup_maps(path_to_id)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_keys = set()
    warnings: list[dict[str, str]] = []

    for file_path in indexed_files:
        try:
            parsed = parse_file(str(file_path), options=parser_options)
        except Exception as exc:
            if strict:
                raise
            warning_path = path_to_id.get(file_path, _to_posix(str(file_path)))
            warnings.append({"path": warning_path, "error": str(exc)})
            continue
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
            _clean_wikilink_target(str(item), include_extensions)
            for item in parsed.get("wikilinks", [])
            if str(item).strip()
        ]
        md_link_targets = [
            _clean_md_link_target(str(item), include_extensions)
            for item in parsed.get("md_links", [])
            if str(item).strip()
        ]
        path_ref_targets = [
            _clean_path_reference_target(str(item), include_extensions)
            for item in parsed.get("path_refs", [])
            if str(item).strip()
        ]
        links_from_metadata_raw = [
            _clean_frontmatter_target(item, include_extensions)
            for item in _normalize_str_list(frontmatter.get("links_to"))
        ]
        links_to_pairs = _resolve_targets(
            [
                target
                for target in wikilink_targets + md_link_targets + path_ref_targets + links_from_metadata_raw
                if target
            ],
            file_path,
            node_id,
            root_path,
            path_to_id,
            stem_to_ids,
            name_to_ids,
            include_extensions,
        )

        depends_raw = [
            _clean_frontmatter_target(item, include_extensions)
            for item in _normalize_str_list(frontmatter.get("depends_on"))
        ]
        relates_raw = [
            _clean_frontmatter_target(item, include_extensions)
            for item in _normalize_str_list(frontmatter.get("relates_to"))
        ]

        depends_pairs = _resolve_targets(
            [target for target in depends_raw if target],
            file_path,
            node_id,
            root_path,
            path_to_id,
            stem_to_ids,
            name_to_ids,
            include_extensions,
        )

        task_ref_raw = [
            f"{str(task_id).strip()}.md"
            for task_id in parsed.get("task_refs", [])
            if str(task_id).strip()
        ]
        relates_pairs = _resolve_targets(
            [target for target in relates_raw + task_ref_raw if target],
            file_path,
            node_id,
            root_path,
            path_to_id,
            stem_to_ids,
            name_to_ids,
            include_extensions,
        )

        links_to_resolved = [target for target, is_resolved in links_to_pairs if is_resolved]
        depends_resolved = [target for target, is_resolved in depends_pairs if is_resolved]
        relates_resolved = [target for target, is_resolved in relates_pairs if is_resolved]

        node = {
            "id": node_id,
            "title": title,
            "type": node_type,
            "project": project_str,
            "status": status_str,
            "summary": parsed.get("summary", ""),
            "tags": parsed.get("tags", []),
            "updated": parsed.get("updated", ""),
            "estimated_tokens": int(parsed.get("estimated_tokens", 0) or 0),
            "links_to": links_to_resolved,
            "depends_on": depends_resolved,
            "relates_to": relates_resolved,
        }
        nodes.append(node)

        for edge_type, target_pairs in (
            ("links_to", links_to_pairs),
            ("depends_on", depends_pairs),
            ("relates_to", relates_pairs),
        ):
            for target, is_resolved in target_pairs:
                if not target:
                    continue
                edge_key = (node_id, target, edge_type, is_resolved)
                if edge_key in edge_keys:
                    continue
                edge_keys.add(edge_key)
                edges.append(
                    {"from": node_id, "to": target, "type": edge_type, "resolved": is_resolved}
                )

    return {
        "generated": _to_iso_now(),
        "scan_root": _to_posix(str(root_path)),
        "scan_roots": [_to_posix(str(path)) for path in scan_roots],
        "nodes": nodes,
        "edges": edges,
        "warnings": warnings,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build an index from Markdown and JSON sources.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=False)
    args = parser.parse_args()

    config_path = Path(args.config).resolve() if args.config else Path("control/scan_config.json").resolve()
    config_data: dict[str, Any] = {}
    if config_path.exists():
        config_data = json.loads(config_path.read_text(encoding="utf-8"))

    result = build_index(args.root, config_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
