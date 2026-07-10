from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "verify_release_metadata.py"
SPEC = importlib.util.spec_from_file_location("verify_release_metadata_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _errors(
    *,
    version: str = "0.5.0",
    changelog: str = "## [Unreleased]\n\n## [0.5.0] - 2026-07-11\n",
    security: str = "| Version | Supported |\n|---|---|\n| 0.5.x | Yes |\n| <= 0.4.x | No |\n",
) -> list[str]:
    return MODULE.verify_release_metadata(
        version,
        changelog_text=changelog,
        security_text=security,
    )


def test_accepts_valid_metadata_with_crlf() -> None:
    assert _errors(
        changelog="## [Unreleased]\r\n\r\n## [0.5.0] - 2026-07-11\r\n",
        security="| Version | Supported |\r\n|---|---|\r\n| 0.5.x | Yes |\r\n| <= 0.4.x | No |\r\n",
    ) == []


@pytest.mark.parametrize(
    ("changelog", "expected"),
    [
        ("ships in 0.5.0\n", "found 0"),
        (
            "## [0.5.0] - 2026-07-11\n## [0.5.0] - 2026-07-12\n",
            "found 2",
        ),
        ("## [0.5.0] - 2026-02-30\n", "not a valid calendar date"),
    ],
)
def test_rejects_invalid_changelog_metadata(changelog: str, expected: str) -> None:
    assert any(expected in error for error in _errors(changelog=changelog))


@pytest.mark.parametrize(
    ("security", "expected"),
    [
        ("| 0.4.x | Yes |\n| <= 0.3.x | No |\n", "0.5.x"),
        ("| 0.5.x | Yes |\n| 0.4.x | Yes |\n", "found ['0.5.x', '0.4.x']"),
    ],
)
def test_rejects_invalid_security_metadata(security: str, expected: str) -> None:
    assert any(expected in error for error in _errors(security=security))


def test_rejects_non_stable_semver() -> None:
    assert _errors(version="0.5.0rc1") == [
        "release version must be stable SemVer X.Y.Z, got '0.5.0rc1'"
    ]
