from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "context_refresh.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("context_refresh", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_context_refresh_classifies_repo_task_and_memory_paths() -> None:
    script = _load_script()

    report = script.classify_refresh_targets(
        [
            "mdex/context.py",
            "tasks/T20260101010101.md",
            "memory/user.jsonl",
        ]
    )

    assert report["repo"]["needed"] is True
    assert report["repo"]["files"] == ["mdex/context.py"]
    assert report["task"]["needed"] is True
    assert report["task"]["files"] == ["tasks/T20260101010101.md"]
    assert report["task"]["command"][-4:] == [
        "--db",
        ".mdex/task_history.db",
        "--output",
        ".mdex/task_history.json",
    ]
    assert report["memory"]["needed"] is True
    assert report["memory"]["files"] == ["memory/user.jsonl"]


def test_context_refresh_preserves_dot_directory_paths() -> None:
    script = _load_script()

    report = script.classify_refresh_targets([".mdex/config.json", ".mdex/memory/user.jsonl"])

    assert report["repo"]["needed"] is True
    assert report["repo"]["files"] == [".mdex/config.json"]
    assert report["memory"]["needed"] is True
    assert report["memory"]["files"] == [".mdex/memory/user.jsonl"]


def test_context_refresh_index_engine_change_refreshes_repo_and_task() -> None:
    script = _load_script()

    report = script.classify_refresh_targets(["mdex/parser.py"])

    assert report["repo"]["needed"] is True
    assert report["repo"]["files"] == ["mdex/parser.py"]
    assert report["task"]["needed"] is True
    assert report["task"]["files"] == ["mdex/parser.py"]


def test_context_refresh_does_not_treat_legacy_docs_tasks_as_task_lane() -> None:
    script = _load_script()

    report = script.classify_refresh_targets(["docs/tasks/legacy.md"])

    assert report["repo"]["needed"] is True
    assert report["task"]["needed"] is False


def test_context_refresh_dry_run_does_not_execute_scan(tmp_path: Path) -> None:
    script = _load_script()

    result = script.main(["--repo-root", str(tmp_path), "--dry-run", "docs/design.md"])

    assert result == 0


def test_context_refresh_git_changed_files_includes_untracked(tmp_path: Path) -> None:
    script = _load_script()
    probe = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("git not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "codex@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Codex"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True, text=True)

    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "T20260101010101.md").write_text("# task\n", encoding="utf-8")

    changed = script._git_changed_files(tmp_path)

    assert "tasks/T20260101010101.md" in changed


def test_context_refresh_git_changed_files_handles_unborn_head(tmp_path: Path) -> None:
    script = _load_script()
    probe = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("git not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "user.jsonl").write_text("{}\n", encoding="utf-8")

    changed = script._git_changed_files(tmp_path)

    assert "memory/user.jsonl" in changed


def test_context_refresh_git_changed_files_handles_staged_unborn_head(tmp_path: Path) -> None:
    script = _load_script()
    probe = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("git not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "T20260101010101.md").write_text("# task\n", encoding="utf-8")
    subprocess.run(["git", "add", "tasks/T20260101010101.md"], cwd=tmp_path, check=True)

    changed = script._git_changed_files(tmp_path)

    assert "tasks/T20260101010101.md" in changed


def test_context_refresh_git_changed_files_preserves_non_ascii_and_spaces(tmp_path: Path) -> None:
    script = _load_script()
    probe = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("git not available")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    path = task_dir / "日本語 task.md"
    path.write_text("# task\n", encoding="utf-8")
    subprocess.run(["git", "add", path.relative_to(tmp_path).as_posix()], cwd=tmp_path, check=True)

    changed = script._git_changed_files(tmp_path)

    assert "tasks/日本語 task.md" in changed
