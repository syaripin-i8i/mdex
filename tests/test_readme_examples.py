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
        "mdex scan --root tests/fixtures/quality_repo --db .mdex/quality_example.db --output .mdex/quality_example.json",
        'mdex start "root decision" --db .mdex/quality_example.db --limit 5',
        "mdex impact design/root.md --db .mdex/quality_example.db",
        'mdex finish --task "root fix" --db .mdex/quality_example.db --dry-run',
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

    start_block_start = section.index('"task": "root decision"')
    start_block_end = section.index("```", start_block_start)
    start_block = section[start_block_start:start_block_end]
    assert '"index_status": {' in start_block
    assert '"fresh": true' in start_block
    assert '"entrypoint_reason": "ranked_entrypoint_available"' in start_block
    assert start_block.index('"id": "spec/b.md"') < start_block.index('"id": "decision/a.md"')
    assert '"id": "design/root.md"' in start_block
    assert '"recommended_next_actions": [' in start_block
    assert '"recommended_next_actions_v2": [' in start_block
    assert '"command": "mdex"' in start_block
    assert '"args": ["open", "spec/b.md"]' in start_block

    impact_start = section.index('"inputs": ["design/root.md"]')
    impact_end = section.index("```", impact_start)
    impact_block = section[impact_start:impact_end]
    assert '"id": "design/root.md"' in impact_block
    assert '"id": "tasks/pending/T20260101000001.md"' in impact_block
    assert '"id": "decision/a.md"' in impact_block

    assert '"task": "root fix"' in section
    assert '"status": "success"' in section
    assert '"dry_run": true' in section
    assert '"noop": true' in section
    assert '"noop_reason": "dry-run completed with no changed files and no enrich candidates"' in section
    assert '"requires_manual_targeting": false' in section
