from __future__ import annotations

import os
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


def parse_file(path: str) -> dict[str, Any]:
    source_path = Path(path)
    raw_text = source_path.read_text(encoding="utf-8")

    frontmatter, body = _split_frontmatter(raw_text)
    lines = body.splitlines()

    title = _extract_title(lines, source_path.stem)
    headings = _extract_headings(lines)
    wikilinks = _extract_wikilinks(body)
    md_links = _extract_md_links(body)
    summary = _extract_summary(body, max_sentences=3, max_chars=200)
    tags = _normalize_tags(frontmatter.get("tags"))

    updated = _normalize_updated(frontmatter.get("updated"))
    if not updated:
        mtime = source_path.stat().st_mtime
        updated = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "title": title,
        "frontmatter": frontmatter,
        "wikilinks": wikilinks,
        "md_links": md_links,
        "summary": summary,
        "headings": headings,
        "tags": tags,
        "updated": updated,
    }
