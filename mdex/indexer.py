from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from mdex.db_ownership import has_legacy_mdex_metadata, has_mdex_schema
from mdex.locking import (
    DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
    exclusive_db_lock,
    exclusive_resource_lock,
    exclusive_resource_locks,
)
from mdex.output_paths import ensure_distinct_scan_outputs, ensure_scan_output_resource
from mdex.path_identity import (
    canonical_path_key,
    validate_directory_identities,
    validated_mutable_resource,
)
from mdex.scan_manifest import (
    SCAN_MANIFEST_VERSION,
    ScanManifestError,
    config_file_hash,
    load_scan_manifest,
    normalized_index_kind,
    scan_manifest_from_index,
    set_scan_manifest,
)


class ScanOutputsWriteError(RuntimeError):
    def __init__(
        self,
        detail: str,
        *,
        db_path: str,
        json_path: str,
        db_written: bool,
        json_written: bool,
    ) -> None:
        super().__init__(detail)
        self.db_path = db_path
        self.json_path = json_path
        self.db_written = db_written
        self.json_written = json_written


def _write_json_unlocked(index: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(_json_public_index(index), ensure_ascii=False, indent=2) + "\n"

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=str(output.parent),
    )
    temp_path = Path(temp_name)
    try:
        handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        fd = -1
        with handle:
            handle.write(serialized)
        os.replace(temp_path, output)
    finally:
        try:
            if fd >= 0:
                os.close(fd)
        finally:
            temp_path.unlink(missing_ok=True)


def write_json(
    index: dict[str, Any],
    output_path: str,
    *,
    lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
) -> None:
    if scan_manifest_from_index(index) is not None:
        raise ValueError("manifest-backed JSON must be written with write_scan_outputs")
    ensure_scan_output_resource(output_path)
    output = validated_mutable_resource(output_path, label="JSON output")
    with exclusive_resource_lock(output, timeout=lock_timeout):
        output = validated_mutable_resource(output, label="JSON output")
        if output.exists():
            try:
                existing = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing = None
            if isinstance(existing, dict) and "scan_manifest" in existing:
                raise ValueError(
                    "manifest-backed JSON must be updated with write_scan_outputs"
                )
        _write_json_unlocked(index, output)


def _json_public_index(index: dict[str, Any]) -> dict[str, Any]:
    public_index = dict(index)
    public_index.pop("_source_paths", None)
    public_index.pop("_scan_manifest", None)
    manifest = scan_manifest_from_index(index)
    if manifest is not None:
        public_index["scan_manifest"] = {
            "manifest_version": manifest.get("manifest_version"),
            "scan_id": manifest.get("scan_id"),
            "index_kind": manifest.get("index_kind"),
            "output": manifest.get("output"),
        }
    public_nodes: list[dict[str, Any]] = []
    for node in _normalize_nodes(index):
        public_nodes.append(
            {
                key: value
                for key, value in node.items()
                if key not in {"search_terms", "learning_note"}
            }
        )
    public_index["nodes"] = public_nodes
    return public_index


def _normalize_nodes(index: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = index.get("nodes", [])
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _normalize_edges(index: dict[str, Any]) -> list[dict[str, Any]]:
    edges = index.get("edges", [])
    if not isinstance(edges, list):
        return []
    return [edge for edge in edges if isinstance(edge, dict)]


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _fetch_node_overrides(cur: sqlite3.Cursor) -> list[tuple[str, str, str, str]]:
    if not _table_exists(cur, "node_overrides"):
        return []
    rows = cur.execute(
        "SELECT id, summary, summary_source, summary_updated FROM node_overrides"
    ).fetchall()
    return [
        (
            str(row[0] or ""),
            str(row[1] or ""),
            str(row[2] or ""),
            str(row[3] or ""),
        )
        for row in rows
        if str(row[0] or "").strip()
    ]


def _create_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            title TEXT,
            type TEXT,
            project TEXT,
            status TEXT,
            summary TEXT,
            summary_source TEXT,
            summary_updated TEXT,
            estimated_tokens INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT,
            search_terms_json TEXT,
            learning_note_json TEXT,
            updated TEXT,
            links_to_json TEXT,
            depends_on_json TEXT,
            relates_to_json TEXT,
            metadata_json TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE edges (
            src TEXT NOT NULL,
            dst TEXT NOT NULL,
            type TEXT NOT NULL,
            resolved INTEGER NOT NULL,
            PRIMARY KEY (src, dst, type)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE index_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE node_overrides (
            id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            summary_source TEXT NOT NULL,
            summary_updated TEXT NOT NULL
        )
        """
    )


def _restore_node_overrides(
    cur: sqlite3.Cursor,
    rows: list[tuple[str, str, str, str]],
    indexed_node_ids: set[str],
) -> None:
    for row in rows:
        node_id = str(row[0] or "").strip()
        if node_id not in indexed_node_ids:
            continue
        cur.execute(
            """
            INSERT OR REPLACE INTO node_overrides (id, summary, summary_source, summary_updated)
            VALUES (?, ?, ?, ?)
            """,
            row,
        )


def _insert_nodes(cur: sqlite3.Cursor, nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        cur.execute(
            """
            INSERT OR REPLACE INTO nodes (
                id, title, type, project, status, summary, summary_source, summary_updated,
                estimated_tokens, tags_json, search_terms_json, learning_note_json,
                updated, links_to_json, depends_on_json, relates_to_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(node.get("id", "")),
                str(node.get("title", "")),
                str(node.get("type", "")),
                str(node.get("project", "")),
                str(node.get("status", "")),
                str(node.get("summary", "")),
                "seed",
                str(node.get("updated", "")),
                int(node.get("estimated_tokens", 0) or 0),
                json.dumps(node.get("tags", []), ensure_ascii=False),
                json.dumps(node.get("search_terms", []), ensure_ascii=False),
                json.dumps(node.get("learning_note", {}), ensure_ascii=False),
                str(node.get("updated", "")),
                json.dumps(node.get("links_to", []), ensure_ascii=False),
                json.dumps(node.get("depends_on", []), ensure_ascii=False),
                json.dumps(node.get("relates_to", []), ensure_ascii=False),
                json.dumps(node.get("metadata", {}), ensure_ascii=False),
            ),
        )


def _insert_edges(cur: sqlite3.Cursor, edges: list[dict[str, Any]]) -> None:
    for edge in edges:
        cur.execute(
            """
            INSERT OR REPLACE INTO edges (src, dst, type, resolved)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(edge.get("from", "")),
                str(edge.get("to", "")),
                str(edge.get("type", "")),
                1 if bool(edge.get("resolved", True)) else 0,
            ),
        )


def _insert_metadata(cur: sqlite3.Cursor, index: dict[str, Any]) -> None:
    scan_roots = index.get("scan_roots")
    if not isinstance(scan_roots, list) or not scan_roots:
        scan_root = str(index.get("scan_root", "")).strip()
        scan_roots = [scan_root] if scan_root else []
    metadata_rows = {
        "generated": str(index.get("generated", "")),
        "scan_root": str(index.get("scan_root", "")),
        "scan_roots": json.dumps(scan_roots, ensure_ascii=False),
        "warnings": json.dumps(index.get("warnings", []), ensure_ascii=False),
        "fingerprints": json.dumps(index.get("fingerprints", {}), ensure_ascii=False),
    }
    manifest = scan_manifest_from_index(index)
    if manifest is not None:
        metadata_rows.update(
            {
                "scan_manifest": json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                "config_hash": str(manifest.get("config_hash", "")),
                "config_path": str(manifest.get("config_path", "")),
                "output_json": str(manifest.get("output_json", "")),
                "index_kind": str(manifest.get("index_kind", "")),
                "scan_id": str(manifest.get("scan_id", "")),
            }
        )
    for key, value in metadata_rows.items():
        cur.execute(
            """
            INSERT OR REPLACE INTO index_metadata (key, value)
            VALUES (?, ?)
            """,
            (key, value),
        )


def _create_indexes(cur: sqlite3.Cursor) -> None:
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_resolved ON edges(resolved)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status)")


def _load_existing_overrides(db_path: Path) -> list[tuple[str, str, str, str]]:
    if not db_path.exists():
        return []
    db = sqlite3.connect(str(db_path))
    try:
        cur = db.cursor()
        return _fetch_node_overrides(cur)
    finally:
        db.close()


def _read_existing_metadata(db_path: Path) -> dict[str, str]:
    if not db_path.exists():
        return {}
    db = sqlite3.connect(str(db_path))
    try:
        cur = db.cursor()
        if not _table_exists(cur, "index_metadata"):
            return {}
        return {
            str(key): str(value)
            for key, value in cur.execute("SELECT key, value FROM index_metadata").fetchall()
        }
    finally:
        db.close()


def _decoded_manifest(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _validated_manifest(raw: Any) -> dict[str, Any] | None:
    decoded = _decoded_manifest(raw)
    if decoded is None:
        return None
    try:
        return load_scan_manifest(
            {"scan_manifest": json.dumps(decoded, ensure_ascii=False)}
        )
    except ScanManifestError:
        return None


def _validated_public_manifest(raw: Any) -> dict[str, Any] | None:
    manifest = _decoded_manifest(raw)
    if manifest is None or manifest.get("manifest_version") != SCAN_MANIFEST_VERSION:
        return None
    if not isinstance(manifest.get("scan_id"), str) or not str(manifest["scan_id"]).strip():
        return None
    try:
        index_kind = normalized_index_kind(manifest.get("index_kind"), default="")
    except ScanManifestError:
        return None
    if not index_kind:
        return None
    output = manifest.get("output")
    if not isinstance(output, dict) or not all(
        isinstance(output.get(key), str) and str(output[key]).strip()
        for key in ("db", "json")
    ):
        return None
    return manifest


def _file_identity_state(path: Path) -> dict[str, str]:
    file_stat = path.stat()
    return {
        "device": str(file_stat.st_dev),
        "inode": str(file_stat.st_ino),
        "size": str(file_stat.st_size),
        "mtime_ns": str(file_stat.st_mtime_ns),
    }


def _unowned_file_state(path: Path) -> dict[str, str]:
    return {"kind": "unowned", **_file_identity_state(path)}


def snapshot_database_state(db_path: str | Path) -> dict[str, str]:
    path = Path(db_path).resolve()
    if not path.exists():
        return {"kind": "absent"}
    try:
        metadata = _read_existing_metadata(path)
    except sqlite3.DatabaseError:
        return _unowned_file_state(path)
    raw_manifest = metadata.get("scan_manifest", "")
    manifest = _validated_manifest(raw_manifest)
    if str(raw_manifest).strip():
        if manifest is None or not str(manifest.get("scan_id", "")).strip():
            return {"kind": "invalid_manifest"}
        return {"kind": "manifest", "scan_id": str(manifest["scan_id"])}
    if _has_legacy_mdex_schema(path, metadata):
        return {
            "kind": "legacy",
            "generated": metadata.get("generated", ""),
            "scan_root": metadata.get("scan_root", ""),
            "scan_roots": metadata.get("scan_roots", ""),
            **_file_identity_state(path),
        }
    return _unowned_file_state(path)


def _validate_expected_database_state(
    db_path: Path,
    expected: dict[str, str],
) -> None:
    current = snapshot_database_state(db_path)
    if current != expected:
        raise ValueError(
            "scan plan became stale before write; the database changed while the index was being built"
        )


def _legacy_outputs_match(metadata: dict[str, str], payload: dict[str, Any]) -> bool:
    if not metadata:
        return False
    if str(payload.get("generated", "")) != metadata.get("generated", ""):
        return False
    try:
        db_roots = json.loads(metadata.get("scan_roots", "[]"))
    except json.JSONDecodeError:
        return False
    return payload.get("scan_roots") == db_roots


def _has_legacy_mdex_schema(db_path: Path, metadata: dict[str, str]) -> bool:
    if not db_path.exists() or not has_legacy_mdex_metadata(metadata):
        return False
    db = sqlite3.connect(str(db_path))
    try:
        return has_mdex_schema(db)
    except sqlite3.DatabaseError:
        return False
    finally:
        db.close()


def _validate_database_replacement_ownership(db_path: Path) -> None:
    if not db_path.exists():
        return
    metadata = _read_existing_metadata(db_path)
    raw_manifest = metadata.get("scan_manifest", "")
    if str(raw_manifest).strip():
        if _decoded_manifest(raw_manifest) is None:
            raise ValueError(f"refusing to replace database with an invalid scan manifest: {db_path}")
        raise ValueError("manifest-backed database must be updated with write_scan_outputs")
    if not _has_legacy_mdex_schema(db_path, metadata):
        raise ValueError(f"refusing to replace an existing unowned SQLite database: {db_path}")


def _json_recovery_identity(json_output: Path, payload: bytes) -> dict[str, str]:
    return {
        "json": json_output.resolve().as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _json_matches_recovery_identity(
    json_output: Path,
    payload: bytes,
    identity: Any,
) -> bool:
    if not isinstance(identity, dict):
        return False
    try:
        return (
            canonical_path_key(str(identity.get("json", "")))
            == canonical_path_key(json_output)
            and str(identity.get("sha256", "")) == hashlib.sha256(payload).hexdigest()
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _manifest_output_pair(manifest: dict[str, Any]) -> tuple[Path, Path] | None:
    output = manifest.get("output")
    if not isinstance(output, dict):
        return None
    db_value = str(output.get("db", "")).strip()
    json_value = str(output.get("json", "")).strip()
    if not db_value or not json_value:
        return None
    return Path(db_value).resolve(), Path(json_value).resolve()


def _manifests_name_same_pair(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_pair = _manifest_output_pair(first)
    second_pair = _manifest_output_pair(second)
    if first_pair is None or second_pair is None:
        return False
    return all(
        canonical_path_key(first_path) == canonical_path_key(second_path)
        for first_path, second_path in zip(first_pair, second_pair)
    )


def _manifest_names_output_pair(
    manifest: dict[str, Any],
    db_output: Path,
    json_output: Path,
) -> bool:
    output = manifest.get("output")
    if not isinstance(output, dict):
        return False
    try:
        manifest_db = Path(str(output.get("db", ""))).resolve()
        manifest_json = Path(str(output.get("json", ""))).resolve()
    except (OSError, RuntimeError):
        return False
    return (
        canonical_path_key(manifest_db) == canonical_path_key(db_output)
        and canonical_path_key(manifest_json) == canonical_path_key(json_output)
    )


def _validate_scan_output_ownership(
    index: dict[str, Any],
    db_output: Path,
    json_output: Path,
) -> dict[str, str] | None:
    incoming_manifest = scan_manifest_from_index(index)
    if incoming_manifest is None:
        raise ValueError("scan manifest is required when writing DB and JSON outputs")

    db_exists = db_output.exists()
    metadata = _read_existing_metadata(db_output)
    raw_db_manifest = metadata.get("scan_manifest", "")
    db_manifest = _validated_manifest(raw_db_manifest)
    if str(raw_db_manifest).strip() and db_manifest is None:
        raise ValueError(f"refusing to overwrite database with an invalid scan manifest: {db_output}")
    legacy_db = db_exists and db_manifest is None and _has_legacy_mdex_schema(db_output, metadata)
    if db_exists and db_manifest is None and not legacy_db:
        raise ValueError(f"refusing to overwrite an existing unowned SQLite database: {db_output}")

    if not json_output.exists():
        if db_manifest is not None:
            existing_pair = _manifest_output_pair(db_manifest)
            if existing_pair is None or (
                canonical_path_key(existing_pair[0]) != canonical_path_key(db_output)
                or canonical_path_key(existing_pair[1]) != canonical_path_key(json_output)
            ):
                raise ValueError(
                    f"refusing to rebind a copied or relocated database without its JSON pair: {db_output}"
                )
        return None

    try:
        json_bytes = json_output.read_bytes()
        json_payload = json.loads(json_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"refusing to overwrite an existing unowned JSON output: {json_output}"
        ) from exc
    if not isinstance(json_payload, dict):
        json_payload = {}

    json_manifest = _validated_public_manifest(json_payload.get("scan_manifest"))
    if db_manifest is not None:
        if json_manifest is None:
            pending_identity = db_manifest.get("previous_json_identity")
            if _manifest_names_output_pair(db_manifest, db_output, json_output) and (
                _json_matches_recovery_identity(json_output, json_bytes, pending_identity)
            ):
                return dict(pending_identity)
            raise ValueError(
                f"refusing to overwrite JSON output not owned by this scan database: {json_output}"
            )
        db_scan_id = str(db_manifest.get("scan_id", ""))
        json_scan_id = str(json_manifest.get("scan_id", ""))
        if not db_scan_id or not json_scan_id:
            raise ValueError(
                f"refusing to overwrite outputs with an invalid scan ownership ID: {json_output}"
            )
        if db_scan_id != json_scan_id:
            if not _manifest_names_output_pair(db_manifest, db_output, json_output) or not (
                _manifest_names_output_pair(json_manifest, db_output, json_output)
            ):
                raise ValueError(
                    f"refusing to overwrite JSON output owned by a different scan database: {json_output}"
                )
            return None

        if not _manifests_name_same_pair(db_manifest, json_manifest):
            raise ValueError(
                f"refusing to overwrite JSON output whose manifests name different output pairs: {json_output}"
            )
        previous_pair = _manifest_output_pair(db_manifest)
        if previous_pair is None:
            raise ValueError(f"refusing to overwrite outputs with an invalid ownership pair: {json_output}")
        db_still_bound = canonical_path_key(previous_pair[0]) == canonical_path_key(db_output)
        json_still_bound = canonical_path_key(previous_pair[1]) == canonical_path_key(json_output)
        if db_still_bound != json_still_bound:
            raise ValueError(
                f"refusing to update only one side of a copied or relocated output pair: {json_output}"
            )
        return None

    if json_manifest is not None:
        raise ValueError(
            f"refusing to overwrite JSON output owned by a different scan database: {json_output}"
        )
    if legacy_db and _legacy_outputs_match(metadata, json_payload):
        return _json_recovery_identity(json_output, json_bytes)
    raise ValueError(f"refusing to overwrite an existing unowned JSON output: {json_output}")


def _validate_manifest_config_identity(index: dict[str, Any]) -> None:
    manifest = scan_manifest_from_index(index)
    identity = manifest.get("config_identity") if isinstance(manifest, dict) else None
    if not isinstance(identity, dict):
        raise ValueError("scan manifest config identity is missing")
    config_path = str(identity.get("path", "")).strip()
    expected_hash = str(identity.get("sha256", "")).strip()
    if not config_path or not expected_hash:
        raise ValueError("scan manifest config identity is invalid")
    if config_file_hash(config_path) != expected_hash:
        raise ValueError("scan configuration changed while the index was being built; retry mdex scan")


def _write_sqlite_unlocked(index: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    nodes = _normalize_nodes(index)
    edges = _normalize_edges(index)
    indexed_node_ids = {str(node.get("id", "")).strip() for node in nodes if str(node.get("id", "")).strip()}
    overrides = _load_existing_overrides(output)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.stem}.",
        suffix=".tmp",
        dir=str(output.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        db = sqlite3.connect(str(temp_path))
        try:
            cur = db.cursor()
            cur.execute("BEGIN")
            _create_schema(cur)
            _restore_node_overrides(cur, overrides, indexed_node_ids)
            _insert_nodes(cur, nodes)
            _insert_edges(cur, edges)
            _insert_metadata(cur, index)
            _create_indexes(cur)

            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)


def write_sqlite(
    index: dict[str, Any],
    db_path: str,
    *,
    lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
) -> None:
    if scan_manifest_from_index(index) is not None:
        raise ValueError("manifest-backed database must be written with write_scan_outputs")
    ensure_scan_output_resource(db_path)
    output = validated_mutable_resource(db_path, label="database output")
    with exclusive_db_lock(output, timeout=lock_timeout):
        output = validated_mutable_resource(output, label="database output")
        _validate_database_replacement_ownership(output)
        _write_sqlite_unlocked(index, output)


def write_scan_outputs(
    index: dict[str, Any],
    db_path: str,
    json_path: str,
    *,
    lock_timeout: float = DEFAULT_DB_LOCK_TIMEOUT_SECONDS,
    expected_previous_scan_id: str | None = None,
    expected_database_state: dict[str, str] | None = None,
    expected_root_identities: list[dict[str, Any]] | None = None,
) -> None:
    """Replace a scan's DB/JSON pair while holding locks for both outputs."""

    db_output = validated_mutable_resource(db_path, label="database output")
    json_output = validated_mutable_resource(json_path, label="JSON output")
    ensure_distinct_scan_outputs(db_output, json_output)
    incoming_manifest = scan_manifest_from_index(index)
    if incoming_manifest is None or not _manifest_names_output_pair(
        incoming_manifest,
        db_output,
        json_output,
    ):
        raise ValueError("scan manifest output pair does not match the requested DB and JSON paths")
    db_written = False
    json_written = False
    with exclusive_resource_locks((db_output, json_output), timeout=lock_timeout):
        try:
            db_output = validated_mutable_resource(db_output, label="database output")
            json_output = validated_mutable_resource(json_output, label="JSON output")
            if expected_root_identities is not None:
                validate_directory_identities(expected_root_identities)
            _validate_manifest_config_identity(index)
            if expected_database_state is not None:
                _validate_expected_database_state(db_output, expected_database_state)
            elif expected_previous_scan_id is not None:
                metadata = _read_existing_metadata(db_output)
                current_manifest = _decoded_manifest(metadata.get("scan_manifest"))
                current_scan_id = (
                    str(current_manifest.get("scan_id", ""))
                    if current_manifest is not None
                    else ""
                )
                if current_scan_id != expected_previous_scan_id:
                    raise ValueError(
                        "scan plan became stale before write; the database was rescanned concurrently"
                    )
            previous_json_identity = _validate_scan_output_ownership(
                index,
                db_output,
                json_output,
            )
            if previous_json_identity is not None:
                manifest = scan_manifest_from_index(index)
                if manifest is None:
                    raise ValueError("scan manifest disappeared before output write")
                manifest["previous_json_identity"] = previous_json_identity
                set_scan_manifest(index, manifest)
            _write_sqlite_unlocked(index, db_output)
            db_written = True
            _write_json_unlocked(index, json_output)
            json_written = True
        except Exception as exc:
            raise ScanOutputsWriteError(
                str(exc),
                db_path=str(db_output),
                json_path=str(json_output),
                db_written=db_written,
                json_written=json_written,
            ) from exc
