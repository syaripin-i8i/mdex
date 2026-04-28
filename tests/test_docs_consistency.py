from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from mdex import cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = PROJECT_ROOT / "docs"
ARCHIVE_DIR = DOCS_DIR / "archive"
README_PATH = PROJECT_ROOT / "README.md"
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"


def _markdown_docs_outside_archive() -> list[Path]:
    docs: list[Path] = []
    for path in DOCS_DIR.rglob("*.md"):
        try:
            path.relative_to(ARCHIVE_DIR)
        except ValueError:
            docs.append(path)
    return sorted(docs)


def _readme_text() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _parse_markdown_table(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        if all(set(cell) <= {"-"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _strip_code_ticks(value: str) -> str:
    return value.strip().strip("`").strip()


def _code_paths(value: str) -> list[str]:
    return [match.strip() for match in re.findall(r"`([^`]+)`", value)]


def _gitignore_patterns() -> set[str]:
    patterns: set[str] = set()
    for raw_line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            patterns.add(line)
    return patterns


def _schema_for_contract_command(command_cell: str) -> Path | None:
    normalized = _strip_code_ticks(command_cell)
    if normalized == "scan":
        return SCHEMAS_DIR / "scan.schema.json"
    if normalized == "start":
        return SCHEMAS_DIR / "start.schema.json"
    if normalized == "context":
        return SCHEMAS_DIR / "context.schema.json"
    if normalized == "impact":
        return SCHEMAS_DIR / "impact.schema.json"
    if normalized == "finish":
        return SCHEMAS_DIR / "finish.schema.json"
    if normalized == "db resolution error":
        return SCHEMAS_DIR / "error.schema.json"
    return None


def _load_schema(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _resolve_ref(ref: str, root_schema: dict[str, object]) -> dict[str, object] | None:
    if not ref.startswith("#/"):
        return None
    current: object = root_schema
    for part in ref[2:].split("/"):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    if isinstance(current, dict):
        return current
    return None


def _schema_has_property_path(schema: dict[str, object], dotted_path: str) -> bool:
    current: dict[str, object] | None = schema
    root = schema
    for segment in dotted_path.split("."):
        if current is None:
            return False
        if "$ref" in current:
            ref = current.get("$ref")
            if not isinstance(ref, str):
                return False
            current = _resolve_ref(ref, root)
            if current is None:
                return False
        properties = current.get("properties")
        if not isinstance(properties, dict):
            return False
        next_node = properties.get(segment)
        if not isinstance(next_node, dict):
            return False
        current = next_node
    return True


def test_design_declares_phase_a_complete() -> None:
    design_text = (DOCS_DIR / "design.md").read_text(encoding="utf-8")
    assert "Phase A（導線）: 完了" in design_text


def test_non_archive_docs_do_not_contain_unstarted_terms() -> None:
    blocked_terms = ("未着手", "未実装")
    offenders: list[str] = []
    for path in _markdown_docs_outside_archive():
        text = path.read_text(encoding="utf-8")
        for term in blocked_terms:
            if term in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT).as_posix()} contains '{term}'")
    assert not offenders, f"found stale planning terms outside docs/archive: {offenders}"


def test_readme_output_contract_primary_keys_match_schema_properties() -> None:
    readme = _readme_text()
    section = _section(readme, "### Output Contract", "### Schema Contracts")
    rows = _parse_markdown_table(section)

    failures: list[str] = []
    for cells in rows[1:]:
        if len(cells) < 2:
            continue
        command_cell, key_cell = cells[0], cells[1]
        schema_path = _schema_for_contract_command(command_cell)
        if schema_path is None:
            continue
        schema = _load_schema(schema_path)
        keys = [_strip_code_ticks(part) for part in key_cell.split(",") if _strip_code_ticks(part)]
        for key in keys:
            if not _schema_has_property_path(schema, key):
                failures.append(
                    f"{command_cell} -> {key} is not declared in {schema_path.relative_to(PROJECT_ROOT).as_posix()}"
                )
    assert not failures, "README Output Contract keys drifted from schema definitions:\n" + "\n".join(failures)


def test_agent_commands_exist_in_cli_parser() -> None:
    agent_text = (PROJECT_ROOT / "AGENT.md").read_text(encoding="utf-8")
    documented = sorted(set(re.findall(r"\bmdex\s+([a-z][a-z0-9_-]*)\b", agent_text)))
    parser = cli._build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if getattr(action, "dest", "") == "command" and hasattr(action, "choices")
    )
    available = set(subparsers.choices.keys())

    missing = [name for name in documented if name not in available]
    assert not missing, f"AGENT.md documents unknown CLI commands: {missing}"


def test_readme_source_of_truth_paths_exist() -> None:
    readme = _readme_text()
    section = _section(readme, "## Source of Truth", "## Project Operations")
    rows = _parse_markdown_table(section)

    missing: list[str] = []
    for cells in rows[1:]:
        if len(cells) < 2:
            continue
        source_cell = cells[1]
        for raw_path in _code_paths(source_cell):
            candidate = PROJECT_ROOT / raw_path
            if not candidate.exists():
                missing.append(raw_path)
    assert not missing, f"README Source of Truth contains missing files: {missing}"


def test_local_only_autonomous_artifacts_are_gitignored() -> None:
    required_patterns = [
        "/BASELINE_INSTRUCTIONS.md",
        "/docs/autonomous_development_workflow.md",
        "/docs/autonomous_development_consent.md",
        "/docs/current_workflow.md",
        "/docs/autonomous_reports/",
    ]

    gitignore_patterns = _gitignore_patterns()
    missing = [pattern for pattern in required_patterns if pattern not in gitignore_patterns]
    assert not missing, f"local-only autonomous artifacts must stay gitignored: {missing}"

    protected_paths = [
        "BASELINE_INSTRUCTIONS.md",
        "docs/autonomous_development_workflow.md",
        "docs/autonomous_development_consent.md",
        "docs/current_workflow.md",
        "docs/autonomous_reports/example.md",
    ]
    result = subprocess.run(
        ["git", "ls-files", "--", *protected_paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    tracked = [line for line in result.stdout.splitlines() if line.strip()]
    assert not tracked, f"local-only autonomous artifacts must not be tracked: {tracked}"
