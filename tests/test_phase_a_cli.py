from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.builder import build_index
from runtime.indexer import write_sqlite
from runtime.store import get_node


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        env=merged_env,
    )


def _build_db(root: Path, config: dict[str, object], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    index = build_index(str(root), config)
    write_sqlite(index, str(db_path))


def _init_git_repo(root: Path) -> None:
    probe = subprocess.run(
        ["git", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("git not available")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    subprocess.run(
        ["git", "config", "user.email", "codex@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.name", "Codex"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def test_db_resolution_repo_default_list(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    result = _run_cli("list", cwd=quality_repo)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    ids = {row["id"] for row in payload}
    assert "design/root.md" in ids


def test_scan_default_outputs_to_dot_mdex(tmp_path: Path) -> None:
    repo = tmp_path / "scan_default_repo"
    repo.mkdir()
    (repo / "design.md").write_text(
        """---
type: design
status: active
updated: 2026-01-01
---
# Design

scan default output test
""",
        encoding="utf-8",
    )
    (repo / "control").mkdir(parents=True, exist_ok=True)
    _write_json(
        repo / "control" / "scan_config.json",
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".json", ".jsonl"],
            "exclude_patterns": [".mdex/**"],
            "node_type_map": {"design": ["design"]},
            "summary_max_sentences": 2,
            "summary_max_chars": 120,
            "output_file": ".mdex/mdex_index.json",
        },
    )

    result = _run_cli("scan", "--root", ".", cwd=repo)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    db_path = str(payload["output"]["db"]).replace("\\", "/")
    json_path = str(payload["output"]["json"]).replace("\\", "/")
    assert db_path.endswith(".mdex/mdex_index.db")
    assert json_path.endswith(".mdex/mdex_index.json")
    assert (repo / ".mdex" / "mdex_index.db").exists()
    assert (repo / ".mdex" / "mdex_index.json").exists()


def test_new_rejects_task_dir_outside_repo_from_config(tmp_path: Path) -> None:
    repo = tmp_path / "task_dir_outside_repo"
    repo.mkdir()
    outside_dir = tmp_path / "outside_tasks"
    outside_dir.mkdir()
    _write_json(
        repo / ".mdex" / "config.json",
        {
            "task_dir": str(outside_dir.resolve()),
        },
    )

    result = _run_cli("new", "task", "outside task dir", cwd=repo)
    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "new failed"
    assert "task_dir must stay within repo" in payload["detail"]


def test_scan_rejects_scan_root_outside_repo_from_config(tmp_path: Path) -> None:
    repo = tmp_path / "scan_root_outside_repo"
    repo.mkdir()
    (repo / "inside.md").write_text("# inside\n", encoding="utf-8")

    outside = tmp_path / "outside_scan_root"
    outside.mkdir()
    (outside / "outside.md").write_text("# outside\n", encoding="utf-8")
    _write_json(
        repo / ".mdex" / "config.json",
        {
            "scan_root": str(outside.resolve()),
        },
    )

    result = _run_cli("scan", cwd=repo)
    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "scan failed"
    assert "scan_root must stay within repo" in payload["detail"]


def test_scan_skips_outside_symlink_target(tmp_path: Path) -> None:
    repo = tmp_path / "scan_symlink_repo"
    repo.mkdir()
    (repo / "inside.md").write_text("# inside\n", encoding="utf-8")

    outside = tmp_path / "scan_symlink_outside"
    outside.mkdir()
    outside_target = outside / "secret.md"
    outside_target.write_text("# secret\n", encoding="utf-8")

    link_path = repo / "linked.md"
    try:
        link_path.symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not available in this environment")

    scanned = _run_cli("scan", "--root", ".", cwd=repo)
    assert scanned.returncode == 0
    scan_payload = json.loads(scanned.stdout)
    assert scan_payload["nodes"] == 1

    listed = _run_cli("list", cwd=repo)
    assert listed.returncode == 0
    listed_payload = json.loads(listed.stdout)
    ids = {row["id"] for row in listed_payload}
    assert "inside.md" in ids
    assert "linked.md" not in ids


def test_db_resolution_prefers_dot_mdex_over_repo_fallback(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    fallback_db = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, fallback_db)

    dot_mdex_db = quality_repo / ".mdex" / "mdex_index.db"
    dot_mdex_db.parent.mkdir(parents=True, exist_ok=True)
    _build_db(quality_repo, quality_config, dot_mdex_db)
    with sqlite3.connect(str(dot_mdex_db)) as conn:
        conn.execute("DELETE FROM nodes")
        conn.execute(
            """
            INSERT INTO nodes (
                id, title, type, project, status, summary, summary_source, summary_updated,
                tags_json, updated, links_to_json, depends_on_json, relates_to_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "custom/only.md",
                "Only DotMdex",
                "design",
                "quality",
                "active",
                "dot-mdex preferred",
                "seed",
                "2026-01-01",
                "[]",
                "2026-01-01",
                "[]",
                "[]",
                "[]",
            ),
        )
        conn.commit()

    result = _run_cli("list", cwd=quality_repo)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    ids = {row["id"] for row in payload}
    assert "custom/only.md" in ids
    assert "design/root.md" not in ids


def test_db_resolution_env_var_list(
    quality_repo: Path,
    quality_config: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_db = quality_repo / "env_index.db"
    _build_db(quality_repo, quality_config, env_db)
    monkeypatch.setenv("MDEX_DB", str(env_db))

    result = _run_cli("list", cwd=quality_repo)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert any(row["id"] == "design/root.md" for row in payload)


def test_db_resolution_config_list(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    config_db = quality_repo / ".mdex" / "config_index.db"
    _build_db(quality_repo, quality_config, config_db)
    _write_json(
        quality_repo / ".mdex" / "config.json",
        {"db": ".mdex/config_index.db"},
    )

    result = _run_cli("list", cwd=quality_repo)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert any(row["id"] == "design/root.md" for row in payload)


def test_db_resolution_failure_returns_attempts(tmp_path: Path) -> None:
    missing_repo = tmp_path / "missing_repo"
    missing_repo.mkdir()

    result = _run_cli("list", cwd=missing_repo)
    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "db not found"
    assert isinstance(payload.get("resolution_attempts"), list)
    assert payload["resolution_attempts"]
    assert "hint" in payload


def test_db_resolution_explicit_db_has_priority(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_db = quality_repo / "explicit.db"
    _build_db(quality_repo, quality_config, explicit_db)

    env_repo = tmp_path / "env_repo"
    env_repo.mkdir()
    (env_repo / "only_env.md").write_text(
        """---
type: design
status: active
updated: 2026-01-01
---
# Env Only

env db only
""",
        encoding="utf-8",
    )
    env_db = tmp_path / "env.db"
    env_config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"design": ["design"]},
        "summary_max_sentences": 2,
        "summary_max_chars": 120,
    }
    _build_db(env_repo, env_config, env_db)
    monkeypatch.setenv("MDEX_DB", str(env_db))

    result = _run_cli("list", "--db", str(explicit_db), cwd=quality_repo)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    ids = {row["id"] for row in payload}
    assert "design/root.md" in ids
    assert "only_env.md" not in ids


def test_context_actionable_and_start_contract(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    legacy = _run_cli("context", "root decision", cwd=quality_repo)
    assert legacy.returncode == 0
    legacy_payload = json.loads(legacy.stdout)
    assert "nodes" in legacy_payload
    assert "recommended_read_order" not in legacy_payload

    actionable = _run_cli("context", "root decision", "--actionable", cwd=quality_repo)
    assert actionable.returncode == 0
    actionable_payload = json.loads(actionable.stdout)
    assert "nodes" in actionable_payload
    assert actionable_payload["recommended_read_order"]
    assert actionable_payload["recommended_next_actions"]
    assert "confidence" in actionable_payload
    assert "why_this_set" in actionable_payload

    start = _run_cli("start", "root decision", cwd=quality_repo)
    assert start.returncode == 0
    start_payload = json.loads(start.stdout)
    assert start_payload["task"] == "root decision"
    assert start_payload["recommended_read_order"]
    assert start_payload["recommended_next_actions"]


def test_finish_dry_run_uses_git_changed_files_without_writes(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    (quality_repo / ".gitignore").write_text("*.db\n", encoding="utf-8")
    _init_git_repo(quality_repo)

    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)
    target_file = quality_repo / "design" / "root.md"
    target_file.write_text(target_file.read_text(encoding="utf-8") + "\nnew finish dry-run change\n", encoding="utf-8")

    with sqlite3.connect(str(db_path)) as conn:
        count_before = int(conn.execute("SELECT COUNT(*) FROM node_overrides").fetchone()[0])
    assert count_before == 0

    finish = _run_cli("finish", "--task", "root fix", "--dry-run", cwd=quality_repo)
    assert finish.returncode == 0
    payload = json.loads(finish.stdout)
    assert payload["dry_run"] is True
    assert payload["changed_files"]
    assert isinstance(payload["enrich_candidates"], list)
    assert payload["applied_enrichments"] == []

    with sqlite3.connect(str(db_path)) as conn:
        count_after = int(conn.execute("SELECT COUNT(*) FROM node_overrides").fetchone()[0])
    assert count_after == 0


def test_impact_with_paths_and_with_git(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    (quality_repo / ".gitignore").write_text("*.db\n", encoding="utf-8")
    _init_git_repo(quality_repo)

    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)
    target_file = quality_repo / "design" / "root.md"
    target_file.write_text(target_file.read_text(encoding="utf-8") + "\nimpact changed file\n", encoding="utf-8")

    by_path = _run_cli("impact", "design/root.md", cwd=quality_repo)
    assert by_path.returncode == 0
    by_path_payload = json.loads(by_path.stdout)
    assert "read_first" in by_path_payload
    assert "related_tasks" in by_path_payload
    assert "decision_records" in by_path_payload
    assert "stale_watch" in by_path_payload

    by_git = _run_cli("impact", "--changed-files-from-git", cwd=quality_repo)
    assert by_git.returncode == 0
    by_git_payload = json.loads(by_git.stdout)
    assert by_git_payload["inputs"]
    assert any(item.endswith("design/root.md") for item in by_git_payload["inputs"])


def test_finish_apply_and_scan(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    (quality_repo / ".gitignore").write_text("*.db\n", encoding="utf-8")
    _init_git_repo(quality_repo)

    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    changed = quality_repo / "design" / "root.md"
    changed.write_text(changed.read_text(encoding="utf-8") + "\nfinish apply change\n", encoding="utf-8")
    summary_file = tmp_path / "summary.txt"
    summary_file.write_text("finish apply summary text", encoding="utf-8")

    result = _run_cli(
        "finish",
        "--task",
        "root fix",
        "--summary-file",
        str(summary_file),
        "--scan",
        cwd=quality_repo,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["requires_manual_targeting"] is False
    assert payload["applied_enrichments"]
    assert payload["scan"]["requested"] is True
    assert payload["scan"]["ran"] is True

    updated = get_node(str(db_path), "design/root.md")
    assert updated is not None
    assert updated["summary_source"] == "agent"
    assert updated["summary"] == "finish apply summary text"


def test_finish_requires_manual_targeting_when_multiple_primary(
    quality_repo: Path,
    quality_config: dict[str, object],
    tmp_path: Path,
) -> None:
    (quality_repo / ".gitignore").write_text("*.db\n", encoding="utf-8")
    _init_git_repo(quality_repo)

    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    for relative in ("design/root.md", "notes/orphan.md"):
        target = quality_repo / relative
        target.write_text(target.read_text(encoding="utf-8") + "\nmultiple primary change\n", encoding="utf-8")

    summary_file = tmp_path / "summary.txt"
    summary_file.write_text("manual targeting expected", encoding="utf-8")

    result = _run_cli(
        "finish",
        "--task",
        "multi primary",
        "--summary-file",
        str(summary_file),
        cwd=quality_repo,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["requires_manual_targeting"] is True
    assert payload["applied_enrichments"] == []


def test_new_task_decision_and_stamp_path(tmp_path: Path) -> None:
    repo = tmp_path / "scaffold_repo"
    repo.mkdir()
    _write_json(
        repo / ".mdex" / "config.json",
        {
            "task_dir": "tasks/pending",
            "decision_dir": "decision",
            "db": "mdex_index.db",
        },
    )

    new_task = _run_cli("new", "task", "example task", cwd=repo)
    assert new_task.returncode == 0
    task_payload = json.loads(new_task.stdout)
    task_path = Path(task_payload["path"])
    assert task_path.exists()
    task_text = task_path.read_text(encoding="utf-8")
    assert "type: task" in task_text
    assert "status: pending" in task_text

    new_decision = _run_cli("new", "decision", "Example Decision", cwd=repo)
    assert new_decision.returncode == 0
    decision_payload = json.loads(new_decision.stdout)
    decision_path = Path(decision_payload["path"])
    assert decision_path.exists()
    decision_text = decision_path.read_text(encoding="utf-8")
    assert "type: decision" in decision_text
    assert "status: active" in decision_text

    note = repo / "note.md"
    note.write_text("# note\n", encoding="utf-8")
    scan_config = {
        "include_extensions": [".md"],
        "exclude_patterns": [],
        "node_type_map": {"task": ["tasks"], "decision": ["decision"]},
        "summary_max_sentences": 2,
        "summary_max_chars": 120,
    }
    _build_db(repo, scan_config, repo / "mdex_index.db")
    stamped = _run_cli("stamp", "note.md", cwd=repo)
    assert stamped.returncode == 0
    stamped_text = note.read_text(encoding="utf-8")
    assert "updated: " in stamped_text
    assert _today_utc() in stamped_text


def test_stamp_node_id_resolution_uses_auto_db(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    target = quality_repo / "design" / "root.md"
    stamped = _run_cli("stamp", "design/root.md", cwd=quality_repo)
    assert stamped.returncode == 0
    after = target.read_text(encoding="utf-8")
    assert f"updated: {_today_utc()}" in after


def test_open_rejects_non_indexed_absolute_and_parent_inputs(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    absolute = _run_cli("open", str((quality_repo / "design" / "root.md").resolve()), cwd=quality_repo)
    assert absolute.returncode == 2
    absolute_payload = json.loads(absolute.stderr)
    assert absolute_payload["error"] == "invalid node id"
    assert "absolute paths are not allowed" in absolute_payload["detail"]

    parent = _run_cli("open", "../outside.md", cwd=quality_repo)
    assert parent.returncode == 2
    parent_payload = json.loads(parent.stderr)
    assert parent_payload["error"] == "invalid node id"
    assert "path traversal" in parent_payload["detail"]

    not_indexed = _run_cli("open", "design/no_such.md", cwd=quality_repo)
    assert not_indexed.returncode == 2
    not_indexed_payload = json.loads(not_indexed.stderr)
    assert not_indexed_payload["error"] == "node not indexed"


def test_stamp_rejects_absolute_parent_and_non_indexed_inputs(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    absolute = _run_cli("stamp", str((quality_repo / "design" / "root.md").resolve()), cwd=quality_repo)
    assert absolute.returncode == 2
    absolute_payload = json.loads(absolute.stderr)
    assert absolute_payload["error"] == "invalid node id"
    assert "absolute paths are not allowed" in absolute_payload["detail"]

    parent = _run_cli("stamp", "../outside.md", cwd=quality_repo)
    assert parent.returncode == 2
    parent_payload = json.loads(parent.stderr)
    assert parent_payload["error"] == "invalid node id"
    assert "path traversal" in parent_payload["detail"]

    not_indexed = _run_cli("stamp", "design/no_such.md", cwd=quality_repo)
    assert not_indexed.returncode == 2
    not_indexed_payload = json.loads(not_indexed.stderr)
    assert not_indexed_payload["error"] == "node not indexed"


def test_open_and_stamp_reject_scan_root_escape_even_if_node_is_indexed(
    quality_repo: Path,
    quality_config: dict[str, object],
) -> None:
    db_path = quality_repo / "mdex_index.db"
    _build_db(quality_repo, quality_config, db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO nodes (
                id, title, type, project, status, summary, summary_source, summary_updated,
                tags_json, updated, links_to_json, depends_on_json, relates_to_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "escape/../../outside.md",
                "escape",
                "design",
                "quality",
                "active",
                "escape",
                "seed",
                "2026-01-01",
                "[]",
                "2026-01-01",
                "[]",
                "[]",
                "[]",
            ),
        )
        conn.commit()

    opened = _run_cli("open", "escape/../../outside.md", cwd=quality_repo)
    assert opened.returncode == 2
    open_payload = json.loads(opened.stderr)
    assert open_payload["error"] == "invalid node id"
    assert "path traversal" in open_payload["detail"]

    stamped = _run_cli("stamp", "escape/../../outside.md", cwd=quality_repo)
    assert stamped.returncode == 2
    stamp_payload = json.loads(stamped.stderr)
    assert stamp_payload["error"] == "invalid node id"
    assert "path traversal" in stamp_payload["detail"]
