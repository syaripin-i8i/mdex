from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mdex import artifacts
from mdex.artifacts import build_artifacts_index
from mdex.builder import build_index
from mdex.context import select_context
from mdex.indexer import write_sqlite
from mdex.multiindex import build_multi_context_payload, build_multi_start_payload
from mdex.store import list_nodes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT)
        if not existing_pythonpath
        else f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
    )
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


def test_artifacts_index_extracts_metadata_and_actionable_digest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outputs = repo / "outputs"
    attribution_dir = outputs / "attribution"
    attribution_dir.mkdir(parents=True)
    raw_logs = outputs / "raw_logs"
    raw_logs.mkdir()
    runtime_state = outputs / "runtime_state"
    runtime_state.mkdir()
    now = datetime.now(timezone.utc).isoformat()

    (attribution_dir / "CDEV.24_attribution.json").write_text(
        json.dumps(
            {
                "generated_at": now,
                "status": "warn",
                "summary": "CDEV.24 attribution report says hard gates dominated candidate selection",
                "p95": 7.5,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (raw_logs / "ignored.json").write_text('{"summary":"should not be indexed"}\n', encoding="utf-8")
    (runtime_state / "live.json").write_text('{"summary":"live state should not be indexed"}\n', encoding="utf-8")

    index = build_artifacts_index([outputs], {"stale_after_days": 9999}, node_id_root=repo)
    assert [node["id"] for node in index["nodes"]] == ["outputs/attribution/CDEV.24_attribution.json"]

    db_path = tmp_path / "artifacts.db"
    write_sqlite(index, str(db_path))
    rows = list_nodes(str(db_path))
    assert rows[0]["metadata"]["kind"] == "attribution"
    assert rows[0]["metadata"]["generated_at"] == now

    payload = select_context("CDEV.24 attribution candidate selection", str(db_path), actionable=True)
    assert payload["nodes"][0]["metadata"]["kind"] == "attribution"
    assert payload["nodes"][0]["freshness"]["stale"] is False
    digest = payload["actionable_digest"]
    assert digest["relevant_artifacts"]
    assert digest["relevant_artifacts"][0]["id"] == "outputs/attribution/CDEV.24_attribution.json"
    assert digest["relevant_artifacts"][0]["freshness"]["stale"] is False
    assert digest["relevant_docs"] == []


def test_artifacts_index_uses_filename_timestamp_and_jsonl_headline(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outputs = repo / "outputs"
    monitor_dir = outputs / "voice_monitor"
    monitor_dir.mkdir(parents=True)
    artifact = monitor_dir / "2026-07-08_voice_monitor.jsonl"
    artifact.write_text(
        json.dumps({"status": "old", "message": "earlier run should not be representative"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"status": "ok", "message": "voice monitor latency p95 observed"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    index = build_artifacts_index(
        [outputs],
        {"stale_after_days": 9999, "stale_after_days_by_kind": {"voice_monitor": 3}},
        node_id_root=repo,
    )
    node = index["nodes"][0]

    assert node["metadata"]["kind"] == "voice_monitor"
    assert node["metadata"]["generated_at"].startswith("2026-07-08T00:00:00")
    assert node["metadata"]["stale_after_days"] == 3
    assert node["status"] == "ok"
    assert "voice monitor latency" in node["title"]


def test_artifacts_index_warns_when_enumerated_file_disappears(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    outputs = repo / "outputs"
    outputs.mkdir(parents=True)
    disappearing = outputs / "disappearing.json"
    keep = outputs / "keep.json"
    disappearing.write_text('{"summary":"gone"}\n', encoding="utf-8")
    keep.write_text('{"summary":"kept artifact"}\n', encoding="utf-8")

    def fake_list_artifact_files(*_args, **_kwargs):
        disappearing.unlink()
        return [disappearing, keep]

    monkeypatch.setattr(artifacts, "_list_artifact_files", fake_list_artifact_files)

    index = build_artifacts_index([outputs], {}, node_id_root=repo)

    assert [node["id"] for node in index["nodes"]] == ["outputs/keep.json"]
    assert index["warnings"][0]["path"] == "outputs/disappearing.json"
    assert index["warnings"][0]["code"] == "file_disappeared"


def test_artifacts_index_skips_files_over_size_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outputs = repo / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "large.json").write_text('{"summary":"' + ("x" * 80) + '"}\n', encoding="utf-8")

    index = build_artifacts_index([outputs], {"max_file_size_bytes": 20}, node_id_root=repo)

    assert index["nodes"] == []
    assert index["warnings"][0]["code"] == "file_too_large"
    assert index["warnings"][0]["max_file_size_bytes"] == 20


def test_artifacts_jsonl_row_limit_keeps_latest_tail_rows(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outputs = repo / "outputs"
    outputs.mkdir(parents=True)
    artifact = outputs / "2026-07-08_eval_result.jsonl"
    artifact.write_text(
        "\n".join(
            [
                json.dumps({"status": "old", "summary": "old eval result"}),
                json.dumps({"status": "latest", "summary": "latest eval result"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    index = build_artifacts_index(
        [outputs],
        {"max_jsonl_rows_read": 1},
        node_id_root=repo,
    )

    assert index["nodes"][0]["title"] == "latest eval result"
    assert index["nodes"][0]["status"] == "latest"


def test_external_artifact_root_can_hide_source_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "private_memory"
    external.mkdir()
    (external / "note.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": "external root artifact",
            }
        ),
        encoding="utf-8",
    )

    index = build_artifacts_index(
        [
            {
                "path": str(external),
                "id_prefix": "agent/claude",
                "expose_source_root": False,
            }
        ],
        {},
        node_id_root=repo,
    )

    node = index["nodes"][0]
    assert node["id"] == "agent/claude/note.json"
    assert index["scan_roots"] == ["agent/claude"]
    assert "source_root" not in node["metadata"]
    assert "path" not in node["metadata"]
    assert external.as_posix() not in json.dumps(index, ensure_ascii=False)


def test_multi_index_context_merges_artifact_digest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    mdex_dir = repo / ".mdex"
    mdex_dir.mkdir()
    outputs = repo / "outputs"
    outputs.mkdir()
    (repo / "decision.md").write_text("# Decision\n\nCDEV.24 attribution policy\n", encoding="utf-8")
    (outputs / "CDEV.24_attribution.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "summary": "CDEV.24 attribution observation",
            }
        ),
        encoding="utf-8",
    )

    repo_db = mdex_dir / "mdex_index.db"
    artifact_db = mdex_dir / "artifacts.db"
    write_sqlite(build_index(repo, {"include_extensions": [".md"], "exclude_patterns": []}), str(repo_db))
    write_sqlite(build_artifacts_index([outputs], {"stale_after_days": 9999}, node_id_root=repo), str(artifact_db))

    payload = build_multi_context_payload(
        "CDEV.24 attribution",
        {"path": str(repo_db), "source": "arg", "repo_root": str(repo), "config": {}},
        include="repo,artifacts",
        budget=4000,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )

    assert set(payload["per_index_context"]) == {"repo", "artifacts"}
    artifact = payload["actionable_digest"]["relevant_artifacts"][0]
    assert artifact["index"] == "artifacts"
    assert artifact["metadata"]["kind"] == "attribution"
    assert any(row["index"] == "artifacts" for row in payload["nodes"])
    assert payload["multi_index"]["indexes"]["repo"]["path"] == ".mdex/mdex_index.db"
    assert payload["multi_index"]["indexes"]["artifacts"]["path"] == ".mdex/artifacts.db"
    assert repo.as_posix() not in json.dumps(payload, ensure_ascii=False)

    start_payload = build_multi_start_payload(
        "CDEV.24 attribution",
        {"path": str(repo_db), "source": "arg", "repo_root": str(repo), "config": {}},
        include="repo,artifacts",
        budget=4000,
        limit=4,
        include_content=False,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )

    assert start_payload["db"]["path"] == ".mdex/mdex_index.db"
    assert start_payload["multi_index"]["indexes"]["repo"]["path"] == ".mdex/mdex_index.db"
    assert start_payload["per_index_start"]["artifacts"]["db"]["path"] == ".mdex/artifacts.db"
    assert repo.as_posix() not in json.dumps(start_payload, ensure_ascii=False)


def test_old_decision_rows_are_marked_stale_but_authoritative(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "decision.md").write_text(
        """---
type: decision
updated: 2020-01-01
---
# Old Decision

The stable contract for artifact freshness.
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "repo.db"
    write_sqlite(build_index(repo, {"include_extensions": [".md"], "exclude_patterns": []}), str(db_path))

    payload = select_context("stable contract artifact freshness", str(db_path), actionable=True)

    assert payload["nodes"][0]["freshness"]["stale"] is True
    assert payload["nodes"][0]["freshness"]["status"] == "stale_but_authoritative"


def test_scan_artifacts_cli_writes_separate_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outputs = repo / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "audit.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "summary": "audit artifact for scan-artifacts",
            }
        ),
        encoding="utf-8",
    )
    (outputs / "broken.json").write_text("", encoding="utf-8")
    db_path = repo / ".mdex" / "artifacts.db"
    json_path = repo / ".mdex" / "artifacts.json"

    result = _run_cli(
        "scan-artifacts",
        "--root",
        str(outputs),
        "--db",
        str(db_path),
        "--output",
        str(json_path),
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["index_kind"] == "artifacts"
    assert payload["nodes"] == 2
    assert payload["warning_summary"]["total"] == 1
    assert payload["warning_summary"]["by_code"] == {"read_warning": 1}
    assert db_path.exists()
    assert json_path.exists()


def test_scan_artifacts_cli_accepts_private_root_specs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    mdex_dir = repo / ".mdex"
    mdex_dir.mkdir(parents=True)
    external = tmp_path / "private_memory"
    external.mkdir()
    (external / "memory.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": "private memory artifact",
            }
        ),
        encoding="utf-8",
    )
    (mdex_dir / "config.json").write_text(
        json.dumps(
            {
                "indexes": {
                    "artifacts": {
                        "roots": [
                            {
                                "path": str(external),
                                "id_prefix": "agent/codex",
                                "expose_source_root": False,
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = _run_cli("scan-artifacts", cwd=repo)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["roots"] == ["agent/codex"]
    artifact_json = (mdex_dir / "artifacts.json").read_text(encoding="utf-8")
    assert "agent/codex/memory.json" in artifact_json
    assert external.as_posix() not in artifact_json
