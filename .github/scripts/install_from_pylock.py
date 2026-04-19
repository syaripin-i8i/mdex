from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


PACKAGE_BLOCK_RE = re.compile(r"(?ms)^\[\[packages\]\]\s*(.*?)(?=^\[\[packages\]\]|\Z)")
KV_RE = re.compile(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"')
SKIP_PACKAGES = {"mdex-cli", "pip", "setuptools", "wheel"}


def _parse_top_level_kv(block: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            break
        match = KV_RE.match(stripped)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def _requirements_from_pylock(lock_path: Path) -> list[str]:
    text = lock_path.read_text(encoding="utf-8")
    pinned: dict[str, str] = {}
    for match in PACKAGE_BLOCK_RE.finditer(text):
        fields = _parse_top_level_kv(match.group(1))
        name = fields.get("name", "").strip()
        version = fields.get("version", "").strip()
        if not name or not version:
            continue
        normalized = name.lower()
        if normalized in SKIP_PACKAGES:
            continue
        existing = pinned.get(normalized)
        if existing is not None and existing != version:
            raise RuntimeError(
                f"conflicting versions for package '{name}': '{existing}' vs '{version}' in {lock_path}"
            )
        pinned[normalized] = version

    requirements = [f"{name}=={version}" for name, version in sorted(pinned.items())]
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
        _run([sys.executable, "-m", "pip", "install", "--requirement", str(req_path)])
        editable = (args.editable or "").strip()
        if editable:
            _run([sys.executable, "-m", "pip", "install", "--editable", editable, "--no-deps"])
    finally:
        req_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
