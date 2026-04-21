from __future__ import annotations

import importlib.util
import json
import tomllib
import textwrap
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
    path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")


def test_requirements_from_pylock_includes_hashes(tmp_path: Path) -> None:
    module = _load_script_module()
    lock = tmp_path / "pylock.toml"
    _write_lock(
        lock,
        """
        lock-version = "1.0"
        created-by = "pip"

        [[packages]]
        name = "samplepkg"
        version = "1.0.0"

        [[packages.wheels]]
        name = "samplepkg-1.0.0-py3-none-any.whl"
        url = "https://example.invalid/samplepkg.whl"

        [packages.wheels.hashes]
        sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        """,
    )

    requirements = module._requirements_from_pylock(lock)
    assert requirements == [
        "samplepkg==1.0.0 --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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
        name = "samplepkg"
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
        name = "samplepkg"
        version = "1.0.0"

        [[packages.wheels]]
        name = "samplepkg-1.0.0-py3-none-any.whl"
        url = "https://example.invalid/samplepkg-1.whl"

        [packages.wheels.hashes]
        sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        [[packages]]
        name = "samplepkg"
        version = "2.0.0"

        [[packages.wheels]]
        name = "samplepkg-2.0.0-py3-none-any.whl"
        url = "https://example.invalid/samplepkg-2.whl"

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
        name = "samplepkg"
        version = "1.0.0"

        [[packages.wheels]]
        name = "samplepkg-1.0.0-py3-none-any.whl"
        url = "https://example.invalid/samplepkg.whl"

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


def test_requirements_from_pylock_supports_regex_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script_module()
    lock = tmp_path / "pylock.toml"
    _write_lock(
        lock,
        """
        lock-version = "1.0"
        created-by = "pip"

        [[packages]]
        name = "samplepkg"
        version = "1.0.0"

        [[packages.wheels]]
        name = "samplepkg-1.0.0-py3-none-any.whl"
        url = "https://example.invalid/samplepkg.whl"

        [packages.wheels.hashes]
        sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        """,
    )

    monkeypatch.setattr(module, "tomllib", None)
    requirements = module._requirements_from_pylock(lock)
    assert requirements == [
        "samplepkg==1.0.0 --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ]


def test_requirements_from_pylock_uses_supplemental_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    supplemental = tmp_path / "hashes.json"
    supplemental.write_text(
        json.dumps(
            {
                "attrs": {
                    "version": "1.0.0",
                    "hashes": ["sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "SUPPLEMENTAL_HASH_PATH", supplemental)
    requirements = module._requirements_from_pylock(lock)
    assert requirements == [
        "attrs==1.0.0 --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    ]


def test_real_pylock_is_covered_by_supplemental_release_hash_catalog() -> None:
    module = _load_script_module()
    repo_root = Path(__file__).resolve().parents[1]
    lock_data = tomllib.loads((repo_root / "pylock.toml").read_text(encoding="utf-8"))
    packages = lock_data.get("packages", [])
    assert isinstance(packages, list)

    supplemental = module._load_supplemental_hashes()
    missing: list[str] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = str(package.get("name", "")).strip().lower()
        version = str(package.get("version", "")).strip()
        if not name or not version:
            continue
        if name in module.SKIP_PACKAGES:
            continue
        row = supplemental.get(name)
        if row is None:
            missing.append(f"{name}=={version} (missing package)")
            continue
        if str(row.get("version", "")).strip() != version:
            missing.append(f"{name}=={version} (version mismatch)")
            continue
        hashes = row.get("hashes", [])
        if not isinstance(hashes, list) or not hashes:
            missing.append(f"{name}=={version} (empty hashes)")

    assert not missing, f"supplemental release hash catalog missing coverage: {missing}"
