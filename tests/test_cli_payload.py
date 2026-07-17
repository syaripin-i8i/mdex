from __future__ import annotations

from mdex import cli


def test_emit_payload_compact_and_pretty(capsys) -> None:
    payload = {"key": "value", "count": 1}

    cli._emit_payload(payload, pretty=False)
    compact = capsys.readouterr().out.strip()
    assert compact == '{"key":"value","count":1}'

    cli._emit_payload(payload, pretty=True)
    pretty = capsys.readouterr().out
    assert '"key": "value"' in pretty
    assert "\n" in pretty


def test_emit_payload_supports_stderr(capsys) -> None:
    cli._emit_payload({"error": "failure"}, stderr=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == '{"error":"failure"}'


def test_context_evidence_identity_exposes_verified_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "evaluate_index_health",
        lambda _path, **kwargs: {
            "status": "stale" if kwargs.get("borrowed") else "healthy",
            "reusable": not kwargs.get("borrowed", False),
            "reason": "worktree_borrowed_index" if kwargs.get("borrowed") else "index_reusable",
            "scan_id": "generation-1",
            "config_hash": "config-1",
            "index_kind": "repo",
            "source_state": {
                "status": "fresh",
                "reason": "source_state_matches_scan",
                "scan_id": "generation-1",
                "config_hash": "config-1",
                "stored_fingerprint_hash": "sha256:same",
                "current_fingerprint_hash": "sha256:same",
            },
            "generated": "2026-07-17T00:00:00+00:00",
            "age_hours": 1.0,
        },
    )
    identity = cli._context_evidence_identity("index.db")
    assert identity == {
        "status": "verified",
        "reusable": True,
        "reason": "index_reusable",
        "scan_id": "generation-1",
        "config_hash": "config-1",
        "index_kind": "repo",
        "source_state": {
            "status": "fresh",
            "reason": "source_state_matches_scan",
            "scan_id": "generation-1",
            "config_hash": "config-1",
            "stored_fingerprint_hash": "sha256:same",
            "current_fingerprint_hash": "sha256:same",
        },
    }

    borrowed = cli._context_evidence_identity("index.db", borrowed=True)
    assert borrowed["status"] == "identified"
    assert borrowed["reusable"] is False
    assert borrowed["reason"] == "worktree_borrowed_index"
    assert borrowed["source_state"]["status"] == "fresh"


def test_resolve_scan_json_path_fails_closed_for_borrowed_db_without_common_root(tmp_path) -> None:
    # A borrowed db must never fall back to guessing a JSON confinement root:
    # without the recorded main checkout root there is no JSON path at all.
    db_info = {
        "path": str(tmp_path / "main" / ".mdex" / "mdex_index.db"),
        "source": "worktree_common_root",
        "repo_root": str(tmp_path / "worktree"),
        "config": {},
    }

    assert cli._resolve_scan_json_path(db_info, None) is None
