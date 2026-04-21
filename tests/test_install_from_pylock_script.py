from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import textwrap
from pathlib import Path
from types import ModuleType

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version
import pytest
import tomllib


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


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _imports_module(node: ast.AST, module_name: str) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name == module_name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.module == module_name
    return False


def _handler_catches_module_not_found(handler: ast.ExceptHandler) -> bool:
    handler_type = handler.type
    if handler_type is None:
        return False
    if isinstance(handler_type, ast.Name):
        return handler_type.id == "ModuleNotFoundError"
    if isinstance(handler_type, ast.Tuple):
        return any(isinstance(item, ast.Name) and item.id == "ModuleNotFoundError" for item in handler_type.elts)
    return False


def _imports_are_guarded(path: Path, module_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = _build_parent_map(tree)
    for node in ast.walk(tree):
        if not _imports_module(node, module_name):
            continue
        current = parents.get(node)
        while current is not None:
            if isinstance(current, ast.Try) and any(
                _handler_catches_module_not_found(handler) for handler in current.handlers
            ):
                break
            current = parents.get(current)
        else:
            return False
    return True


def test_script_imports_with_pip_vendored_packaging(monkeypatch: pytest.MonkeyPatch) -> None:
    script_path = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "install_from_pylock.py"
    source = script_path.read_text(encoding="utf-8")
    original_import = builtins.__import__

    def fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "packaging" or name.startswith("packaging."):
            raise ModuleNotFoundError("No module named 'packaging'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    namespace = {"__file__": str(script_path), "__name__": "install_from_pylock_fallback_test"}
    exec(compile(source, str(script_path), "exec"), namespace)

    requirement = namespace["Requirement"]('typing_extensions<5.0,>=4.6; python_version < "3.13"')
    assert requirement.name == "typing_extensions"
    assert callable(namespace["default_environment"])


def test_bootstrap_scripts_guard_tomllib_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / ".github" / "scripts" / "export_release_hashes.py",
        repo_root / ".github" / "scripts" / "install_from_pylock.py",
    ]
    missing_guards = [str(path) for path in targets if not _imports_are_guarded(path, "tomllib")]
    assert not missing_guards, f"unguarded tomllib imports found: {missing_guards}"


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


def test_requirements_from_pylock_closes_transitive_dependencies(
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
        name = "cyclonedx-python-lib"
        version = "11.7.0"
        """,
    )

    supplemental = tmp_path / "hashes.json"
    supplemental.write_text(
        json.dumps(
            {
                "cyclonedx-python-lib": {
                    "version": "11.7.0",
                    "hashes": ["sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
                    "requires_dist": ['typing_extensions<5.0,>=4.6; python_version < "3.13"'],
                },
                "typing-extensions": {
                    "version": "4.15.0",
                    "hashes": ["sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
                    "requires_dist": [],
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "SUPPLEMENTAL_HASH_PATH", supplemental)
    monkeypatch.setattr(
        module,
        "default_environment",
        lambda: {"python_version": "3.12", "python_full_version": "3.12.0"},
    )
    requirements = module._requirements_from_pylock(lock)
    assert requirements == [
        "cyclonedx-python-lib==11.7.0 --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "typing-extensions==4.15.0 --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
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
        name = module._normalize_name(str(package.get("name", "")))
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


def test_supplemental_catalog_closes_transitives_for_ci_matrix() -> None:
    module = _load_script_module()
    repo_root = Path(__file__).resolve().parents[1]
    lock_data = tomllib.loads((repo_root / "pylock.toml").read_text(encoding="utf-8"))
    packages = lock_data.get("packages", [])
    assert isinstance(packages, list)

    supplemental = module._load_supplemental_hashes()
    assert supplemental
    assert any(
        isinstance(row.get("requires_dist"), list) and bool(row.get("requires_dist"))
        for row in supplemental.values()
        if isinstance(row, dict)
    ), "supplemental hash catalog is missing requires_dist metadata"

    roots: dict[str, str] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = module._normalize_name(str(package.get("name", "")))
        version = str(package.get("version", "")).strip()
        if not name or not version or name in module.SKIP_PACKAGES:
            continue
        roots[name] = version

    base_env = default_environment()
    target_envs: list[dict[str, str]] = []
    for pyver, sys_platform, platform_system, os_name in (
        ("3.10", "linux", "Linux", "posix"),
        ("3.11", "linux", "Linux", "posix"),
        ("3.12", "linux", "Linux", "posix"),
        ("3.10", "darwin", "Darwin", "posix"),
        ("3.11", "darwin", "Darwin", "posix"),
        ("3.12", "darwin", "Darwin", "posix"),
        ("3.10", "win32", "Windows", "nt"),
        ("3.11", "win32", "Windows", "nt"),
        ("3.12", "win32", "Windows", "nt"),
    ):
        env = dict(base_env)
        env["python_version"] = pyver
        env["python_full_version"] = f"{pyver}.0"
        env["sys_platform"] = sys_platform
        env["platform_system"] = platform_system
        env["os_name"] = os_name
        env["extra"] = ""
        target_envs.append(env)

    missing: list[str] = []
    for env in target_envs:
        queue = list(sorted(roots))
        seen = set(queue)
        while queue:
            name = queue.pop(0)
            row = supplemental.get(name)
            if row is None:
                missing.append(
                    f"{name} missing metadata for env py{env['python_version']}:{env['sys_platform']}"
                )
                continue

            if name in roots and str(row.get("version", "")).strip() != roots[name]:
                missing.append(
                    f"{name} version mismatch for env py{env['python_version']}:{env['sys_platform']}"
                )

            requires_dist = row.get("requires_dist", [])
            if not isinstance(requires_dist, list):
                continue

            for raw_requirement in requires_dist:
                requirement_text = str(raw_requirement).strip()
                if not requirement_text:
                    continue
                requirement = Requirement(requirement_text)
                if requirement.marker is not None and not requirement.marker.evaluate(env):
                    continue

                dependency_name = module._normalize_name(requirement.name)
                if dependency_name in module.SKIP_PACKAGES:
                    continue
                dependency = supplemental.get(dependency_name)
                if dependency is None:
                    missing.append(
                        f"{name} -> {dependency_name} missing for env "
                        f"py{env['python_version']}:{env['sys_platform']}"
                    )
                    continue
                dependency_version = str(dependency.get("version", "")).strip()
                if not dependency_version:
                    missing.append(
                        f"{name} -> {dependency_name} missing version for env "
                        f"py{env['python_version']}:{env['sys_platform']}"
                    )
                    continue
                if requirement.specifier and Version(dependency_version) not in requirement.specifier:
                    missing.append(
                        f"{name} -> {dependency_name}=={dependency_version} violates '{requirement.specifier}' "
                        f"for env py{env['python_version']}:{env['sys_platform']}"
                    )
                    continue
                if dependency_name not in seen:
                    seen.add(dependency_name)
                    queue.append(dependency_name)

    assert not missing, f"supplemental release hash catalog is not fully closed: {missing}"
