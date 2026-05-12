from __future__ import annotations

import json
import re
import ast
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from mdex.tokens import estimate_tokens

FRONTMATTER_BOUNDARY_RE = re.compile(r"^\s*---\s*$")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2_PLUS_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
METADATA_LINE_RE = re.compile(r"^\*\*([^*]+)\*\*:\s*(.+?)\s*$")
TASK_ID_RE = re.compile(r"\bT\d{14}\b")
LEARNING_NOTE_HEADING_RE = re.compile(r"^(#{2,6})\s+Learning Note\s*$", re.IGNORECASE)
LEARNING_NOTE_ITEM_RE = re.compile(r"^\s*[-*]\s*([^:：]+)[:：]\s*(.*?)\s*$")
CJK_TOKEN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]{2,}")
ASCII_TERM_RE = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{2,}")

DEFAULT_LINKABLE_EXTENSIONS = (".md", ".json", ".jsonl")
DEFAULT_MARKDOWN_EXTENSION = ".md"

METADATA_KEY_MAP = {
    "id": "id",
    "project": "project",
    "status": "status",
    "type": "type",
    "updated": "updated",
    "tags": "tags",
    "depends_on": "depends_on",
    "relates_to": "relates_to",
    "links_to": "links_to",
    "links": "links_to",
}

JSON_METADATA_KEY_MAP = {
    "id": "id",
    "project": "project",
    "status": "status",
    "type": "type",
    "updated": "updated",
    "updated_at": "updated",
    "created_at": "updated",
    "timestamp": "updated",
    "time": "updated",
    "tags": "tags",
    "depends_on": "depends_on",
    "dependson": "depends_on",
    "relates_to": "relates_to",
    "relatesto": "relates_to",
    "links_to": "links_to",
    "linksto": "links_to",
    "links": "links_to",
}

JSON_TITLE_KEYS = {
    "title",
    "name",
    "label",
    "subject",
    "session_id",
    "conversation_id",
    "id",
}

JSON_DIRECT_SUMMARY_KEYS = {
    "summary",
    "description",
}

JSON_PRIORITY_TEXT_KEYS = {
    "summary",
    "description",
    "message",
    "messages",
    "content",
    "text",
    "prompt",
    "response",
    "responses",
    "assistant",
    "user",
    "system",
    "events",
    "entries",
    "logs",
    "items",
    "note",
    "details",
}

JSON_SKIP_SUMMARY_KEYS = {
    "tags",
    "updated",
    "updated_at",
    "created_at",
    "timestamp",
    "time",
    "links_to",
    "depends_on",
    "relates_to",
}

JSON_CONTAINER_KEYS = ("metadata", "mdex", "document")
PYTHON_TEST_PATH_MARKERS = ("/test/", "/tests/", "_test.", ".test.", ".spec.", "/spec/")
GENERIC_CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".ps1",
    ".rb",
    ".rs",
    ".sh",
    ".ts",
    ".tsx",
}
GENERIC_SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*(?:export\s+)?(?:class|interface|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
    re.compile(r"^\s*func(?:\s+\([^)]*\))?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    re.compile(
        r"^\s*(?:(?:public|private|protected|static|async|final|virtual|override|inline|extern|constexpr)\s+)*"
        r"[A-Za-z_][A-Za-z0-9_:<>\*&\[\],\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*(?:\{|$)"
    ),
)
GENERIC_CONTROL_NAMES = {"if", "for", "while", "switch", "catch", "return", "sizeof"}


def _normalize_updated(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    if value is None:
        return ""
    return str(value)


def _normalize_extensions(raw_value: Any) -> tuple[str, ...]:
    if isinstance(raw_value, str):
        candidates = re.split(r"[\s,]+", raw_value)
    elif isinstance(raw_value, (list, tuple, set)):
        candidates = [str(item) for item in raw_value]
    else:
        candidates = list(DEFAULT_LINKABLE_EXTENSIONS)

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
        return DEFAULT_LINKABLE_EXTENSIONS
    return tuple(normalized)


def _build_extension_pattern(allowed_extensions: tuple[str, ...]) -> str:
    parts = [re.escape(extension.lstrip(".")) for extension in sorted(allowed_extensions, key=len, reverse=True)]
    if not parts:
        parts = [re.escape(DEFAULT_MARKDOWN_EXTENSION.lstrip("."))]
    return "|".join(parts)


def _has_allowed_extension(value: str, allowed_extensions: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(lowered.endswith(extension) for extension in allowed_extensions)


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


def _normalize_metadata_key(raw_key: str) -> str:
    step = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw_key.strip())
    return re.sub(r"[^a-zA-Z0-9]+", "_", step).strip("_").lower()


def _normalize_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _term_candidates(text: str) -> list[str]:
    terms: list[str] = []
    lower_text = text.lower()
    terms.extend(match.group(0) for match in ASCII_TERM_RE.finditer(lower_text))
    terms.extend(match.group(0) for match in CJK_TOKEN_RE.finditer(text))
    return _dedupe_keep_order([term.strip() for term in terms if term.strip()])


def _symbol_alias_terms(symbols: list[str]) -> list[str]:
    terms: list[str] = []
    for symbol in symbols:
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", symbol)
        spaced = spaced.replace("_", " ").replace("-", " ")
        parts = [part.lower() for part in re.split(r"\s+", spaced) if len(part) >= 2]
        terms.extend(parts)
        if len(parts) > 1:
            terms.append("_".join(parts))
            terms.append("".join(parts))
    return _dedupe_keep_order(terms)


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

        raw_key = _normalize_metadata_key(match.group(1))
        mapped_key = METADATA_KEY_MAP.get(raw_key)
        if not mapped_key:
            continue

        raw_value = match.group(2).strip()
        if not raw_value:
            continue

        if mapped_key in {"tags", "depends_on", "relates_to", "links_to"}:
            metadata[mapped_key] = [item.strip() for item in re.split(r"[,、]", raw_value) if item.strip()]
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


def _extract_file_links(text: str, allowed_extensions: tuple[str, ...]) -> list[str]:
    links: list[str] = []
    for raw_target in MD_LINK_RE.findall(text):
        target = raw_target.strip()
        if not target:
            continue
        path_part = target.split("#", 1)[0].split("?", 1)[0].strip()
        if _has_allowed_extension(path_part, allowed_extensions):
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


def _extract_heading_section(body: str, heading_re: re.Pattern[str]) -> str:
    lines = body.splitlines()
    collected: list[str] = []
    in_section = False
    source_level = 0

    for line in lines:
        heading_match = H2_PLUS_RE.match(line)
        if not in_section:
            target_match = heading_re.match(line)
            if not target_match:
                continue
            in_section = True
            source_level = len(target_match.group(1))
            continue

        if heading_match and len(heading_match.group(1)) <= source_level:
            break
        collected.append(line)

    return "\n".join(collected).strip()


def _remove_heading_section(body: str, heading_re: re.Pattern[str]) -> str:
    lines = body.splitlines()
    kept: list[str] = []
    in_section = False
    source_level = 0

    for line in lines:
        heading_match = H2_PLUS_RE.match(line)
        if not in_section:
            target_match = heading_re.match(line)
            if target_match:
                in_section = True
                source_level = len(target_match.group(1))
                continue
            kept.append(line)
            continue

        if heading_match and len(heading_match.group(1)) <= source_level:
            in_section = False
            kept.append(line)

    return "\n".join(kept).strip()


def _extract_learning_note(body: str) -> dict[str, Any]:
    section = _extract_heading_section(body, LEARNING_NOTE_HEADING_RE)
    if not section:
        return {"captured": False, "fields": {}, "terms": []}

    fields: dict[str, str] = {}
    for line in section.splitlines():
        match = LEARNING_NOTE_ITEM_RE.match(line)
        if not match:
            continue
        key = _normalize_metadata_key(match.group(1))
        value = match.group(2).strip()
        if key and value:
            fields[key] = value

    term_source_parts = [section]
    for key in ("symptom", "next_time_query_seed", "alias", "aliases", "symptom_terms"):
        if fields.get(key):
            term_source_parts.append(fields[key])

    return {
        "captured": True,
        "fields": fields,
        "terms": _term_candidates("\n".join(term_source_parts)),
    }


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


def _clean_path_reference(raw: str, allowed_extensions: tuple[str, ...]) -> str:
    value = raw.strip().strip("`\"'[]()<>")
    value = value.rstrip(".,;:")
    value = value.split("#", 1)[0].split("?", 1)[0].strip()
    if not value:
        return ""
    if not _has_allowed_extension(value, allowed_extensions):
        return ""
    return value


def _extract_path_refs(body: str, allowed_extensions: tuple[str, ...]) -> list[str]:
    source = FENCED_CODE_RE.sub("", body)
    extension_pattern = _build_extension_pattern(allowed_extensions)
    inline_path_re = re.compile(
        rf"`([^`\n]*?\.(?:{extension_pattern})(?:#[^`\n]*)?)`",
        re.IGNORECASE,
    )
    windows_abs_re = re.compile(
        rf"(?i)\b[a-z]:[\\/][^\s`\"'<>|]+?\.(?:{extension_pattern})\b"
    )

    refs: list[str] = []
    for match in inline_path_re.findall(source):
        clean = _clean_path_reference(match, allowed_extensions)
        if clean:
            refs.append(clean)

    for match in windows_abs_re.findall(source):
        clean = _clean_path_reference(match, allowed_extensions)
        if clean:
            refs.append(clean)

    return _dedupe_keep_order(refs)


def _extract_text_file_refs(text: str, allowed_extensions: tuple[str, ...]) -> list[str]:
    extension_pattern = _build_extension_pattern(allowed_extensions)
    file_ref_re = re.compile(
        rf"(?i)(?:[a-z]:[\\/]|\.{{1,2}}[\\/])?[a-z0-9_./\\-]+?\.(?:{extension_pattern})"
    )

    refs: list[str] = []
    for match in file_ref_re.findall(text):
        clean = _clean_path_reference(match, allowed_extensions)
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


def _candidate_json_objects(payload: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return candidates

    candidates.append(payload)
    for key in JSON_CONTAINER_KEYS:
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    return candidates


def _extract_json_frontmatter(payload: Any) -> dict[str, Any]:
    frontmatter: dict[str, Any] = {}

    candidates = _candidate_json_objects(payload)
    if isinstance(payload, list):
        for item in payload[:20]:
            if isinstance(item, dict):
                candidates.append(item)

    for candidate in candidates:
        for raw_key, raw_value in candidate.items():
            mapped_key = JSON_METADATA_KEY_MAP.get(_normalize_metadata_key(str(raw_key)))
            if not mapped_key:
                continue

            if mapped_key in {"tags", "depends_on", "relates_to", "links_to"}:
                values = _normalize_str_list(raw_value)
                if values and mapped_key not in frontmatter:
                    frontmatter[mapped_key] = values
                continue

            if isinstance(raw_value, (dict, list)):
                continue

            if raw_value is None:
                continue

            if mapped_key not in frontmatter:
                frontmatter[mapped_key] = raw_value

    return frontmatter


def _find_json_scalar(payload: Any, keys: set[str], depth: int = 0) -> Any | None:
    if depth > 6:
        return None

    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            if _normalize_metadata_key(str(raw_key)) in keys and not isinstance(value, (dict, list)):
                return value
        for value in payload.values():
            found = _find_json_scalar(value, keys, depth + 1)
            if found is not None:
                return found
        return None

    if isinstance(payload, list):
        for item in payload[:20]:
            found = _find_json_scalar(item, keys, depth + 1)
            if found is not None:
                return found

    return None


def _extract_json_title(payload: Any, fallback: str) -> str:
    for candidate in _candidate_json_objects(payload):
        for raw_key, raw_value in candidate.items():
            if _normalize_metadata_key(str(raw_key)) not in JSON_TITLE_KEYS:
                continue
            if raw_value is None:
                continue
            title = str(raw_value).strip()
            if title:
                return title

    found = _find_json_scalar(payload, JSON_TITLE_KEYS)
    if found is None:
        return fallback

    title = str(found).strip()
    return title or fallback


def _append_json_string(collected: list[str], value: str, limit: int) -> None:
    if len(collected) >= limit:
        return
    clean = re.sub(r"\s+", " ", value).strip()
    if clean:
        collected.append(clean)


def _collect_json_strings(payload: Any, collected: list[str], limit: int, depth: int = 0) -> None:
    if len(collected) >= limit or depth > 6:
        return

    if isinstance(payload, str):
        _append_json_string(collected, payload, limit)
        return

    if isinstance(payload, list):
        for item in payload[:20]:
            _collect_json_strings(item, collected, limit, depth + 1)
            if len(collected) >= limit:
                return
        return

    if not isinstance(payload, dict):
        return

    priority_values: list[Any] = []
    regular_values: list[Any] = []
    for raw_key, raw_value in payload.items():
        normalized_key = _normalize_metadata_key(str(raw_key))
        if normalized_key in JSON_SKIP_SUMMARY_KEYS:
            continue
        if normalized_key in JSON_PRIORITY_TEXT_KEYS:
            priority_values.append(raw_value)
        else:
            regular_values.append(raw_value)

    for value in [*priority_values, *regular_values]:
        _collect_json_strings(value, collected, limit, depth + 1)
        if len(collected) >= limit:
            return


def _extract_json_summary(payload: Any, max_sentences: int, max_chars: int) -> str:
    direct = _find_json_scalar(payload, JSON_DIRECT_SUMMARY_KEYS)
    if direct is not None:
        direct_text = str(direct).strip()
        if direct_text:
            if len(direct_text) > max_chars:
                return direct_text[:max_chars].rstrip()
            return direct_text

    snippets: list[str] = []
    _collect_json_strings(payload, snippets, limit=40)
    source = "\n\n".join(_dedupe_keep_order(snippets))
    if not source:
        return ""

    summary = _extract_summary(source, max_sentences=max_sentences, max_chars=max_chars)
    if summary:
        return summary

    compact = source[:max_chars].rstrip()
    return compact


def _extract_json_path_refs(payload: Any, allowed_extensions: tuple[str, ...]) -> list[str]:
    snippets: list[str] = []
    _collect_json_strings(payload, snippets, limit=80)

    refs: list[str] = []
    for snippet in snippets:
        refs.extend(_extract_text_file_refs(snippet, allowed_extensions))
    return _dedupe_keep_order(refs)


def _json_links_from_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    return _normalize_str_list(frontmatter.get("links_to"))


def _load_json_payload(raw_text: str, suffix: str) -> Any:
    if suffix == ".jsonl":
        entries: list[Any] = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                entries.append(stripped)
        return entries
    return json.loads(raw_text)


def _parse_markdown_file(source_path: Path, raw_text: str, options: dict[str, Any]) -> dict[str, Any]:
    allowed_extensions = _normalize_extensions(options.get("linkable_extensions"))
    frontmatter, body = _split_frontmatter(raw_text)
    inline_metadata = _extract_inline_metadata(body)
    merged_frontmatter = _merge_frontmatter(frontmatter, inline_metadata)

    lines = body.splitlines()
    link_source = _strip_code_literals(body)

    title = _extract_title(lines, source_path.stem)
    headings = _extract_headings(lines)
    wikilinks = _extract_wikilinks(link_source)
    md_links = _extract_file_links(link_source, allowed_extensions)
    summary_max_sentences = _get_int_option(options, "summary_max_sentences", 3)
    summary_max_chars = _get_int_option(options, "summary_max_chars", 200)
    body_for_summary = _remove_heading_section(body, LEARNING_NOTE_HEADING_RE)
    summary = _extract_summary(body_for_summary, max_sentences=summary_max_sentences, max_chars=summary_max_chars)
    tags = _normalize_tags(merged_frontmatter.get("tags"))
    task_refs = _extract_task_refs(raw_text)
    path_refs = _extract_path_refs(body, allowed_extensions)
    learning_note = _extract_learning_note(body)

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
        "learning_note": learning_note,
        "search_terms": learning_note.get("terms", []),
    }


def _parse_json_file(source_path: Path, raw_text: str, options: dict[str, Any]) -> dict[str, Any]:
    allowed_extensions = _normalize_extensions(options.get("linkable_extensions"))
    summary_max_sentences = _get_int_option(options, "summary_max_sentences", 3)
    summary_max_chars = _get_int_option(options, "summary_max_chars", 200)

    try:
        payload = _load_json_payload(raw_text, source_path.suffix.lower())
    except json.JSONDecodeError:
        return _parse_text_file(source_path, raw_text, options)

    frontmatter = _extract_json_frontmatter(payload)
    title = _extract_json_title(payload, source_path.stem)
    summary = _extract_json_summary(payload, summary_max_sentences, summary_max_chars)
    tags = _normalize_tags(frontmatter.get("tags"))
    updated = _normalize_updated(frontmatter.get("updated"))
    if not updated:
        updated = _normalize_updated(_find_json_scalar(payload, {"updated", "updated_at", "created_at", "timestamp", "time"}))
    if not updated:
        mtime = source_path.stat().st_mtime
        updated = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "title": title,
        "frontmatter": frontmatter,
        "wikilinks": [],
        "md_links": _json_links_from_frontmatter(frontmatter),
        "summary": summary,
        "headings": [],
        "tags": tags,
        "updated": updated,
        "task_refs": _extract_task_refs(raw_text),
        "path_refs": _extract_json_path_refs(payload, allowed_extensions),
        "learning_note": {"captured": False, "fields": {}, "terms": []},
        "search_terms": [],
    }


def _parse_text_file(source_path: Path, raw_text: str, options: dict[str, Any]) -> dict[str, Any]:
    allowed_extensions = _normalize_extensions(options.get("linkable_extensions"))
    summary_max_sentences = _get_int_option(options, "summary_max_sentences", 3)
    summary_max_chars = _get_int_option(options, "summary_max_chars", 200)

    summary = _extract_summary(raw_text, max_sentences=summary_max_sentences, max_chars=summary_max_chars)
    if not summary:
        summary = raw_text[:summary_max_chars].strip()

    mtime = source_path.stat().st_mtime
    updated = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "title": source_path.stem,
        "frontmatter": {},
        "wikilinks": [],
        "md_links": [],
        "summary": summary,
        "headings": [],
        "tags": [],
        "updated": updated,
        "task_refs": _extract_task_refs(raw_text),
        "path_refs": _extract_text_file_refs(raw_text, allowed_extensions),
        "learning_note": {"captured": False, "fields": {}, "terms": []},
        "search_terms": [],
    }


def _is_python_test_path(source_path: Path, node_id: str = "") -> bool:
    normalized = (node_id or _path_tail(source_path)).replace("\\", "/").lower()
    return any(marker in f"/{normalized}" for marker in PYTHON_TEST_PATH_MARKERS) or normalized.rsplit("/", 1)[-1].startswith("test_")


def _path_tail(source_path: Path, *, max_parts: int = 4) -> str:
    return "/".join(source_path.parts[-max_parts:])


def _python_import_name(node: ast.AST) -> str:
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names if alias.name]
        return ", ".join(names[:3])
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            module = "." * node.level + module
        names = [alias.name for alias in node.names if alias.name]
        return f"{module}: {', '.join(names[:3])}".strip(": ")
    return ""


def _parse_python_file(source_path: Path, raw_text: str, options: dict[str, Any]) -> dict[str, Any]:
    summary_max_chars = _get_int_option(options, "summary_max_chars", 200)
    option_node_id = str(options.get("node_id", "") or "").strip().replace("\\", "/")
    is_test = _is_python_test_path(source_path, option_node_id)
    module_name = source_path.with_suffix("").name

    classes: list[str] = []
    functions: list[str] = []
    imports: list[str] = []
    try:
        tree = ast.parse(raw_text)
    except SyntaxError:
        summary = f"Python {'test' if is_test else 'module'} {module_name}. Syntax error prevented symbol extraction."
        return {
            "title": f"Python {'test' if is_test else 'module'} {module_name}",
            "frontmatter": {"type": "test" if is_test else "code"},
            "wikilinks": [],
            "md_links": [],
            "summary": summary,
            "headings": [],
            "tags": ["python", "test" if is_test else "code"],
            "updated": datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "task_refs": [],
            "path_refs": [],
            "learning_note": {"captured": False, "fields": {}, "terms": []},
            "search_terms": [module_name, source_path.stem, option_node_id or _path_tail(source_path), "python"],
            "estimated_tokens": estimate_tokens(summary),
        }

    seen_classes: set[str] = set()
    seen_functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.name not in seen_classes:
                seen_classes.add(node.name)
                classes.append(node.name)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in seen_functions:
                seen_functions.add(node.name)
                functions.append(node.name)
            continue
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_name = _python_import_name(node)
            if import_name:
                imports.append(import_name)

    parts = [f"Python {'test' if is_test else 'module'} {module_name}."]
    if classes:
        parts.append("Classes: " + ", ".join(classes[:8]) + ".")
    if functions:
        parts.append("Functions: " + ", ".join(functions[:12]) + ".")
    if imports:
        parts.append("Imports: " + ", ".join(imports[:8]) + ".")
    summary = " ".join(parts)
    if len(summary) > summary_max_chars:
        summary = summary[:summary_max_chars].rstrip()

    mtime = source_path.stat().st_mtime
    updated = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    symbols = [*classes, *functions]
    path_hint = option_node_id or _path_tail(source_path)
    code_terms = _term_candidates(
        " ".join([path_hint, *symbols, *imports]).replace("_", " ")
    )
    symbol_aliases = _symbol_alias_terms(symbols)
    search_terms = _dedupe_keep_order(
        [
            module_name,
            source_path.stem,
            path_hint,
            *symbols,
            *symbol_aliases,
            *imports,
            *code_terms[:60],
            "python",
            "test" if is_test else "code",
        ]
    )

    return {
        "title": f"Python {'test' if is_test else 'module'} {module_name}",
        "frontmatter": {"type": "test" if is_test else "code"},
        "wikilinks": [],
        "md_links": [],
        "summary": summary,
        "headings": [],
        "tags": ["python", "test" if is_test else "code", *symbols[:8]],
        "updated": updated,
        "task_refs": _extract_task_refs(raw_text),
        "path_refs": _extract_text_file_refs(raw_text, _normalize_extensions(options.get("linkable_extensions"))),
        "learning_note": {"captured": False, "fields": {}, "terms": []},
        "search_terms": search_terms,
        "estimated_tokens": estimate_tokens(summary),
    }


def _parse_code_file(source_path: Path, raw_text: str, options: dict[str, Any]) -> dict[str, Any]:
    summary_max_chars = _get_int_option(options, "summary_max_chars", 200)
    option_node_id = str(options.get("node_id", "") or "").strip().replace("\\", "/")
    path_hint = option_node_id or _path_tail(source_path)
    is_test = _is_python_test_path(source_path, option_node_id)
    stem = source_path.stem
    symbols: list[str] = []
    in_block_comment = False
    in_template_literal = False
    for line in raw_text.splitlines():
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if in_template_literal:
            if stripped.count("`") % 2 == 1:
                in_template_literal = False
            continue
        if not stripped or stripped.startswith(("//", "#", "*")):
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        if stripped.count("`") % 2 == 1:
            in_template_literal = True
            continue
        for pattern in GENERIC_SYMBOL_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            symbol = match.group(1)
            if symbol not in GENERIC_CONTROL_NAMES:
                symbols.append(symbol)
            break
    symbols = _dedupe_keep_order(symbols)[:16]

    parts = [f"Code {'test' if is_test else 'file'} {stem}."]
    if symbols:
        parts.append("Symbols: " + ", ".join(symbols[:12]) + ".")
    summary = " ".join(parts)
    if len(summary) > summary_max_chars:
        summary = summary[:summary_max_chars].rstrip()

    symbol_aliases = _symbol_alias_terms(symbols)
    terms = _dedupe_keep_order(
        [
            stem,
            path_hint,
            *symbols,
            *symbol_aliases,
            *_term_candidates(" ".join([path_hint, *symbols, *symbol_aliases]).replace("_", " ")),
            "test" if is_test else "code",
        ]
    )
    return {
        "title": f"Code {'test' if is_test else 'file'} {stem}",
        "frontmatter": {"type": "test" if is_test else "code"},
        "wikilinks": [],
        "md_links": [],
        "summary": summary,
        "headings": [],
        "tags": ["test" if is_test else "code", *symbols[:8]],
        "updated": datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "task_refs": [],
        "path_refs": [],
        "learning_note": {"captured": False, "fields": {}, "terms": []},
        "search_terms": terms,
        "estimated_tokens": estimate_tokens(summary),
    }


def parse_file(path: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    source_path = Path(path)
    raw_text = source_path.read_text(encoding="utf-8")
    suffix = source_path.suffix.lower()

    if suffix == ".md":
        parsed = _parse_markdown_file(source_path, raw_text, options)
    elif suffix in {".json", ".jsonl"}:
        parsed = _parse_json_file(source_path, raw_text, options)
    elif suffix == ".py":
        parsed = _parse_python_file(source_path, raw_text, options)
    elif suffix in GENERIC_CODE_EXTENSIONS:
        parsed = _parse_code_file(source_path, raw_text, options)
    else:
        parsed = _parse_text_file(source_path, raw_text, options)

    if int(parsed.get("estimated_tokens", 0) or 0) <= 0:
        parsed["estimated_tokens"] = estimate_tokens(raw_text)
    return parsed
