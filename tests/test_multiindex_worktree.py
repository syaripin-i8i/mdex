from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from mdex.builder import build_index
from mdex.dbresolve import resolve_db_path
from mdex.indexer import write_sqlite
from mdex.multiindex import build_multi_context_payload, resolve_index_specs

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SCAN_CONFIG = {"include_extensions": [".md"], "exclude_patterns": []}


def _write_config(repo: Path, config: object) -> None:
    config_path = repo / ".mdex" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")


def _make_linked_worktree(
    tmp_path: Path,
    name: str = "wt",
    *,
    commondir: bool = True,
) -> tuple[Path, Path]:
    """Create a main checkout plus a linked-worktree layout like git does.

    The worktree root holds a `.git` FILE with `gitdir: <main>/.git/worktrees/<name>`
    and that gitdir holds a `commondir` file pointing back at `<main>/.git`.
    """
    main = tmp_path / "main"
    gitdir = main / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    if commondir:
        (gitdir / "commondir").write_text("../..\n", encoding="utf-8")

    worktree = main / ".claude" / "worktrees" / name
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    return main, worktree


def _build_db(root: Path, db_path: Path, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "doc.md").write_text(f"# Doc\n\n{body}\n", encoding="utf-8")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    write_sqlite(build_index(str(root), _SCAN_CONFIG), str(db_path))


def _worktree_db_info(tmp_path: Path, *, config: dict | None = None) -> tuple[Path, Path, dict]:
    """Main checkout with a repo db, worktree with only the tracked config."""
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, config or {})
    _write_config(worktree, config or {})
    _build_db(main / "docs", main / ".mdex" / "mdex_index.db", "repo lane body")
    return main, worktree, resolve_db_path(None, cwd=worktree, must_exist=True)


def test_resolve_index_specs_borrows_missing_alias_from_main_root(tmp_path: Path) -> None:
    main, worktree, db_info = _worktree_db_info(tmp_path)
    _build_db(main / "task_docs", main / ".mdex" / "task_history.db", "task lane body")

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "repo,task")}

    repo_spec = specs["repo"]
    assert repo_spec["source"] == "worktree_common_root"
    assert repo_spec["borrowed"] is True
    assert "local_path" not in repo_spec
    assert Path(repo_spec["path"]) == (main / ".mdex" / "mdex_index.db").resolve()

    task_spec = specs["task"]
    assert task_spec["exists"] is True
    assert task_spec["source"] == "default:task+worktree_common_root"
    assert task_spec["borrowed"] is True
    assert Path(task_spec["path"]) == (main / ".mdex" / "task_history.db").resolve()
    assert Path(task_spec["local_path"]) == (worktree / ".mdex" / "task_history.db").resolve()


def test_resolve_index_specs_borrows_sibling_when_repo_db_is_local(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})
    _build_db(worktree / "docs", worktree / ".mdex" / "mdex_index.db", "repo lane body")
    _build_db(main / "task_docs", main / ".mdex" / "task_history.db", "task lane body")
    db_info = resolve_db_path(None, cwd=worktree, must_exist=True)
    assert db_info["source"] == "repo_default"

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "repo,task")}

    assert "borrowed" not in specs["repo"]
    task_spec = specs["task"]
    assert task_spec["exists"] is True
    assert task_spec["source"] == "default:task+worktree_common_root"
    assert Path(task_spec["path"]) == (main / ".mdex" / "task_history.db").resolve()


def test_resolve_index_specs_prefers_worktree_local_alias_db(tmp_path: Path) -> None:
    main, worktree, db_info = _worktree_db_info(tmp_path)
    _build_db(main / "task_docs", main / ".mdex" / "task_history.db", "main task body")
    local_task = worktree / ".mdex" / "task_history.db"
    _build_db(worktree / "task_docs", local_task, "local task body")

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "task")}

    task_spec = specs["task"]
    assert Path(task_spec["path"]) == local_task.resolve()
    assert task_spec["source"] == "default:task"
    assert "borrowed" not in task_spec
    assert "local_path" not in task_spec


def test_resolve_index_specs_mirrors_relative_config_alias_db(tmp_path: Path) -> None:
    config = {"indexes": {"task": {"db": ".mdex/custom_task.db"}}}
    main, worktree, db_info = _worktree_db_info(tmp_path, config=config)
    _build_db(main / "task_docs", main / ".mdex" / "custom_task.db", "custom task body")

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "task")}

    task_spec = specs["task"]
    assert task_spec["exists"] is True
    assert task_spec["source"] == "config:task+worktree_common_root"
    assert Path(task_spec["path"]) == (main / ".mdex" / "custom_task.db").resolve()
    assert Path(task_spec["local_path"]) == (worktree / ".mdex" / "custom_task.db").resolve()


def test_resolve_index_specs_does_not_mirror_absolute_config_alias_db(tmp_path: Path) -> None:
    absolute_db = tmp_path / "elsewhere" / "task.db"
    config = {"indexes": {"task": {"db": str(absolute_db)}}}
    main, worktree, db_info = _worktree_db_info(tmp_path, config=config)

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "task")}

    task_spec = specs["task"]
    assert task_spec["exists"] is False
    assert task_spec["source"] == "config:task"
    assert Path(task_spec["path"]) == absolute_db.resolve()
    assert "borrowed" not in task_spec


def test_resolve_index_specs_skips_mirror_escaping_main_root(tmp_path: Path) -> None:
    # Relative to the main root the value escapes into tmp_path, where the db
    # exists; the mirror must be skipped anyway.
    config = {"indexes": {"task": {"db": "../escaped_task.db"}}}
    main, worktree, db_info = _worktree_db_info(tmp_path, config=config)
    (tmp_path / "escaped_task.db").write_text("", encoding="utf-8")

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "task")}

    task_spec = specs["task"]
    assert task_spec["source"] == "config:task"
    assert "borrowed" not in task_spec


def test_resolve_index_specs_without_common_root_stays_local(tmp_path: Path) -> None:
    repo = tmp_path / "plain_repo"
    repo.mkdir()
    repo_db = repo / ".mdex" / "mdex_index.db"
    _build_db(repo / "docs", repo_db, "repo lane body")
    db_info = {"path": str(repo_db), "source": "arg", "repo_root": str(repo), "config": {}}

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "repo,task")}

    task_spec = specs["task"]
    assert task_spec["exists"] is False
    assert task_spec["source"] == "default:task"
    assert Path(task_spec["path"]) == (repo / ".mdex" / "task_history.db").resolve()
    assert "borrowed" not in task_spec
    assert "borrowed" not in specs["repo"]


def test_resolver_discovers_legacy_task_index_name(tmp_path: Path) -> None:
    repo = tmp_path / "legacy_task_repo"
    repo.mkdir()
    repo_db = repo / ".mdex" / "mdex_index.db"
    _build_db(repo / "docs", repo_db, "repo lane body")
    legacy_task = repo / ".mdex" / "task_index.db"
    _build_db(repo / "tasks", legacy_task, "legacy task lane")
    db_info = {
        "path": str(repo_db),
        "source": "arg",
        "repo_root": str(repo),
        "config": {},
    }

    task_spec = resolve_index_specs(db_info, "task")[0]
    assert task_spec["exists"] is True
    assert task_spec["source"] == "legacy:task"
    assert Path(task_spec["path"]) == legacy_task.resolve()


def test_status_uses_configured_task_index_instead_of_default_name(tmp_path: Path) -> None:
    repo = tmp_path / "configured_task_repo"
    repo.mkdir()
    _write_config(repo, {"indexes": {"task": {"db": ".mdex/custom_task.db"}}})
    repo_db = repo / ".mdex" / "mdex_index.db"
    _build_db(repo / "docs", repo_db, "repo lane body")
    custom_task = repo / ".mdex" / "custom_task.db"
    _build_db(repo / "tasks", custom_task, "configured task lane")

    status = _run_cli(
        "status", "--db", str(repo_db), "--include", "task", cwd=repo
    )
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    task_report = payload["indexes"]["task"]
    assert task_report["health"]["reason"] == "scan_manifest_missing"
    assert task_report["health"]["reason"] != "index_db_missing"


def test_resolve_index_specs_missing_everywhere_keeps_local_anchor(tmp_path: Path) -> None:
    main, worktree, db_info = _worktree_db_info(tmp_path)

    specs = {spec["alias"]: spec for spec in resolve_index_specs(db_info, "task")}

    task_spec = specs["task"]
    assert task_spec["exists"] is False
    assert task_spec["source"] == "default:task"
    assert Path(task_spec["path"]) == (worktree / ".mdex" / "task_history.db").resolve()
    assert "borrowed" not in task_spec


def test_status_missing_task_keeps_legacy_index_health_shape(tmp_path: Path) -> None:
    repo = tmp_path / "missing_task_repo"
    repo.mkdir()
    _write_config(repo, {})
    repo_db = repo / ".mdex" / "mdex_index.db"
    _build_db(repo / "docs", repo_db, "repo lane body")

    status = _run_cli(
        "status", "--db", str(repo_db), "--include", "task", cwd=repo
    )
    assert status.returncode == 0, status.stderr
    report = json.loads(status.stdout)["indexes"]["task"]
    assert report["health"]["reason"] == "index_db_missing"
    assert report["index_health"]["ready"] is False
    assert report["index_health"]["fresh"] is False
    assert report["index_health"]["stale"] is True


def test_multi_context_searches_borrowed_task_index(tmp_path: Path) -> None:
    main, worktree, db_info = _worktree_db_info(tmp_path)
    _build_db(main / "task_docs", main / ".mdex" / "task_history.db", "repo lane body")

    payload = build_multi_context_payload(
        "repo lane body",
        db_info,
        include="repo,task",
        budget=4000,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )

    task_index = payload["multi_index"]["indexes"]["task"]
    assert task_index["ok"] is True
    assert task_index["source"] == "default:task+worktree_common_root"
    assert task_index["borrowed"] is True
    assert task_index["local_path"] == ".mdex/task_history.db"
    assert any(row["index"] == "task" for row in payload["nodes"])


def test_stale_borrowed_artifact_scan_action_targets_worktree_local_path(tmp_path: Path) -> None:
    config = {"indexes": {"artifacts": {"index_stale_after_hours": 1}}}
    main, worktree, db_info = _worktree_db_info(tmp_path, config=config)
    artifact_db = main / ".mdex" / "artifacts.db"
    _build_db(main / "artifact_docs", artifact_db, "artifact lane body")
    db = sqlite3.connect(str(artifact_db))
    try:
        db.execute(
            "UPDATE index_metadata SET value = ? WHERE key = 'generated'",
            ("2000-01-01T00:00:00+00:00",),
        )
        db.commit()
    finally:
        db.close()

    payload = build_multi_context_payload(
        "artifact lane body",
        db_info,
        include="repo,artifacts",
        budget=4000,
        limit=4,
        include_content=False,
        actionable=True,
        digest="full",
        scoring_config=None,
        scoring_config_source="defaults",
    )

    artifact_index = payload["multi_index"]["indexes"]["artifacts"]
    assert artifact_index["borrowed"] is True
    assert artifact_index["artifacts_index_age"]["stale"] is True
    # The rescan recommendation must anchor at the worktree-local path, never
    # at the borrowed main-checkout db.
    assert "run mdex scan-artifacts --db .mdex/artifacts.db" in payload["recommended_next_actions"]
    assert {
        "command": "mdex",
        "args": ["scan-artifacts", "--db", ".mdex/artifacts.db"],
        "reason": "refresh stale artifact index before trusting artifacts lane coverage",
    } in payload["recommended_next_actions_v2"]


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT)
        if not existing_pythonpath
        else f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
    )
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


def test_status_from_worktree_uses_borrowed_indexes_and_main_scan_json(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})
    (main / "docs").mkdir(parents=True)
    (main / "docs" / "doc.md").write_text("# Doc\n\nrepo lane body\n", encoding="utf-8")

    scan = _run_cli("scan", "--root", "docs", cwd=main)
    assert scan.returncode == 0, scan.stderr
    _build_db(main / "task_docs", main / ".mdex" / "task_history.db", "task lane body")

    status = _run_cli("status", "--include", "repo,task", cwd=worktree)
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)

    task_report = payload["indexes"]["task"]
    assert task_report.get("index_health", {}).get("reason") != "index_db_missing"

    repo_messages = [
        str(finding.get("message", ""))
        for check in payload["indexes"]["repo"].get("checks", [])
        for finding in list(check.get("findings", []) or [])
    ]
    assert not any("scan JSON output is missing" in message for message in repo_messages)
    assert not any(
        "manifest JSON output path is unavailable" in message for message in repo_messages
    )


def test_single_index_start_marks_borrowed_repo_db_non_reusable(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    runtime_config = {"scan_config": "control/scan_config.json"}
    _write_config(main, runtime_config)
    _write_config(worktree, runtime_config)
    (main / "control").mkdir()
    (main / "control" / "scan_config.json").write_text(
        json.dumps(_SCAN_CONFIG), encoding="utf-8"
    )
    (main / "docs").mkdir(parents=True)
    (main / "docs" / "doc.md").write_text(
        "# Doc\n\nborrowed entrypoint\n", encoding="utf-8"
    )
    scan = _run_cli("scan", "--root", "docs", cwd=main)
    assert scan.returncode == 0, scan.stderr

    start = _run_cli("start", "borrowed entrypoint", cwd=worktree)
    assert start.returncode == 0, start.stderr
    payload = json.loads(start.stdout)
    assert payload["health"]["reason"] == "worktree_borrowed_index"
    assert payload["health"]["reusable"] is False
    assert payload["index_status"]["fresh"] is False
    assert payload["entrypoint_reason"] != "ranked_entrypoint_available"
