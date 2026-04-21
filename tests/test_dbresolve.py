from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdex.dbresolve import (
    DbResolutionError,
    detect_repo_root,
    load_runtime_context,
    resolve_db_path,
    resolve_decision_dir,
    resolve_scan_config_path,
    resolve_scan_root,
    resolve_scan_roots,
    resolve_task_dir,
)


def _write_config(repo: Path, config: object) -> None:
    config_path = repo / ".mdex" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")


def test_detect_repo_root_prefers_mdex_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    _write_config(repo, {"db": ".mdex/custom.db"})
    (repo / ".git").mkdir()

    assert detect_repo_root(nested) == repo.resolve()


def test_detect_repo_root_falls_back_to_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    assert detect_repo_root(nested) == repo.resolve()


def test_load_runtime_context_rejects_non_object_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, ["not-an-object"])

    with pytest.raises(ValueError, match="config root must be object"):
        load_runtime_context(repo)


def test_resolve_scan_roots_dedup_and_deprecation_warning(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(
        repo,
        {
            "scan_roots": ["docs", "./docs", "design"],
            "scan_root": "legacy",
        },
    )

    context = load_runtime_context(repo)
    roots, warnings = resolve_scan_roots(context)

    assert [path.relative_to(repo).as_posix() for path in roots] == ["docs", "design"]
    assert any("scan_root is deprecated" in warning for warning in warnings)
    assert resolve_scan_root(context) == (repo / "docs").resolve()


def test_resolve_decision_dir_prefers_existing_plural_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, {})
    (repo / "decisions").mkdir()

    context = load_runtime_context(repo)
    resolved = resolve_decision_dir(context)

    assert resolved == (repo / "decisions").resolve()


def test_resolve_paths_from_config_values(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(
        repo,
        {
            "task_dir": "work/tasks",
            "scan_config": "control/custom_scan.json",
        },
    )

    context = load_runtime_context(repo)
    assert resolve_task_dir(context) == (repo / "work" / "tasks").resolve()
    assert resolve_scan_config_path(context) == (repo / "control" / "custom_scan.json").resolve()


def test_resolve_db_path_prefers_explicit_and_allows_outside_repo(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, {})

    outside = tmp_path / "outside" / "cache.db"
    resolved = resolve_db_path(str(outside), cwd=repo, must_exist=False)

    assert Path(resolved["path"]) == outside.resolve()
    assert resolved["source"] == "arg"
    assert outside.parent.exists()


def test_resolve_db_path_uses_env_then_config_then_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, {"db": ".mdex/from-config.db"})
    env_db = tmp_path / "env" / "from-env.db"
    env_db.parent.mkdir(parents=True, exist_ok=True)
    env_db.write_text("", encoding="utf-8")
    monkeypatch.setenv("MDEX_DB", str(env_db))

    resolved = resolve_db_path(None, cwd=repo, must_exist=True)
    assert Path(resolved["path"]) == env_db.resolve()
    assert resolved["source"] == "env"

    monkeypatch.setenv("MDEX_DB", str(tmp_path / "env" / "missing.db"))
    config_db = repo / ".mdex" / "from-config.db"
    config_db.parent.mkdir(parents=True, exist_ok=True)
    config_db.write_text("", encoding="utf-8")

    resolved_from_config = resolve_db_path(None, cwd=repo, must_exist=True)
    assert Path(resolved_from_config["path"]) == config_db.resolve()
    assert resolved_from_config["source"] == "config"


def test_resolve_db_path_raises_with_resolution_attempts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, {})

    with pytest.raises(DbResolutionError) as caught:
        resolve_db_path(None, cwd=repo, must_exist=True)

    payload = caught.value.payload
    assert payload["error"] == "db not found"
    assert payload["resolution_attempts"]
    assert "mdex scan --root" in payload["hint"]
