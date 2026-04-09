from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"


def _quality_repo_section() -> str:
    text = README_PATH.read_text(encoding="utf-8")
    start = text.index("## 再現サンプル（fixtures/quality_repo）")
    end = text.index("## 全出力は JSON")
    return text[start:end]


def test_readme_quality_repo_commands_are_in_sync() -> None:
    section = _quality_repo_section()
    commands = [
        "mdex scan --root tests/fixtures/quality_repo --db .tmp_quality.db",
        "mdex first design/root.md --db .tmp_quality.db --limit 2",
        "mdex related design/root.md --db .tmp_quality.db --limit 3",
        'mdex enrich design/root.md --db .tmp_quality.db --summary "Root Design の要点を短く更新した summary"',
    ]
    for command in commands:
        assert command in section


def test_readme_quality_repo_expected_outputs_are_in_sync() -> None:
    section = _quality_repo_section()

    assert '"nodes": 6' in section
    assert '"total": 8' in section
    assert '"resolved": 6' in section
    assert '"unresolved": 2' in section
    assert '"resolution_rate": 75.0' in section

    prereq_start = section.index('"prerequisites": [')
    prereq_end = section.index("```", prereq_start)
    prereq_block = section[prereq_start:prereq_end]
    assert prereq_block.index('"id": "spec/b.md"') < prereq_block.index('"id": "decision/a.md"')

    related_start = section.index('"related": [')
    related_end = section.index("```", related_start)
    related_block = section[related_start:related_end]
    assert '"id": "decision/a.md"' in related_block
    assert '"id": "spec/b.md"' in related_block
    assert '"id": "tasks/pending/T20260101000001.md"' in related_block

    assert '"status": "enriched"' in section
    assert '"node_id": "design/root.md"' in section
    assert '"summary_source": "agent"' in section
    assert '"skipped": false' in section
    assert "`summary_source` は `agent` のまま保持される" in section
