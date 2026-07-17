from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mdex import health


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict[str, object]:
    return {
        "scan_id": "scan-1",
        "config_hash": "sha256:config",
        "index_kind": "repo",
    }


def _metadata(generated: str) -> dict[str, str]:
    return {"generated": generated, "scan_manifest": "{}"}


def _source_state(status: str, reason: str) -> dict[str, str]:
    return {
        "status": status,
        "reason": reason,
        "scan_id": "scan-1",
        "config_hash": "sha256:config",
    }


def _evaluate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    generated: str,
    source_status: str = "fresh",
    source_reason: str = "source_state_matches_scan",
    borrowed: bool = False,
) -> dict[str, object]:
    db_path = tmp_path / "index.db"
    db_path.touch()
    monkeypatch.setattr(health, "list_index_metadata", lambda _path: _metadata(generated))
    monkeypatch.setattr(health, "load_scan_manifest", lambda _metadata: _manifest())
    monkeypatch.setattr(
        health,
        "verify_manifest_source_state",
        lambda _manifest, _metadata: _source_state(source_status, source_reason),
    )
    return health.evaluate_index_health(db_path, borrowed=borrowed)


def test_timestamp_fresh_and_fingerprint_match_is_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _evaluate(
        tmp_path,
        monkeypatch,
        generated=datetime.now(timezone.utc).isoformat(),
    )
    assert result["status"] == "healthy"
    assert result["reusable"] is True
    assert result["reason"] == "index_reusable"


def test_health_schema_reason_codes_match_evaluator_contract() -> None:
    schema = json.loads(
        (PROJECT_ROOT / "schemas" / "health.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["properties"]["status"]["enum"]) == health.HEALTH_STATUSES
    assert set(schema["properties"]["reason"]["enum"]) == health.HEALTH_REASON_CODES


def test_source_mismatch_outranks_fresh_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _evaluate(
        tmp_path,
        monkeypatch,
        generated=datetime.now(timezone.utc).isoformat(),
        source_status="stale",
        source_reason="source_fingerprint_mismatch",
    )
    assert result["age_hours"] <= 1
    assert result["status"] == "stale"
    assert result["reusable"] is False
    assert result["reason"] == "source_fingerprint_mismatch"


def test_matching_source_with_old_timestamp_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _evaluate(
        tmp_path,
        monkeypatch,
        generated=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
    )
    assert result["source_state"]["status"] == "fresh"
    assert result["status"] == "stale"
    assert result["reusable"] is False
    assert result["reason"] == "index_age_exceeded"


def test_missing_or_invalid_manifest_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "index.db"
    db_path.touch()
    generated = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(health, "list_index_metadata", lambda _path: {"generated": generated})
    missing = health.evaluate_index_health(db_path)
    assert missing["status"] == "unavailable"
    assert missing["reusable"] is False
    assert missing["reason"] == "scan_manifest_missing"

    monkeypatch.setattr(
        health,
        "list_index_metadata",
        lambda _path: {"generated": generated, "scan_manifest": "not-json"},
    )
    invalid = health.evaluate_index_health(db_path)
    assert invalid["status"] == "unavailable"
    assert invalid["reusable"] is False
    assert invalid["reason"] == "scan_manifest_invalid"


def test_borrowed_worktree_index_is_never_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _evaluate(
        tmp_path,
        monkeypatch,
        generated=datetime.now(timezone.utc).isoformat(),
        borrowed=True,
    )
    assert result["source_state"]["status"] == "fresh"
    assert result["status"] == "stale"
    assert result["reusable"] is False
    assert result["reason"] == "worktree_borrowed_index"


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.pop("MDEX_DB", None)
    return subprocess.run(
        [sys.executable, "-m", "mdex.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def test_all_agent_commands_share_non_reusable_health_after_source_change(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / ".mdex").mkdir(parents=True)
    (repo / "control").mkdir()
    (repo / "docs").mkdir()
    (repo / ".mdex" / "config.json").write_text(
        json.dumps({"db": ".mdex/index.db"}), encoding="utf-8"
    )
    scan_config = repo / "control" / "scan.json"
    scan_config.write_text(
        json.dumps(
            {
                "include_extensions": [".md"],
                "exclude_patterns": [".mdex/**"],
                "use_default_exclude_patterns": True,
            }
        ),
        encoding="utf-8",
    )
    source = repo / "docs" / "a.md"
    source.write_text("# Alpha\n\nhealth authority\n", encoding="utf-8")
    db_path = repo / ".mdex" / "index.db"
    json_path = repo / ".mdex" / "index.json"
    scan = _run_cli(
        "scan",
        "--root",
        ".",
        "--config",
        str(scan_config),
        "--db",
        str(db_path),
        "--output",
        str(json_path),
        cwd=repo,
    )
    assert scan.returncode == 0, scan.stderr

    source.write_text("# Alpha\n\nhealth authority changed\n", encoding="utf-8")
    calls = {
        "doctor": ("doctor", "--db", str(db_path), "--json-index", str(json_path)),
        "status": ("status", "--db", str(db_path), "--json-index", str(json_path)),
        "context": ("context", "health", "--db", str(db_path), "--actionable"),
        "start": ("start", "health", "--db", str(db_path), "--for-agent", "worker"),
    }
    payloads: dict[str, dict[str, object]] = {}
    for name, args in calls.items():
        result = _run_cli(*args, cwd=repo)
        assert result.returncode == 0, result.stderr
        payloads[name] = json.loads(result.stdout)

    summaries = {
        (
            str(payload["health"]["status"]),
            bool(payload["health"]["reusable"]),
            str(payload["health"]["reason"]),
            str(payload["health"]["scan_id"]),
            str(payload["health"]["config_hash"]),
            str(payload["health"]["source_state"]["status"]),
            str(payload["health"]["generated"]),
        )
        for payload in payloads.values()
    }
    assert len(summaries) == 1
    summary = next(iter(summaries))
    assert summary[0:3] == ("stale", False, "source_fingerprint_mismatch")

    start = payloads["start"]
    assert start["index_status"]["fresh"] is False
    assert start["evidence_identity"]["reusable"] is False
    assert start["entrypoint_reason"] != "ranked_entrypoint_available"
    assert "run mdex scan" in start["recommended_next_actions"]
    assert start["agent_prompt_pack"]["health"]["reusable"] is False
    assert "unverified" in start["agent_prompt_pack"]["prompt"]
    assert all(
        item.get("evidence_use") == "unverified_non_reusable"
        for item in start["recommended_read_order"]
    )


def test_task_only_commands_preserve_one_lane_health_identity(tmp_path: Path) -> None:
    repo = tmp_path / "task_repo"
    (repo / ".mdex").mkdir(parents=True)
    (repo / "control").mkdir()
    (repo / "tasks").mkdir()
    task_db = repo / ".mdex" / "task.db"
    task_json = repo / ".mdex" / "task.json"
    (repo / ".mdex" / "config.json").write_text(
        json.dumps({"indexes": {"task": {"db": ".mdex/task.db"}}}),
        encoding="utf-8",
    )
    scan_config = repo / "control" / "task_scan.json"
    scan_config.write_text(
        json.dumps(
            {
                "index_kind": "task",
                "include_extensions": [".md"],
                "exclude_patterns": [],
            }
        ),
        encoding="utf-8",
    )
    source = repo / "tasks" / "T1.md"
    source.write_text("# Task One\n\ntask-only health\n", encoding="utf-8")
    scan = _run_cli(
        "scan",
        "--root",
        "tasks",
        "--node-id-root",
        ".",
        "--config",
        str(scan_config),
        "--db",
        str(task_db),
        "--output",
        str(task_json),
        cwd=repo,
    )
    assert scan.returncode == 0, scan.stderr
    source.write_text("# Task One\n\ntask-only health changed\n", encoding="utf-8")

    calls = {
        "doctor": ("doctor", "--db", str(task_db), "--json-index", str(task_json)),
        "status": ("status", "--db", str(task_db), "--include", "task"),
        "context": ("context", "task-only", "--db", str(task_db), "--include", "task", "--actionable"),
        "start": ("start", "task-only", "--db", str(task_db), "--include", "task"),
    }
    payloads: dict[str, dict[str, object]] = {}
    for name, args in calls.items():
        result = _run_cli(*args, cwd=repo)
        assert result.returncode == 0, result.stderr
        payloads[name] = json.loads(result.stdout)
    identities = {
        (
            payload["health"]["status"],
            payload["health"]["reusable"],
            payload["health"]["reason"],
            payload["health"]["scan_id"],
            payload["health"]["config_hash"],
        )
        for payload in payloads.values()
    }
    assert len(identities) == 1
    assert next(iter(identities))[0:3] == (
        "stale",
        False,
        "source_fingerprint_mismatch",
    )
    start = payloads["start"]
    assert start["entrypoint_reason"] == "multi_index_not_reusable"
    assert "run mdex scan" in start["recommended_next_actions"]
    assert all(
        item.get("evidence_use") == "unverified_non_reusable"
        for item in start["recommended_read_order"]
    )
