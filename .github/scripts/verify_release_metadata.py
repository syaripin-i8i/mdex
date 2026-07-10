#!/usr/bin/env python3
"""Verify release-only changelog and security metadata."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STABLE_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
CHANGELOG_RELEASE_RE = re.compile(
    r"^## \[(?P<version>\d+\.\d+\.\d+)\] - (?P<date>\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)


def verify_release_metadata(
    version: str,
    *,
    changelog_text: str,
    security_text: str,
) -> list[str]:
    """Return release metadata errors for a stable package version."""

    version_match = STABLE_SEMVER_RE.fullmatch(version)
    if version_match is None:
        return [f"release version must be stable SemVer X.Y.Z, got {version!r}"]

    errors: list[str] = []
    release_rows = [
        match for match in CHANGELOG_RELEASE_RE.finditer(changelog_text) if match.group("version") == version
    ]
    if len(release_rows) != 1:
        errors.append(
            f"CHANGELOG.md must contain exactly one formal heading for {version}; found {len(release_rows)}"
        )
    else:
        release_date = release_rows[0].group("date")
        try:
            date.fromisoformat(release_date)
        except ValueError:
            errors.append(f"CHANGELOG.md release date is not a valid calendar date: {release_date}")

    expected_line = f"{version_match.group(1)}.{version_match.group(2)}.x"
    supported_rows: list[str] = []
    for raw_line in security_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 2 and cells[1].casefold() == "yes":
            supported_rows.append(cells[0])

    if supported_rows != [expected_line]:
        errors.append(
            "SECURITY.md must mark only the release minor as supported: "
            f"expected [{expected_line!r}], found {supported_rows!r}"
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, default=PROJECT_ROOT / "CHANGELOG.md")
    parser.add_argument("--security", type=Path, default=PROJECT_ROOT / "SECURITY.md")
    args = parser.parse_args(argv)

    errors = verify_release_metadata(
        args.version,
        changelog_text=args.changelog.read_text(encoding="utf-8"),
        security_text=args.security.read_text(encoding="utf-8"),
    )
    if errors:
        for error in errors:
            print(f"release metadata error: {error}", file=sys.stderr)
        return 1

    print(f"release metadata verified for {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
