from __future__ import annotations

import json

from mdex import source_freshness
from mdex.scan_manifest import canonical_config_hash


def _manifest(config_hash: str) -> dict[str, object]:
    return {
        "scan_id": "scan-1",
        "config_hash": config_hash,
        "config_path": "/repo/.mdex/config.json",
        "config_identity": {"sha256": "file-1"},
        "scan_roots": ["/repo/docs"],
        "node_id_root": "/repo",
        "strict": False,
    }


def test_source_state_is_fresh_only_when_config_and_fingerprints_match(monkeypatch) -> None:
    config = {"extensions": [".md"]}
    fingerprints = {"docs/a.md": {"sha256": "abc", "size": 3}}
    monkeypatch.setattr(
        source_freshness, "load_scan_config_with_identity", lambda *_a, **_k: (config, "file-1")
    )
    monkeypatch.setattr(source_freshness.Path, "is_dir", lambda _self: True)
    monkeypatch.setattr(
        source_freshness, "build_index", lambda *_a, **_k: {"fingerprints": fingerprints}
    )

    result = source_freshness.verify_manifest_source_state(
        _manifest(canonical_config_hash(config)), {"fingerprints": json.dumps(fingerprints)}
    )

    assert result["status"] == "fresh"
    assert result["reason"] == "source_state_matches_scan"
    assert result["scan_id"] == "scan-1"
    assert result["config_hash"] == canonical_config_hash(config)
    assert result["stored_fingerprint_hash"] == result["current_fingerprint_hash"]


def test_source_state_is_stale_when_current_source_differs(monkeypatch) -> None:
    config: dict[str, object] = {}
    monkeypatch.setattr(
        source_freshness, "load_scan_config_with_identity", lambda *_a, **_k: (config, "file-1")
    )
    monkeypatch.setattr(source_freshness.Path, "is_dir", lambda _self: True)
    monkeypatch.setattr(
        source_freshness,
        "build_index",
        lambda *_a, **_k: {"fingerprints": {"docs/a.md": {"sha256": "new"}}},
    )

    result = source_freshness.verify_manifest_source_state(
        _manifest(canonical_config_hash(config)),
        {"fingerprints": json.dumps({"docs/a.md": {"sha256": "old"}})},
    )

    assert result["status"] == "stale"
    assert result["reason"] == "source_fingerprint_mismatch"
    assert result["scan_id"] == "scan-1"


def test_source_state_fails_closed_when_verification_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        source_freshness,
        "load_scan_config_with_identity",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("missing config")),
    )

    result = source_freshness.verify_manifest_source_state(
        _manifest("sha256:expected"), {"fingerprints": "{}"}
    )

    assert result == {
        "status": "unknown",
        "reason": "source_state_verification_failed",
        "scan_id": "scan-1",
        "config_hash": "sha256:expected",
        "detail": "missing config",
    }
