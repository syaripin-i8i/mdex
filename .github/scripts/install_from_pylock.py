from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
import tomllib


SKIP_PACKAGES = {"mdex-cli", "pip", "setuptools", "wheel"}
ALLOWED_HASH_ALGORITHMS = {"sha256"}


def _extract_hashes(package: dict[str, Any], *, lock_path: Path) -> set[str]:
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

    if not hashes:
        package_name = str(package.get("name", "")).strip() or "<unknown>"
        raise RuntimeError(
            f"missing supported artifact hashes for package '{package_name}' in {lock_path}; "
            "cannot use --require-hashes safely"
        )

    return hashes


def _requirements_from_pylock(lock_path: Path) -> list[str]:
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
        hashes = _extract_hashes(package, lock_path=lock_path)

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
