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


def test_resolve_task_dir_defaults_to_tasks_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, {})

    context = load_runtime_context(repo)

    assert resolve_task_dir(context) == (repo / "tasks").resolve()


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


def test_resolve_db_path_plain_repo_has_no_worktree_attempts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(repo, {})
    (repo / ".git").mkdir()

    with pytest.raises(DbResolutionError) as caught:
        resolve_db_path(None, cwd=repo, must_exist=True)

    sources = [attempt["source"] for attempt in caught.value.payload["resolution_attempts"]]
    assert sources == ["repo_default", "repo_default"]


def test_resolve_db_path_worktree_falls_back_to_main_root_db(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})
    main_db = main / ".mdex" / "mdex_index.db"
    main_db.write_text("", encoding="utf-8")

    resolved = resolve_db_path(None, cwd=worktree, must_exist=True)

    assert Path(resolved["path"]) == main_db.resolve()
    assert resolved["source"] == "worktree_common_root"
    assert Path(resolved["repo_root"]) == worktree.resolve()
    sources = [attempt["source"] for attempt in resolved["resolution_attempts"]]
    assert sources == ["repo_default", "repo_default", "worktree_common_root"]
    assert all(
        not attempt["exists"]
        for attempt in resolved["resolution_attempts"]
        if attempt["source"] == "repo_default"
    )


def test_resolve_db_path_worktree_prefers_local_db(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})
    (main / ".mdex" / "mdex_index.db").write_text("", encoding="utf-8")
    local_db = worktree / ".mdex" / "mdex_index.db"
    local_db.write_text("", encoding="utf-8")

    resolved = resolve_db_path(None, cwd=worktree, must_exist=True)

    assert Path(resolved["path"]) == local_db.resolve()
    assert resolved["source"] == "repo_default"


def test_resolve_db_path_worktree_mirrors_config_db_value(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {"db": ".mdex/custom.db"})
    _write_config(worktree, {"db": ".mdex/custom.db"})
    main_db = main / ".mdex" / "custom.db"
    main_db.write_text("", encoding="utf-8")

    resolved = resolve_db_path(None, cwd=worktree, must_exist=True)

    assert Path(resolved["path"]) == main_db.resolve()
    assert resolved["source"] == "worktree_common_root"


def test_resolve_db_path_worktree_fails_closed_when_no_db_anywhere(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})

    with pytest.raises(DbResolutionError) as caught:
        resolve_db_path(None, cwd=worktree, must_exist=True)

    payload = caught.value.payload
    assert payload["error"] == "db not found"
    attempts = payload["resolution_attempts"]
    worktree_paths = [
        attempt["path"] for attempt in attempts if attempt["source"] == "repo_default"
    ]
    main_paths = [
        attempt["path"] for attempt in attempts if attempt["source"] == "worktree_common_root"
    ]
    assert worktree_paths
    assert main_paths
    assert (worktree / ".mdex" / "mdex_index.db").resolve().as_posix() in worktree_paths
    assert (main / ".mdex" / "mdex_index.db").resolve().as_posix() in main_paths


def test_resolve_db_path_worktree_creates_local_db_when_must_exist_false(
    tmp_path: Path,
) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})
    (main / ".mdex" / "mdex_index.db").write_text("", encoding="utf-8")

    resolved = resolve_db_path(None, cwd=worktree, must_exist=False)

    assert Path(resolved["path"]) == (worktree / ".mdex" / "mdex_index.db").resolve()
    assert resolved["source"] == "repo_default"
    assert all(
        attempt["source"] != "worktree_common_root"
        for attempt in resolved["resolution_attempts"]
    )


def test_resolve_db_path_worktree_fallback_disabled_for_writers(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})
    (main / ".mdex" / "mdex_index.db").write_text("", encoding="utf-8")

    with pytest.raises(DbResolutionError) as caught:
        resolve_db_path(None, cwd=worktree, must_exist=True, allow_worktree_fallback=False)

    payload = caught.value.payload
    assert payload["error"] == "db not found"
    assert all(
        attempt["source"] != "worktree_common_root"
        for attempt in payload["resolution_attempts"]
    )


def test_resolve_db_path_worktree_without_commondir_stays_fail_closed(
    tmp_path: Path,
) -> None:
    main, worktree = _make_linked_worktree(tmp_path, commondir=False)
    _write_config(main, {})
    _write_config(worktree, {})
    (main / ".mdex" / "mdex_index.db").write_text("", encoding="utf-8")

    with pytest.raises(DbResolutionError) as caught:
        resolve_db_path(None, cwd=worktree, must_exist=True)

    assert all(
        attempt["source"] != "worktree_common_root"
        for attempt in caught.value.payload["resolution_attempts"]
    )


def test_resolve_db_path_worktree_dedupes_mirrored_config_default(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {"db": ".mdex/mdex_index.db"})
    _write_config(worktree, {"db": ".mdex/mdex_index.db"})

    with pytest.raises(DbResolutionError) as caught:
        resolve_db_path(None, cwd=worktree, must_exist=True)

    mirrored = [
        attempt["path"]
        for attempt in caught.value.payload["resolution_attempts"]
        if attempt["source"] == "worktree_common_root"
    ]
    assert len(mirrored) == len(set(mirrored))
    assert (main / ".mdex" / "mdex_index.db").resolve().as_posix() in mirrored


def test_resolve_db_path_worktree_skips_mirror_escaping_main_root(tmp_path: Path) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(worktree, {})
    local_db = worktree / ".mdex" / "mdex_index.db"
    local_db.write_text("", encoding="utf-8")
    outside = tmp_path / "outside-mdex"
    outside.mkdir()
    try:
        (main / ".mdex").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available in this environment")

    resolved = resolve_db_path(None, cwd=worktree, must_exist=True)

    assert Path(resolved["path"]) == local_db.resolve()
    assert resolved["source"] == "repo_default"


def test_resolve_db_path_worktree_keeps_env_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main, worktree = _make_linked_worktree(tmp_path)
    _write_config(main, {})
    _write_config(worktree, {})
    (main / ".mdex" / "mdex_index.db").write_text("", encoding="utf-8")
    env_db = tmp_path / "env" / "from-env.db"
    env_db.parent.mkdir(parents=True, exist_ok=True)
    env_db.write_text("", encoding="utf-8")
    monkeypatch.setenv("MDEX_DB", str(env_db))

    resolved = resolve_db_path(None, cwd=worktree, must_exist=True)

    assert Path(resolved["path"]) == env_db.resolve()
    assert resolved["source"] == "env"
