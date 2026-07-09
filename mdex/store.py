from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mdex.db_ownership import has_legacy_mdex_metadata, has_mdex_schema
from mdex.locking import DEFAULT_DB_LOCK_TIMEOUT_SECONDS, exclusive_db_lock
from mdex.output_paths import ensure_scan_output_resource
from mdex.path_identity import validated_mutable_resource
from mdex.scan_manifest import ScanManifestError, load_scan_manifest

OVERRIDE_SELECT_SQL = (
    "SELECT id, summary, summary_source, summary_updated "
    "FROM node_overrides"
)

SEARCH_TOKEN_SPLIT_RE = re.compile(r"[\s,.;:!?/\\(){}\[\]<>\"'\-、。．，！？：；・「」『』（）［］｛｝＜＞]+")
WORD_TOKEN_RE = re.compile(r"[a-z0-9_]+")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]")
SCRIPT_SEGMENT_RE = re.compile(r"[a-z0-9_][a-z0-9_.-]{2,}|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]{2,}")
SEARCH_TITLE_WEIGHT = 3.0
SEARCH_SUMMARY_WEIGHT = 2.0
SEARCH_TAG_WEIGHT = 2.5
SEARCH_ID_WEIGHT = 1.0
SEARCH_TERM_WEIGHT = 2.4
SEARCH_LEARNING_NOTE_WEIGHT = 3.0


def _as_json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(loaded, list):
        return [str(item) for item in loaded]
    return []


def _as_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _ensure_owned_mdex_database(conn: sqlite3.Connection, db_path: Path) -> None:
    if not has_mdex_schema(conn):
        raise ValueError(f"refusing to mutate an unowned SQLite database: {db_path}")
    metadata = {
        str(row[0]): str(row[1])
        for row in conn.execute("SELECT key, value FROM index_metadata").fetchall()
    }
    if not has_legacy_mdex_metadata(metadata):
        raise ValueError(f"refusing to mutate an unowned SQLite database: {db_path}")
    raw_manifest = metadata.get("scan_manifest", "").strip()
    if raw_manifest:
        try:
            load_scan_manifest(metadata)
        except ScanManifestError as exc:
            raise ValueError(f"refusing to mutate database with invalid scan manifest: {db_path}") from exc


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"] or "").strip() == column_name for row in rows)


def _node_select_sql(conn: sqlite3.Connection) -> str:
    columns = [
        "id",
        "title",
        "type",
        "project",
        "status",
        "summary",
        "summary_source",
        "summary_updated",
        "estimated_tokens" if _column_exists(conn, "nodes", "estimated_tokens") else "0 AS estimated_tokens",
        "tags_json",
        "search_terms_json" if _column_exists(conn, "nodes", "search_terms_json") else "'[]' AS search_terms_json",
        "learning_note_json" if _column_exists(conn, "nodes", "learning_note_json") else "'{}' AS learning_note_json",
        "updated",
        "links_to_json",
        "depends_on_json",
        "relates_to_json",
        "metadata_json" if _column_exists(conn, "nodes", "metadata_json") else "'{}' AS metadata_json",
    ]
    return f"SELECT {', '.join(columns)} FROM nodes"


def _ensure_node_overrides_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_overrides (
            id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            summary_source TEXT NOT NULL,
            summary_updated TEXT NOT NULL
        )
        """
    )


def _load_overrides(
    conn: sqlite3.Connection,
    node_ids: Iterable[str],
) -> dict[str, dict[str, str]]:
    ids = sorted({str(node_id).strip() for node_id in node_ids if str(node_id).strip()})
    if not ids:
        return {}
    if not _table_exists(conn, "node_overrides"):
        return {}

    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"{OVERRIDE_SELECT_SQL} WHERE id IN ({placeholders})",
        ids,
    ).fetchall()

    overrides: dict[str, dict[str, str]] = {}
    for row in rows:
        node_id = str(row["id"] or "").strip()
        if not node_id:
            continue
        overrides[node_id] = {
            "summary": str(row["summary"] or ""),
            "summary_source": str(row["summary_source"] or ""),
            "summary_updated": str(row["summary_updated"] or ""),
        }
    return overrides


def _apply_override(node: dict[str, Any], overrides: dict[str, dict[str, str]]) -> dict[str, Any]:
    node_id = str(node.get("id", "")).strip()
    if not node_id or node_id not in overrides:
        return node
    merged = dict(node)
    merged.update(overrides[node_id])
    return merged


def _build_where_clauses(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in filters.items():
        if value is None:
            continue
        clauses.append(f"{column} = ?")
        params.append(value)
    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


def _normalize_filter(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _contains_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def _cjk_ngrams(text: str) -> list[str]:
    grams: list[str] = []
    for match in re.finditer(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff々〆〤ー]+", text):
        segment = match.group(0)
        if len(segment) < 4:
            continue
        for size in (2, 3, 4):
            if len(segment) < size:
                continue
            grams.extend(segment[index : index + size] for index in range(0, len(segment) - size + 1))
    return grams


def _script_segments(text: str) -> list[str]:
    if not _contains_cjk(text) or not re.search(r"[a-z0-9_]", text):
        return []
    return [match.group(0) for match in SCRIPT_SEGMENT_RE.finditer(text)]


def _parse_timestamp(value: str) -> datetime | None:
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


def _search_terms(query: str) -> list[str]:
    lowered = query.strip().lower()
    if not lowered:
        return []
    terms: list[str] = [lowered]
    seen = {lowered}
    for segment in _script_segments(lowered):
        if segment in seen:
            continue
        seen.add(segment)
        terms.append(segment)
    for token in SEARCH_TOKEN_SPLIT_RE.split(lowered):
        clean = token.strip()
        if not clean:
            continue
        if _contains_cjk(clean):
            add_token = len(clean) >= 2
        else:
            add_token = len(clean) >= 3
        if add_token and clean not in seen:
            seen.add(clean)
            terms.append(clean)
        for gram in _cjk_ngrams(clean):
            if gram in seen:
                continue
            seen.add(gram)
            terms.append(gram)
    return terms


def _search_score(node: dict[str, Any], terms: list[str]) -> float:
    title = str(node.get("title", "")).lower()
    summary = str(node.get("summary", "")).lower()
    node_id = str(node.get("id", "")).lower()
    tags = {str(item).strip().lower() for item in node.get("tags", []) if str(item).strip()}
    search_terms = [str(item).strip().lower() for item in node.get("search_terms", []) if str(item).strip()]
    search_text = " ".join(search_terms)
    learning_note = node.get("learning_note", {})
    learning_text = ""
    if isinstance(learning_note, dict):
        learning_text = json.dumps(learning_note, ensure_ascii=False).lower()
    title_words = set(WORD_TOKEN_RE.findall(title))
    summary_words = set(WORD_TOKEN_RE.findall(summary))
    id_words = set(WORD_TOKEN_RE.findall(node_id))
    tag_words = set()
    for tag in tags:
        tag_words.update(WORD_TOKEN_RE.findall(tag))

    score = 0.0
    for term in terms:
        if _contains_cjk(term):
            if term in title:
                score += SEARCH_TITLE_WEIGHT
            if term in summary:
                score += SEARCH_SUMMARY_WEIGHT
            if any(term in tag for tag in tags):
                score += SEARCH_TAG_WEIGHT
            if term in node_id:
                score += SEARCH_ID_WEIGHT
            if term in search_text:
                score += SEARCH_TERM_WEIGHT
            if term in learning_text:
                score += SEARCH_LEARNING_NOTE_WEIGHT
            continue

        is_phrase = bool(re.search(r"[\s\-_/]", term))
        if is_phrase:
            if term in title:
                score += SEARCH_TITLE_WEIGHT
            if term in summary:
                score += SEARCH_SUMMARY_WEIGHT
            if any(term in tag for tag in tags):
                score += SEARCH_TAG_WEIGHT
            if term in node_id:
                score += SEARCH_ID_WEIGHT
            if term in search_text:
                score += SEARCH_TERM_WEIGHT
            if term in learning_text:
                score += SEARCH_LEARNING_NOTE_WEIGHT
            continue

        if term in title_words:
            score += SEARCH_TITLE_WEIGHT
        if term in summary_words:
            score += SEARCH_SUMMARY_WEIGHT
        if term in tag_words:
            score += SEARCH_TAG_WEIGHT
        if term in id_words:
            score += SEARCH_ID_WEIGHT
        if term in search_terms:
            score += SEARCH_TERM_WEIGHT
        if term in learning_text:
            score += SEARCH_LEARNING_NOTE_WEIGHT
    return score


def _row_to_node(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"] or ""),
        "title": str(row["title"] or ""),
        "type": str(row["type"] or ""),
        "project": str(row["project"] or ""),
        "status": str(row["status"] or ""),
        "summary": str(row["summary"] or ""),
        "summary_source": str(row["summary_source"] or ""),
        "summary_updated": str(row["summary_updated"] or ""),
        "estimated_tokens": int(row["estimated_tokens"] or 0),
        "tags": _as_json_list(row["tags_json"]),
        "search_terms": _as_json_list(row["search_terms_json"]),
        "learning_note": _as_json_dict(row["learning_note_json"]),
        "updated": str(row["updated"] or ""),
        "links_to": _as_json_list(row["links_to_json"]),
        "depends_on": _as_json_list(row["depends_on_json"]),
        "relates_to": _as_json_list(row["relates_to_json"]),
        "metadata": _as_json_dict(row["metadata_json"]),
    }


def list_nodes(
    db_path: str,
    *,
    node_type: str | None = None,
    project: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    filters = {
        "LOWER(type)": _normalize_filter(node_type),
        "LOWER(project)": _normalize_filter(project),
        "LOWER(status)": _normalize_filter(status),
    }
    where_sql, params = _build_where_clauses(filters)

    with _connect(db_path) as conn:
        query = f"{_node_select_sql(conn)}{where_sql} ORDER BY id"
        rows = conn.execute(query, params).fetchall()
        nodes = [_row_to_node(row) for row in rows]
        overrides = _load_overrides(conn, (node.get("id", "") for node in nodes))

    return [_apply_override(node, overrides) for node in nodes]


def get_node(db_path: str, node_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        select_sql = _node_select_sql(conn)
        row = conn.execute(
            f"{select_sql} WHERE id = ?",
            (node_id,),
        ).fetchone()
        overrides = _load_overrides(conn, [node_id])

    if row is None:
        return None

    return _apply_override(_row_to_node(row), overrides)


def search_nodes(
    db_path: str,
    query: str,
    limit: int = 20,
    *,
    nodes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    terms = _search_terms(query)
    if not terms:
        return []
    safe_limit = _coerce_positive_int(limit, 20)
    candidate_nodes = nodes if nodes is not None else list_nodes(db_path)

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for node in candidate_nodes:
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            continue
        score = _search_score(node, terms)
        if score <= 0:
            continue
        scored.append((score, node_id, node))

    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored[:safe_limit]]


def list_orphan_nodes(db_path: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        select_sql = _node_select_sql(conn)
        sql = (
            f"{select_sql} AS n WHERE NOT EXISTS ("
            "SELECT 1 FROM edges e "
            "WHERE e.resolved = 1 AND (e.src = n.id OR e.dst = n.id)"
            ") ORDER BY n.id"
        )
        rows = conn.execute(sql).fetchall()
        nodes = [_row_to_node(row) for row in rows]
        overrides = _load_overrides(conn, (node.get("id", "") for node in nodes))
    return [_apply_override(node, overrides) for node in nodes]


def list_stale_nodes(db_path: str, days: int = 30) -> list[dict[str, Any]]:
    safe_days = _coerce_positive_int(days, 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=safe_days)
    nodes = list_nodes(db_path)

    stale_rows: list[tuple[datetime | None, dict[str, Any]]] = []
    for node in nodes:
        summary_source = str(node.get("summary_source", "")).strip().lower()
        if summary_source == "agent":
            continue

        updated = str(node.get("updated", "")).strip()
        parsed_updated = _parse_timestamp(updated)
        if parsed_updated is not None and parsed_updated > cutoff:
            continue

        stale_rows.append(
            (
                parsed_updated,
                {
                    "id": str(node.get("id", "")),
                    "title": str(node.get("title", "")),
                    "type": str(node.get("type", "")),
                    "status": str(node.get("status", "")),
                    "summary_source": str(node.get("summary_source", "")),
                    "updated": updated,
                },
            )
        )

    max_dt = datetime.max.replace(tzinfo=timezone.utc)
    stale_rows.sort(
        key=lambda row: (
            1 if row[0] is None else 0,
            row[0] if row[0] is not None else max_dt,
            row[1]["id"],
        )
    )
    return [row[1] for row in stale_rows]


def list_edges(
    db_path: str,
    *,
    src: str | None = None,
    dst: str | None = None,
    edge_type: str | None = None,
    resolved: bool | None = None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []

    if src is not None:
        filters.append("src = ?")
        params.append(src)
    if dst is not None:
        filters.append("dst = ?")
        params.append(dst)
    if edge_type is not None:
        filters.append("type = ?")
        params.append(edge_type)
    if resolved is not None:
        filters.append("resolved = ?")
        params.append(1 if resolved else 0)

    where_sql = ""
    if filters:
        where_sql = " WHERE " + " AND ".join(filters)

    query = f"SELECT src, dst, type, resolved FROM edges{where_sql} ORDER BY src, type, dst"

    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "from": str(row["src"] or ""),
            "to": str(row["dst"] or ""),
            "type": str(row["type"] or ""),
            "resolved": bool(int(row["resolved"] or 0)),
        }
        for row in rows
    ]


def list_missing_links(db_path: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    references: dict[str, set[str]] = {}
    for edge in list_edges(db_path, edge_type="links_to", resolved=False):
        target = str(edge.get("to", "")).strip()
        source = str(edge.get("from", "")).strip()
        if not target or not source:
            continue
        references.setdefault(target, set()).add(source)

    rows = [
        {
            "id": target,
            "type": "links_to",
            "count": len(sources),
            "referenced_by": sorted(sources),
        }
        for target, sources in references.items()
    ]
    rows.sort(key=lambda row: (-int(row["count"]), str(row["id"])))
    if limit is None:
        return rows
    return rows[: _coerce_positive_int(limit, 20)]


def list_edges_for_nodes(
    db_path: str,
    node_ids: Iterable[str],
    *,
    resolved_only: bool = True,
) -> list[dict[str, Any]]:
    ids = sorted({str(node_id).strip() for node_id in node_ids if str(node_id).strip()})
    if not ids:
        return []

    placeholders = ",".join("?" for _ in ids)
    filters: list[str] = [f"(src IN ({placeholders}) OR dst IN ({placeholders}))"]
    params: list[Any] = [*ids, *ids]
    if resolved_only:
        filters.append("resolved = 1")

    where_sql = " WHERE " + " AND ".join(filters)
    query = f"SELECT src, dst, type, resolved FROM edges{where_sql} ORDER BY src, type, dst"

    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "from": str(row["src"] or ""),
            "to": str(row["dst"] or ""),
            "type": str(row["type"] or ""),
            "resolved": bool(int(row["resolved"] or 0)),
        }
        for row in rows
    ]


def list_index_metadata(db_path: str) -> dict[str, str]:
    with _connect(db_path) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_metadata'"
        ).fetchone()
        if table_exists is None:
            return {}
        rows = conn.execute("SELECT key, value FROM index_metadata").fetchall()

    metadata: dict[str, str] = {}
    for row in rows:
        key = str(row["key"] or "").strip()
        if not key:
            continue
        metadata[key] = str(row["value"] or "")
    return metadata


def list_node_override_ids(db_path: str) -> list[str]:
    with _connect(db_path) as conn:
        if not _table_exists(conn, "node_overrides"):
            return []
        rows = conn.execute("SELECT id FROM node_overrides ORDER BY id").fetchall()
    return [str(row["id"] or "") for row in rows if str(row["id"] or "").strip()]


def list_node_overrides(db_path: str) -> list[dict[str, str]]:
    with _connect(db_path) as conn:
        if not _table_exists(conn, "node_overrides"):
            return []
        rows = conn.execute(f"{OVERRIDE_SELECT_SQL} ORDER BY id").fetchall()
    return [
        {
            "id": str(row["id"] or ""),
            "summary": str(row["summary"] or ""),
            "summary_source": str(row["summary_source"] or ""),
            "summary_updated": str(row["summary_updated"] or ""),
        }
        for row in rows
        if str(row["id"] or "").strip()
    ]


def get_index_metadata(db_path: str, key: str, default: str | None = None) -> str | None:
    metadata = list_index_metadata(db_path)
    if key in metadata:
        return metadata[key]
    return default


def get_scan_root(db_path: str, default: str = ".") -> str:
    value = get_index_metadata(db_path, "scan_root", default)
    if value is None:
        return default
    cleaned = value.strip()
    return cleaned or default


def update_node_summary(
    db_path: str,
    node_id: str,
    summary: str,
    *,
    source: str = "agent",
    lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
) -> bool:
    result = apply_node_summary(
        db_path,
        node_id,
        summary,
        source=source,
        overwrite_existing_agent=True,
        lock_timeout=lock_timeout,
    )
    return result.get("status") == "updated"


def apply_node_summary(
    db_path: str,
    node_id: str,
    summary: str,
    *,
    source: str = "agent",
    overwrite_existing_agent: bool = False,
    lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
) -> dict[str, str]:
    clean_id = node_id.strip()
    if not clean_id:
        return {"status": "missing"}
    clean_summary = summary.strip()
    clean_source = source.strip() if source and source.strip() else "agent"
    ensure_scan_output_resource(db_path)
    safe_db_path = validated_mutable_resource(db_path, label="database")
    with exclusive_db_lock(safe_db_path, timeout=lock_timeout):
        safe_db_path = validated_mutable_resource(safe_db_path, label="database")
        with _connect(str(safe_db_path)) as conn:
            _ensure_owned_mdex_database(conn, safe_db_path)
            _ensure_node_overrides_table(conn)
            row = conn.execute(
                """
                SELECT
                    nodes.summary AS seed_summary,
                    nodes.summary_source AS seed_source,
                    node_overrides.id AS override_id,
                    node_overrides.summary AS override_summary,
                    node_overrides.summary_source AS override_source
                FROM nodes
                LEFT JOIN node_overrides ON node_overrides.id = nodes.id
                WHERE nodes.id = ?
                """,
                (clean_id,),
            ).fetchone()
            if row is None:
                return {"status": "missing"}

            has_override = row["override_id"] is not None
            previous_summary = str(
                row["override_summary"] if has_override else row["seed_summary"] or ""
            )
            previous_source = str(
                row["override_source"] if has_override else row["seed_source"] or "seed"
            ).strip().lower()
            if previous_source == "agent" and not overwrite_existing_agent:
                return {
                    "status": "skipped",
                    "previous_summary": previous_summary,
                    "previous_source": previous_source,
                }

            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                """
                INSERT INTO node_overrides (id, summary, summary_source, summary_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    summary = excluded.summary,
                    summary_source = excluded.summary_source,
                    summary_updated = excluded.summary_updated
                """,
                (clean_id, clean_summary, clean_source, now),
            )
            conn.commit()
            if int(cursor.rowcount or 0) <= 0:
                return {"status": "failed"}
            return {
                "status": "updated",
                "previous_summary": previous_summary,
                "previous_source": previous_source,
            }


def resolve_node_id_from_path(db_path: str, absolute_path: str) -> str | None:
    scan_root = Path(get_scan_root(db_path, default=".")).resolve()
    target_path = Path(absolute_path).resolve()

    try:
        node_id = target_path.relative_to(scan_root).as_posix()
    except ValueError:
        return None

    with _connect(db_path) as conn:
        row = conn.execute("SELECT id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        return str(row["id"] or "").strip() or None


def count_edges(edge_rows: Iterable[dict[str, Any]]) -> tuple[int, int, int]:
    total = 0
    resolved = 0
    for edge in edge_rows:
        total += 1
        if bool(edge.get("resolved", False)):
            resolved += 1
    unresolved = total - resolved
    return total, resolved, unresolved
