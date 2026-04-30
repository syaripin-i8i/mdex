from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


# Pytest loads conftest before importing test modules, so expose a consistent
# tomllib module name across supported Python versions.
sys.modules.setdefault("tomllib", tomllib)


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "repo"
    target = tmp_path / "repo"
    shutil.copytree(source, target)
    return target


@pytest.fixture()
def build_config() -> dict[str, object]:
    return {
        "include_extensions": [".md", ".json", ".jsonl"],
        "exclude_patterns": [],
        "node_type_map": {
            "spec": ["spec", "specs"],
        },
        "summary_max_sentences": 2,
        "summary_max_chars": 120,
    }


@pytest.fixture()
def quality_repo(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "quality_repo"
    target = tmp_path / "quality_repo"
    shutil.copytree(source, target)
    return target


@pytest.fixture()
def quality_config() -> dict[str, object]:
    return {
        "include_extensions": [".md", ".json", ".jsonl"],
        "exclude_patterns": [],
        "node_type_map": {
            "design": ["design"],
            "decision": ["decision"],
            "spec": ["spec"],
            "task": ["tasks", "task"],
            "reference": ["notes", "note"],
        },
        "summary_max_sentences": 3,
        "summary_max_chars": 200,
    }
