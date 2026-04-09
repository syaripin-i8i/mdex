from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "repo"
    target = tmp_path / "repo"
    shutil.copytree(source, target)
    return target


@pytest.fixture()
def build_config() -> dict[str, object]:
    return {
        "exclude_patterns": [],
        "node_type_map": {
            "spec": ["spec", "specs"],
        },
        "summary_max_sentences": 2,
        "summary_max_chars": 120,
    }
