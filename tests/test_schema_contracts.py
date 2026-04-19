from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, validate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PROJECT_ROOT / "schemas"


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _load_schema(schema_filename: str) -> dict[str, object]:
    raw = (SCHEMA_DIR / schema_filename).read_text(encoding="utf-8")
    loaded = json.loads(raw)
    assert isinstance(loaded, dict)
    return loaded


def _validate_payload(payload: dict[str, object], schema_filename: str) -> None:
    schema = _load_schema(schema_filename)
    Draft202012Validator.check_schema(schema)
    validate(instance=payload, schema=schema)


def test_schema_files_are_valid_draft_2020_12() -> None:
    for name in (
        "scan.schema.json",
        "start.schema.json",
        "context.schema.json",
        "impact.schema.json",
        "finish.schema.json",
    ):
        schema = _load_schema(name)
        Draft202012Validator.check_schema(schema)


def test_cli_outputs_match_contract_schemas(quality_repo: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "contract.db"
    scan_json = tmp_path / "scan.json"
    config_path = PROJECT_ROOT / "control" / "scan_config.json"

    scan = _run_cli(
        "scan",
        "--root",
        str(quality_repo),
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        "--output",
        str(scan_json),
        cwd=PROJECT_ROOT,
    )
    assert scan.returncode == 0
    scan_payload = json.loads(scan.stdout)
    _validate_payload(scan_payload, "scan.schema.json")

    start = _run_cli("start", "root decision", "--db", str(db_path), cwd=quality_repo)
    assert start.returncode == 0
    start_payload = json.loads(start.stdout)
    _validate_payload(start_payload, "start.schema.json")

    context = _run_cli("context", "root decision", "--db", str(db_path), "--actionable", cwd=quality_repo)
    assert context.returncode == 0
    context_payload = json.loads(context.stdout)
    _validate_payload(context_payload, "context.schema.json")

    impact = _run_cli("impact", "design/root.md", "--db", str(db_path), cwd=quality_repo)
    assert impact.returncode == 0
    impact_payload = json.loads(impact.stdout)
    _validate_payload(impact_payload, "impact.schema.json")

    finish = _run_cli("finish", "--task", "root fix", "--db", str(db_path), "--dry-run", cwd=quality_repo)
    assert finish.returncode == 0
    finish_payload = json.loads(finish.stdout)
    _validate_payload(finish_payload, "finish.schema.json")
