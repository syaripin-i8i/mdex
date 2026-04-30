from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


SKIP_PACKAGES = {"mdex-cli", "pip", "setuptools", "wheel"}
NAME_NORMALIZE_RE = re.compile(r"[-_.]+")
TARGET_MATRIX = (
    ("3.10", "linux", "Linux", "posix"),
    ("3.11", "linux", "Linux", "posix"),
    ("3.12", "linux", "Linux", "posix"),
    ("3.13", "linux", "Linux", "posix"),
    ("3.14", "linux", "Linux", "posix"),
    ("3.10", "darwin", "Darwin", "posix"),
    ("3.11", "darwin", "Darwin", "posix"),
    ("3.12", "darwin", "Darwin", "posix"),
    ("3.13", "darwin", "Darwin", "posix"),
    ("3.14", "darwin", "Darwin", "posix"),
    ("3.10", "win32", "Windows", "nt"),
    ("3.11", "win32", "Windows", "nt"),
    ("3.12", "win32", "Windows", "nt"),
    ("3.13", "win32", "Windows", "nt"),
    ("3.14", "win32", "Windows", "nt"),
)


def _normalize_name(name: str) -> str:
    stripped = name.strip().lower()
    return NAME_NORMALIZE_RE.sub("-", stripped)


def _build_target_envs() -> list[dict[str, str]]:
    base = default_environment()
    target_envs: list[dict[str, str]] = []
    for pyver, sys_platform, platform_system, os_name in TARGET_MATRIX:
        env = dict(base)
        env["python_version"] = pyver
        env["python_full_version"] = f"{pyver}.0"
        env["sys_platform"] = sys_platform
        env["platform_system"] = platform_system
        env["os_name"] = os_name
        env["extra"] = ""
        target_envs.append(env)
    return target_envs


def _load_packages(lock_path: Path) -> dict[str, str]:
    if tomllib is None:
        raise RuntimeError("tomllib is required to generate release-hash catalog")
    loaded = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = loaded.get("packages", [])
    if not isinstance(packages, list):
        raise RuntimeError(f"invalid pylock format (packages must be a list): {lock_path}")

    output: dict[str, str] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = _normalize_name(str(package.get("name", "")))
        version = str(package.get("version", "")).strip()
        if not name or not version:
            continue
        if name in SKIP_PACKAGES:
            continue
        existing = output.get(name)
        if existing is not None and existing != version:
            raise RuntimeError(f"conflicting locked versions for package '{name}': '{existing}' vs '{version}'")
        output[name] = version
    return output


def _fetch_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"failed to fetch {url}: HTTP {exc.code}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid JSON payload from {url}")
    return payload


def _fetch_project_payload(name: str, *, project_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cached = project_cache.get(name)
    if cached is not None:
        return cached
    payload = _fetch_json(f"https://pypi.org/pypi/{name}/json")
    project_cache[name] = payload
    return payload


def _fetch_release_payload(
    name: str,
    version: str,
    *,
    release_cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    cache_key = (name, version)
    cached = release_cache.get(cache_key)
    if cached is not None:
        return cached
    payload = _fetch_json(f"https://pypi.org/pypi/{name}/{version}/json")
    release_cache[cache_key] = payload
    return payload


def _extract_hashes_from_files(files: object, *, package_name: str, version: str) -> list[str]:
    if not isinstance(files, list):
        raise RuntimeError(f"invalid PyPI file list for {package_name}=={version}")
    hashes: set[str] = set()
    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        if bool(file_row.get("yanked", False)):
            continue
        digests = file_row.get("digests", {})
        if not isinstance(digests, dict):
            continue
        sha256 = str(digests.get("sha256", "")).strip().lower()
        if sha256:
            hashes.add(f"sha256:{sha256}")
    if not hashes:
        raise RuntimeError(f"no sha256 hashes found on PyPI for {package_name}=={version}")
    return sorted(hashes)


def _extract_release_metadata(payload: dict[str, Any], *, package_name: str, version: str) -> dict[str, Any]:
    files = payload.get("urls", [])
    hashes = _extract_hashes_from_files(files, package_name=package_name, version=version)
    raw_requires_dist = payload.get("info", {}).get("requires_dist", [])
    requires_dist: list[str] = []
    if isinstance(raw_requires_dist, list):
        for raw_requirement in raw_requires_dist:
            requirement = str(raw_requirement).strip()
            if requirement:
                requires_dist.append(requirement)
    return {
        "version": version,
        "hashes": hashes,
        "requires_dist": requires_dist,
    }


def _requirement_active_target_envs(
    requirement: Requirement,
    *,
    target_envs: list[dict[str, str]],
) -> list[dict[str, str]]:
    marker = requirement.marker
    if marker is None:
        return list(target_envs)
    active_envs: list[dict[str, str]] = []
    for env in target_envs:
        if marker.evaluate(env):
            active_envs.append(env)
    return active_envs


def _active_target_python_versions(
    requirements: list[Requirement],
    *,
    target_envs: list[dict[str, str]],
) -> list[str]:
    versions: set[str] = set()
    for requirement in requirements:
        for env in _requirement_active_target_envs(requirement, target_envs=target_envs):
            python_version = str(env.get("python_version", "")).strip()
            if python_version:
                versions.add(python_version)
    return sorted(versions, key=lambda value: tuple(int(part) for part in value.split(".")))


def _version_satisfies_constraints(
    version: str,
    *,
    requirements: list[Requirement],
    target_envs: list[dict[str, str]],
) -> bool:
    parsed_version = Version(version)
    for requirement in requirements:
        active_envs = _requirement_active_target_envs(requirement, target_envs=target_envs)
        if not active_envs:
            continue
        if requirement.specifier and parsed_version not in requirement.specifier:
            return False
    return True


def _files_support_target_pythons(files: object, *, target_python_versions: list[str]) -> bool:
    if not target_python_versions:
        return True
    if not isinstance(files, list):
        return False

    hashed_non_yanked_files: list[dict[str, Any]] = []
    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        if bool(file_row.get("yanked", False)):
            continue
        digests = file_row.get("digests", {})
        if not isinstance(digests, dict):
            continue
        sha256 = str(digests.get("sha256", "")).strip().lower()
        if not sha256:
            continue
        hashed_non_yanked_files.append(file_row)

    if not hashed_non_yanked_files:
        return False

    for target_python in target_python_versions:
        target_version = Version(f"{target_python}.0")
        supported = False
        for file_row in hashed_non_yanked_files:
            requires_python = str(file_row.get("requires_python", "")).strip()
            if not requires_python:
                supported = True
                break
            try:
                specifier = SpecifierSet(requires_python)
            except InvalidSpecifier:
                supported = True
                break
            if target_version in specifier:
                supported = True
                break
        if not supported:
            return False
    return True


def _select_dependency_version(
    package_name: str,
    *,
    requirements: list[Requirement],
    target_envs: list[dict[str, str]],
    project_cache: dict[str, dict[str, Any]],
) -> str:
    payload = _fetch_project_payload(package_name, project_cache=project_cache)
    releases = payload.get("releases", {})
    if not isinstance(releases, dict):
        raise RuntimeError(f"invalid PyPI project payload for {package_name}: missing releases")

    candidates: list[tuple[Version, str, object]] = []
    for raw_version, files in releases.items():
        version_text = str(raw_version).strip()
        if not version_text:
            continue
        try:
            parsed_version = Version(version_text)
        except InvalidVersion:
            continue
        candidates.append((parsed_version, version_text, files))

    if not candidates:
        raise RuntimeError(f"no release versions available for dependency '{package_name}'")

    candidates.sort(key=lambda row: row[0], reverse=True)
    target_python_versions = _active_target_python_versions(requirements, target_envs=target_envs)

    for allow_prerelease in (False, True):
        for parsed_version, version_text, files in candidates:
            if parsed_version.is_prerelease and not allow_prerelease:
                continue
            if not _version_satisfies_constraints(
                version_text,
                requirements=requirements,
                target_envs=target_envs,
            ):
                continue
            if not _files_support_target_pythons(files, target_python_versions=target_python_versions):
                continue
            return version_text

    constraint_text = ", ".join(sorted({str(requirement) for requirement in requirements}))
    raise RuntimeError(
        f"unable to resolve a version for dependency '{package_name}' "
        f"that satisfies constraints: {constraint_text}"
    )


def _resolve_catalog(root_packages: dict[str, str]) -> dict[str, dict[str, Any]]:
    target_envs = _build_target_envs()
    project_cache: dict[str, dict[str, Any]] = {}
    release_cache: dict[tuple[str, str], dict[str, Any]] = {}
    selected_versions = dict(root_packages)
    locked_names = set(root_packages)
    constraints: dict[str, dict[str, Requirement]] = {}
    catalog: dict[str, dict[str, Any]] = {}
    queue = list(sorted(selected_versions))

    while queue:
        package_name = queue.pop(0)
        version = selected_versions[package_name]
        payload = _fetch_release_payload(
            package_name,
            version,
            release_cache=release_cache,
        )
        catalog[package_name] = _extract_release_metadata(
            payload,
            package_name=package_name,
            version=version,
        )

        requires_dist = catalog[package_name].get("requires_dist", [])
        if not isinstance(requires_dist, list):
            continue

        for raw_requirement in requires_dist:
            requirement_text = str(raw_requirement).strip()
            if not requirement_text:
                continue
            requirement = Requirement(requirement_text)
            active_envs = _requirement_active_target_envs(requirement, target_envs=target_envs)
            if not active_envs:
                continue

            dependency_name = _normalize_name(requirement.name)
            if dependency_name in SKIP_PACKAGES:
                continue

            dep_constraints = constraints.setdefault(dependency_name, {})
            dep_constraints[str(requirement)] = requirement
            constraint_list = list(dep_constraints.values())

            selected = selected_versions.get(dependency_name)
            if selected is None:
                selected_versions[dependency_name] = _select_dependency_version(
                    dependency_name,
                    requirements=constraint_list,
                    target_envs=target_envs,
                    project_cache=project_cache,
                )
                queue.append(dependency_name)
                continue

            if _version_satisfies_constraints(
                selected,
                requirements=constraint_list,
                target_envs=target_envs,
            ):
                continue

            if dependency_name in locked_names:
                raise RuntimeError(
                    f"locked package '{dependency_name}=={selected}' does not satisfy constraints: "
                    + ", ".join(sorted(dep_constraints))
                )

            resolved_version = _select_dependency_version(
                dependency_name,
                requirements=constraint_list,
                target_envs=target_envs,
                project_cache=project_cache,
            )
            if resolved_version != selected:
                selected_versions[dependency_name] = resolved_version
                catalog.pop(dependency_name, None)
                queue.append(dependency_name)

    return dict(sorted(catalog.items()))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a fully-closed PyPI release hash catalog (including transitive dependencies) "
            "for package versions pinned in pylock.toml."
        )
    )
    parser.add_argument("--lock", default="pylock.toml", help="Path to pylock.toml")
    parser.add_argument(
        "--output",
        default=".github/locks/pypi_release_hashes.json",
        help="Path to output JSON hash catalog",
    )
    args = parser.parse_args()

    lock_path = Path(args.lock).resolve()
    if not lock_path.exists():
        raise SystemExit(f"lock file not found: {lock_path}")

    root_packages = _load_packages(lock_path)
    output = _resolve_catalog(root_packages)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(output)} package entries to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
