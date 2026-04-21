from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "install_from_pylock.py"
    spec = importlib.util.spec_from_file_location("install_from_pylock_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load install_from_pylock.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_lock(path: Path, body: str) -> None:
    path.write_text(body.strip() + "\n", encoding="utf-8")


def test_requirements_from_pylock_includes_hashes(tmp_path: Path) -> None:
    module = _load_script_module()
    lock = tmp_path / "pylock.toml"
    _write_lock(
        lock,
        """
        lock-version = "1.0"
        created-by = "pip"

        [[packages]]
        name = "attrs"
        version = "1.0.0"

        [[packages.wheels]]
        name = "attrs-1.0.0-py3-none-any.whl"
        url = "https://example.invalid/attrs.whl"

        [packages.wheels.hashes]
        sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        """,
    )

    requirements = module._requirements_from_pylock(lock)
    assert requirements == [
        "attrs==1.0.0 --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ]


def test_requirements_from_pylock_rejects_missing_hash(tmp_path: Path) -> None:
    module = _load_script_module()
    lock = tmp_path / "pylock.toml"
    _write_lock(
        lock,
        """
        lock-version = "1.0"
        created-by = "pip"

        [[packages]]
        name = "attrs"
        version = "1.0.0"
        """,
    )

    with pytest.raises(RuntimeError, match="missing supported artifact hashes"):
        module._requirements_from_pylock(lock)


def test_requirements_from_pylock_rejects_conflicting_versions(tmp_path: Path) -> None:
    module = _load_script_module()
    lock = tmp_path / "pylock.toml"
    _write_lock(
        lock,
        """
        lock-version = "1.0"
        created-by = "pip"

        [[packages]]
        name = "attrs"
        version = "1.0.0"

        [[packages.wheels]]
        name = "attrs-1.0.0-py3-none-any.whl"
        url = "https://example.invalid/attrs-1.whl"

        [packages.wheels.hashes]
        sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        [[packages]]
        name = "attrs"
        version = "2.0.0"

        [[packages.wheels]]
        name = "attrs-2.0.0-py3-none-any.whl"
        url = "https://example.invalid/attrs-2.whl"

        [packages.wheels.hashes]
        sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        """,
    )

    with pytest.raises(RuntimeError, match="conflicting versions"):
        module._requirements_from_pylock(lock)


def test_main_uses_require_hashes_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    lock = tmp_path / "pylock.toml"
    _write_lock(
        lock,
        """
        lock-version = "1.0"
        created-by = "pip"

        [[packages]]
        name = "attrs"
        version = "1.0.0"

        [[packages.wheels]]
        name = "attrs-1.0.0-py3-none-any.whl"
        url = "https://example.invalid/attrs.whl"

        [packages.wheels.hashes]
        sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        """,
    )

    calls: list[list[str]] = []
    monkeypatch.setattr(module, "_run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(module.sys, "argv", ["install_from_pylock.py", "--lock", str(lock), "--editable", ""])

    assert module.main() == 0
    assert calls
    assert "--require-hashes" in calls[0]
