from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - exercised in py3.10 runtime
    tomllib = None  # type: ignore[assignment]


SKIP_PACKAGES = {"mdex-cli", "pip", "setuptools", "wheel"}
ALLOWED_HASH_ALGORITHMS = {"sha256"}
PACKAGE_BLOCK_RE = re.compile(r"(?ms)^\s*\[\[packages\]\]\s*(.*?)(?=^\s*\[\[packages\]\]|\Z)")
TOP_LEVEL_KV_RE = re.compile(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"')
SHA256_RE = re.compile(r'(?m)^\s*sha256\s*=\s*"([^"]+)"\s*$')
SUPPLEMENTAL_HASH_PATH = Path(__file__).resolve().parent.parent / "locks" / "pypi_release_hashes.json"


def _parse_top_level_kv(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            break
        match = TOP_LEVEL_KV_RE.match(stripped)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def _load_supplemental_hashes() -> dict[str, dict[str, Any]]:
    if not SUPPLEMENTAL_HASH_PATH.exists():
        return {}
    loaded = json.loads(SUPPLEMENTAL_HASH_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for raw_name, raw_entry in loaded.items():
        if not isinstance(raw_entry, dict):
            continue
        output[str(raw_name).strip().lower()] = raw_entry
    return output


def _extract_hashes(
    package: dict[str, Any],
    *,
    lock_path: Path,
    supplemental_hashes: dict[str, dict[str, Any]],
) -> set[str]:
    hashes: set[str] = set()

    def _collect_from_hash_map(hash_map: object) -> None:
        if not isinstance(hash_map, dict):
            return
        for raw_algorithm, raw_value in hash_map.items():
            algorithm = str(raw_algorithm).strip().lower()
            if algorithm not in ALLOWED_HASH_ALGORITHMS:
                continue
            value = str(raw_value).strip().lower()
            if value:
                hashes.add(f"{algorithm}:{value}")

    wheels = package.get("wheels")
    if isinstance(wheels, list):
        for wheel in wheels:
            if not isinstance(wheel, dict):
                continue
            _collect_from_hash_map(wheel.get("hashes"))

    sdist = package.get("sdist")
    if isinstance(sdist, dict):
        _collect_from_hash_map(sdist.get("hashes"))

    package_name = str(package.get("name", "")).strip().lower()
    package_version = str(package.get("version", "")).strip()
    supplemental = supplemental_hashes.get(package_name)
    if supplemental is not None:
        supplemental_version = str(supplemental.get("version", "")).strip()
        if supplemental_version and supplemental_version != package_version:
            raise RuntimeError(
                f"supplemental hash version mismatch for package '{package_name}': "
                f"expected {package_version}, found {supplemental_version}"
            )
        extra_hashes = supplemental.get("hashes", [])
        if isinstance(extra_hashes, list):
            for raw_hash in extra_hashes:
                value = str(raw_hash).strip().lower()
                if value.startswith("sha256:"):
                    hashes.add(value)

    if not hashes:
        package_name = package_name or "<unknown>"
        raise RuntimeError(
            f"missing supported artifact hashes for package '{package_name}' in {lock_path}; "
            "cannot use --require-hashes safely"
        )

    return hashes


def _requirements_from_pylock_with_tomllib(
    lock_path: Path,
    *,
    supplemental_hashes: dict[str, dict[str, Any]],
) -> list[str]:
    if tomllib is None:
        raise RuntimeError("tomllib is unavailable")
    loaded = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = loaded.get("packages", [])
    if not isinstance(packages, list):
        raise RuntimeError(f"invalid pylock format (packages must be a list): {lock_path}")

    pinned: dict[str, dict[str, Any]] = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = str(package.get("name", "")).strip()
        version = str(package.get("version", "")).strip()
        if not name or not version:
            continue
        normalized = name.lower()
        if normalized in SKIP_PACKAGES:
            continue
        hashes = _extract_hashes(
            package,
            lock_path=lock_path,
            supplemental_hashes=supplemental_hashes,
        )

        existing = pinned.get(normalized)
        if existing is not None and existing["version"] != version:
            raise RuntimeError(
                f"conflicting versions for package '{name}': '{existing['version']}' vs '{version}' in {lock_path}"
            )
        if existing is None:
            pinned[normalized] = {"version": version, "hashes": set(hashes)}
        else:
            existing["hashes"].update(hashes)

    requirements: list[str] = []
    for name, package in sorted(pinned.items()):
        version = str(package["version"])
        hash_args = " ".join(f"--hash={value}" for value in sorted(package["hashes"]))
        requirements.append(f"{name}=={version} {hash_args}".rstrip())
    if not requirements:
        raise RuntimeError(f"no installable pinned packages found in {lock_path}")
    return requirements


def _requirements_from_pylock_regex_fallback(
    lock_path: Path,
    *,
    supplemental_hashes: dict[str, dict[str, Any]],
) -> list[str]:
    text = lock_path.read_text(encoding="utf-8")
    pinned: dict[str, dict[str, Any]] = {}

    for match in PACKAGE_BLOCK_RE.finditer(text):
        block = match.group(1)
        fields = _parse_top_level_kv(block)
        name = fields.get("name", "").strip()
        version = fields.get("version", "").strip()
        if not name or not version:
            continue
        normalized = name.lower()
        if normalized in SKIP_PACKAGES:
            continue

        hashes = {f"sha256:{value.lower()}" for value in SHA256_RE.findall(block)}
        supplemental = supplemental_hashes.get(normalized, {})
        supplemental_version = str(supplemental.get("version", "")).strip()
        if supplemental_version and supplemental_version != version:
            raise RuntimeError(
                f"supplemental hash version mismatch for package '{name}': "
                f"expected {version}, found {supplemental_version}"
            )
        extra_hashes = supplemental.get("hashes", [])
        if isinstance(extra_hashes, list):
            for raw_hash in extra_hashes:
                value = str(raw_hash).strip().lower()
                if value.startswith("sha256:"):
                    hashes.add(value)

        if not hashes:
            raise RuntimeError(
                f"missing supported artifact hashes for package '{name}' in {lock_path}; "
                "cannot use --require-hashes safely"
            )

        existing = pinned.get(normalized)
        if existing is not None and existing["version"] != version:
            raise RuntimeError(
                f"conflicting versions for package '{name}': '{existing['version']}' vs '{version}' in {lock_path}"
            )
        if existing is None:
            pinned[normalized] = {"version": version, "hashes": set(hashes)}
        else:
            existing["hashes"].update(hashes)

    requirements: list[str] = []
    for name, package in sorted(pinned.items()):
        version = str(package["version"])
        hash_args = " ".join(f"--hash={value}" for value in sorted(package["hashes"]))
        requirements.append(f"{name}=={version} {hash_args}".rstrip())
    if not requirements:
        raise RuntimeError(f"no installable pinned packages found in {lock_path}")
    return requirements


def _requirements_from_pylock(lock_path: Path) -> list[str]:
    supplemental_hashes = _load_supplemental_hashes()
    if tomllib is not None:
        return _requirements_from_pylock_with_tomllib(
            lock_path,
            supplemental_hashes=supplemental_hashes,
        )
    return _requirements_from_pylock_regex_fallback(
        lock_path,
        supplemental_hashes=supplemental_hashes,
    )


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install pinned dependencies from pylock.toml.")
    parser.add_argument("--lock", default="pylock.toml", help="Path to pylock.toml")
    parser.add_argument(
        "--editable",
        default=".",
        help="Editable project path to install with --no-deps (empty string disables editable install)",
    )
    args = parser.parse_args()

    lock_path = Path(args.lock).resolve()
    if not lock_path.exists():
        raise SystemExit(f"lock file not found: {lock_path}")

    requirements = _requirements_from_pylock(lock_path)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as req_file:
        req_file.write("\n".join(requirements))
        req_file.write("\n")
        req_path = Path(req_file.name)

    try:
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--requirement",
                str(req_path),
            ]
        )
        editable = (args.editable or "").strip()
        if editable:
            _run([sys.executable, "-m", "pip", "install", "--editable", editable, "--no-deps"])
    finally:
        req_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
