from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

FRONTMATTER_BOUNDARY_RE = re.compile(r"^\s*---\s*$")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2_PLUS_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
METADATA_LINE_RE = re.compile(r"^\*\*([^*]+)\*\*:\s*(.+?)\s*$")
TASK_ID_RE = re.compile(r"\bT\d{14}\b")
INLINE_MD_PATH_RE = re.compile(r"`([^`\n]*?\.md(?:#[^`\n]*)?)`")
WINDOWS_ABS_MD_RE = re.compile(r"(?i)\b[a-z]:[\\/][^\s`\"'<>|]+?\.md\b")

METADATA_KEY_MAP = {
    "id": "id",
    "project": "project",
    "status": "status",
    "type": "type",
    "updated": "updated",
    "tags": "tags",
    "depends_on": "depends_on",
    "relates_to": "relates_to",
}


def _normalize_updated(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    if value is None:
        return ""
    return str(value)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or not FRONTMATTER_BOUNDARY_RE.match(lines[0]):
        return {}, text

    end_idx = None
    for idx in range(1, len(lines)):
        if FRONTMATTER_BOUNDARY_RE.match(lines[idx]):
            end_idx = idx
            break

    if end_idx is None:
        return {}, text

    frontmatter_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])

    loaded = yaml.safe_load(frontmatter_text) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    return loaded, body


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _strip_code_literals(text: str) -> str:
    without_fences = FENCED_CODE_RE.sub("", text)
    return INLINE_CODE_RE.sub("", without_fences)


def _extract_inline_metadata(body: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            break

        match = METADATA_LINE_RE.match(stripped)
        if not match:
            continue

        raw_key = match.group(1).strip().lower().replace(" ", "_")
        mapped_key = METADATA_KEY_MAP.get(raw_key)
        if not mapped_key:
            continue

        raw_value = match.group(2).strip()
        if not raw_value:
            continue

        if mapped_key in {"tags", "depends_on", "relates_to"}:
            values = [item.strip() for item in re.split(r"[,、]", raw_value) if item.strip()]
            metadata[mapped_key] = values
        else:
            metadata[mapped_key] = raw_value
    return metadata


def _merge_frontmatter(frontmatter: dict[str, Any], inline_metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(frontmatter)
    for key, value in inline_metadata.items():
        if key not in merged or merged.get(key) in (None, "", []):
            merged[key] = value
    return merged


def _extract_wikilinks(text: str) -> list[str]:
    links: list[str] = []
    for match in WIKILINK_RE.findall(text):
        candidate = match.split("|", 1)[0].strip()
        if candidate:
            links.append(candidate)
    return links


def _extract_md_links(text: str) -> list[str]:
    links: list[str] = []
    for raw_target in MD_LINK_RE.findall(text):
        target = raw_target.strip()
        if not target:
            continue
        path_part = target.split("#", 1)[0].split("?", 1)[0].strip()
        if path_part.lower().endswith(".md"):
            links.append(path_part)
    return links


def _extract_title(lines: list[str], fallback: str) -> str:
    for line in lines:
        match = H1_RE.match(line)
        if match:
            return match.group(1).strip()
    return fallback


def _extract_headings(lines: list[str]) -> list[str]:
    headings: list[str] = []
    for line in lines:
        match = H2_PLUS_RE.match(line)
        if match:
            headings.append(match.group(2).strip())
    return headings


def _split_sentences(text: str) -> list[str]:
    sentences = re.findall(r".+?(?:[。．.!?！？]+|$)", text)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _extract_summary(body: str, max_sentences: int = 3, max_chars: int = 200) -> str:
    paragraphs = re.split(r"\n\s*\n", body)
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].startswith("#"):
            continue
        if all(METADATA_LINE_RE.match(line) for line in lines):
            continue
        text = " ".join(lines)
        sentences = _split_sentences(text)
        if not sentences:
            continue
        summary = "".join(sentences[:max_sentences]).strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip()
        return summary
    return ""


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _extract_task_refs(text: str) -> list[str]:
    return _dedupe_keep_order(TASK_ID_RE.findall(text))


def _clean_path_reference(raw: str) -> str:
    value = raw.strip().strip("`\"'[]()<>")
    value = value.rstrip(".,;:")
    value = value.split("#", 1)[0].split("?", 1)[0].strip()
    if not value:
        return ""
    if not value.lower().endswith(".md"):
        return ""
    return value


def _extract_path_refs(body: str) -> list[str]:
    source = FENCED_CODE_RE.sub("", body)
    refs: list[str] = []

    for match in INLINE_MD_PATH_RE.findall(source):
        clean = _clean_path_reference(match)
        if clean:
            refs.append(clean)

    for match in WINDOWS_ABS_MD_RE.findall(source):
        clean = _clean_path_reference(match)
        if clean:
            refs.append(clean)

    return _dedupe_keep_order(refs)


def _get_int_option(options: dict[str, Any], key: str, default: int) -> int:
    raw = options.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def parse_file(path: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    source_path = Path(path)
    raw_text = source_path.read_text(encoding="utf-8")

    frontmatter, body = _split_frontmatter(raw_text)
    inline_metadata = _extract_inline_metadata(body)
    merged_frontmatter = _merge_frontmatter(frontmatter, inline_metadata)

    lines = body.splitlines()
    link_source = _strip_code_literals(body)

    title = _extract_title(lines, source_path.stem)
    headings = _extract_headings(lines)
    wikilinks = _extract_wikilinks(link_source)
    md_links = _extract_md_links(link_source)
    summary_max_sentences = _get_int_option(options, "summary_max_sentences", 3)
    summary_max_chars = _get_int_option(options, "summary_max_chars", 200)
    summary = _extract_summary(body, max_sentences=summary_max_sentences, max_chars=summary_max_chars)
    tags = _normalize_tags(merged_frontmatter.get("tags"))
    task_refs = _extract_task_refs(raw_text)
    path_refs = _extract_path_refs(body)

    updated = _normalize_updated(merged_frontmatter.get("updated"))
    if not updated:
        mtime = source_path.stat().st_mtime
        updated = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "title": title,
        "frontmatter": merged_frontmatter,
        "wikilinks": wikilinks,
        "md_links": md_links,
        "summary": summary,
        "headings": headings,
        "tags": tags,
        "updated": updated,
        "task_refs": task_refs,
        "path_refs": path_refs,
    }
