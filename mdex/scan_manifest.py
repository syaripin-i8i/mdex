from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping


SCAN_MANIFEST_VERSION = 1
SUPPORTED_DOCUMENT_INDEX_KINDS = {"repo", "task", "memory"}


class ScanManifestError(ValueError):
    pass


def canonical_config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def config_file_hash(config_path: str | Path) -> str:
    path = Path(config_path)
    payload = path.read_bytes() if path.exists() else b""
    return hashlib.sha256(payload).hexdigest()


def normalized_index_kind(value: Any, *, default: str = "repo") -> str:
    kind = str(value or default).strip().lower()
    if not kind:
        kind = default
    if not all(character.isalnum() or character in {"-", "_"} for character in kind):
        raise ScanManifestError(f"invalid index_kind: {kind}")
    return kind


def build_scan_manifest(
    *,
    repo_root: str | Path,
    scan_roots: Iterable[str | Path],
    node_id_root: str | Path,
    config_path: str | Path,
    config: Mapping[str, Any],
    db_output: str | Path,
    output_json: str | Path,
    output_origin: str,
    index_kind: str,
    canonicalize_scan_roots: bool = True,
    config_file_sha256: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    resolved_config = Path(config_path).resolve()
    resolved_db = Path(db_output).resolve()
    resolved_json = Path(output_json).resolve()
    root_values = (
        [Path(path).resolve().as_posix() for path in scan_roots]
        if canonicalize_scan_roots
        else [str(path) for path in scan_roots]
    )
    return {
        "manifest_version": SCAN_MANIFEST_VERSION,
        "scan_id": str(uuid.uuid4()),
        "repo_root": root.as_posix(),
        "scan_roots": root_values,
        "node_id_root": Path(node_id_root).resolve().as_posix(),
        "config_path": resolved_config.as_posix(),
        "config_hash": canonical_config_hash(config),
        "config_identity": {
            "path": resolved_config.as_posix(),
            "sha256": config_file_sha256 or config_file_hash(resolved_config),
        },
        "output_json": resolved_json.as_posix(),
        "output": {
            "db": resolved_db.as_posix(),
            "json": resolved_json.as_posix(),
        },
        "output_origin": str(output_origin).strip() or "unknown",
        "index_kind": normalized_index_kind(index_kind),
        "strict": bool(strict),
    }


def set_scan_manifest(index: dict[str, Any], manifest: Mapping[str, Any]) -> None:
    value = dict(manifest)
    try:
        setattr(index, "scan_manifest", value)
    except (AttributeError, TypeError):
        index["_scan_manifest"] = value


def scan_manifest_from_index(index: Mapping[str, Any]) -> dict[str, Any] | None:
    value = getattr(index, "scan_manifest", None)
    if not isinstance(value, dict):
        value = index.get("_scan_manifest")
    return dict(value) if isinstance(value, dict) else None


def load_scan_manifest(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw = metadata.get("scan_manifest")
    if not isinstance(raw, str) or not raw.strip():
        raise ScanManifestError(
            "scan manifest is missing; run mdex scan explicitly before using finish --scan"
        )
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScanManifestError("scan manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ScanManifestError("scan manifest must be a JSON object")

    required_strings = (
        "scan_id",
        "repo_root",
        "node_id_root",
        "config_path",
        "config_hash",
        "output_json",
        "output_origin",
        "index_kind",
    )
    if manifest.get("manifest_version") != SCAN_MANIFEST_VERSION:
        raise ScanManifestError("unsupported scan manifest version")
    for key in required_strings:
        if not isinstance(manifest.get(key), str) or not str(manifest[key]).strip():
            raise ScanManifestError(f"scan manifest field is missing or invalid: {key}")
    roots = manifest.get("scan_roots")
    if not isinstance(roots, list) or not roots or not all(
        isinstance(item, str) and item.strip() for item in roots
    ):
        raise ScanManifestError("scan manifest field is missing or invalid: scan_roots")
    identity = manifest.get("config_identity")
    if not isinstance(identity, dict) or not all(
        isinstance(identity.get(key), str) and str(identity[key]).strip()
        for key in ("path", "sha256")
    ):
        raise ScanManifestError("scan manifest field is missing or invalid: config_identity")
    output = manifest.get("output")
    if not isinstance(output, dict) or not all(
        isinstance(output.get(key), str) and str(output[key]).strip()
        for key in ("db", "json")
    ):
        raise ScanManifestError("scan manifest field is missing or invalid: output")
    if not isinstance(manifest.get("strict"), bool):
        raise ScanManifestError("scan manifest field is missing or invalid: strict")
    normalized_index_kind(manifest["index_kind"])
    return manifest
