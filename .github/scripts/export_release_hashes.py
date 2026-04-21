from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


def _load_packages(lock_path: Path) -> list[tuple[str, str]]:
    if tomllib is None:
        raise RuntimeError("tomllib is required to generate release-hash catalog")
    loaded = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = loaded.get("packages", [])
    if not isinstance(packages, list):
        raise RuntimeError(f"invalid pylock format (packages must be a list): {lock_path}")

    pairs: set[tuple[str, str]] = set()
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = str(package.get("name", "")).strip().lower()
        version = str(package.get("version", "")).strip()
        if not name or not version:
            continue
        pairs.add((name, version))
    return sorted(pairs)


def _fetch_release_hashes(name: str, version: str) -> list[str]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"failed to fetch {name}=={version} from PyPI: HTTP {exc.code}") from exc

    files = payload.get("urls", [])
    if not isinstance(files, list):
        raise RuntimeError(f"invalid PyPI response for {name}=={version}")

    hashes: set[str] = set()
    for file_row in files:
        if not isinstance(file_row, dict):
            continue
        digests = file_row.get("digests", {})
        if not isinstance(digests, dict):
            continue
        sha256 = str(digests.get("sha256", "")).strip().lower()
        if sha256:
            hashes.add(f"sha256:{sha256}")
    if not hashes:
        raise RuntimeError(f"no sha256 hashes found on PyPI for {name}=={version}")
    return sorted(hashes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export all PyPI release hashes for package versions pinned in pylock.toml."
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

    packages = _load_packages(lock_path)
    output: dict[str, dict[str, object]] = {}
    for name, version in packages:
        if name in {"mdex-cli", "pip", "setuptools", "wheel"}:
            continue
        hashes = _fetch_release_hashes(name, version)
        output[name] = {
            "version": version,
            "hashes": hashes,
        }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(output)} package entries to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
