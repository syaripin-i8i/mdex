from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, validate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PROJECT_ROOT / "schemas"


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    existing_pythonpath = merged_env.get("PYTHONPATH", "")
    merged_env["PYTHONPATH"] = str(PROJECT_ROOT) if not existing_pythonpath else f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
    return subprocess.run(
        [sys.executable, "-m", "mdex.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        env=merged_env,
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
        "error.schema.json",
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


def test_cli_error_outputs_match_error_schema(quality_repo: Path, tmp_path: Path) -> None:
    db_path = tmp_path / "contract_error.db"
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
        str(tmp_path / "scan.json"),
        cwd=PROJECT_ROOT,
    )
    assert scan.returncode == 0

    missing_db_dir = tmp_path / "missing-db-dir"
    missing_db_dir.mkdir()
    missing_db = _run_cli("list", cwd=missing_db_dir)
    assert missing_db.returncode == 2
    missing_db_payload = json.loads(missing_db.stderr)
    _validate_payload(missing_db_payload, "error.schema.json")

    open_absolute = _run_cli("open", str((quality_repo / "design" / "root.md").resolve()), "--db", str(db_path), cwd=quality_repo)
    assert open_absolute.returncode == 2
    open_absolute_payload = json.loads(open_absolute.stderr)
    _validate_payload(open_absolute_payload, "error.schema.json")

    open_parent = _run_cli("open", "../outside.md", "--db", str(db_path), cwd=quality_repo)
    assert open_parent.returncode == 2
    open_parent_payload = json.loads(open_parent.stderr)
    _validate_payload(open_parent_payload, "error.schema.json")

    stamp_not_indexed = _run_cli("stamp", "design/not_indexed.md", "--db", str(db_path), cwd=quality_repo)
    assert stamp_not_indexed.returncode == 2
    stamp_not_indexed_payload = json.loads(stamp_not_indexed.stderr)
    _validate_payload(stamp_not_indexed_payload, "error.schema.json")
