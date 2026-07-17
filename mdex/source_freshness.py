from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from mdex.builder import collect_source_fingerprints
from mdex.scan_config import load_scan_config_with_identity
from mdex.scan_manifest import canonical_config_hash


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def verify_manifest_source_state(
    manifest: Mapping[str, Any], metadata: Mapping[str, str]
) -> dict[str, Any]:
    """Recompute scan inputs and bind the result to one stored scan generation."""
    scan_id = str(manifest.get("scan_id", ""))
    expected_config_hash = str(manifest.get("config_hash", ""))
    binding = {"scan_id": scan_id, "config_hash": expected_config_hash}

    try:
        stored_fingerprints = json.loads(metadata.get("fingerprints", ""))
        if not isinstance(stored_fingerprints, dict):
            raise ValueError("stored fingerprints are not an object")

        config_path = Path(str(manifest["config_path"]))
        config, config_file_sha256 = load_scan_config_with_identity(config_path, optional=False)
        current_config_hash = canonical_config_hash(config)
        if current_config_hash != expected_config_hash:
            return {
                "status": "stale",
                "reason": "config_hash_mismatch",
                **binding,
                "current_config_hash": current_config_hash,
            }

        config_identity = manifest.get("config_identity")
        if not isinstance(config_identity, dict) or (
            str(config_identity.get("sha256", "")) != config_file_sha256
        ):
            return {
                "status": "stale",
                "reason": "config_file_identity_mismatch",
                **binding,
                "current_config_hash": current_config_hash,
            }

        scan_roots = [Path(str(value)) for value in manifest["scan_roots"]]
        node_id_root = Path(str(manifest["node_id_root"]))
        if any(not path.is_dir() for path in [*scan_roots, node_id_root]):
            return {"status": "stale", "reason": "scan_input_missing", **binding}

        current_fingerprints = collect_source_fingerprints(
            scan_roots,
            config,
            node_id_root=node_id_root,
        )
        if not isinstance(current_fingerprints, dict):
            raise ValueError("current fingerprints are not an object")

        stored_hash = _canonical_hash(stored_fingerprints)
        current_hash = _canonical_hash(current_fingerprints)
        if current_fingerprints != stored_fingerprints:
            return {
                "status": "stale",
                "reason": "source_fingerprint_mismatch",
                **binding,
                "stored_fingerprint_hash": stored_hash,
                "current_fingerprint_hash": current_hash,
            }
        return {
            "status": "fresh",
            "reason": "source_state_matches_scan",
            **binding,
            "stored_fingerprint_hash": stored_hash,
            "current_fingerprint_hash": current_hash,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "reason": "source_state_verification_failed",
            **binding,
            "detail": str(exc),
        }
