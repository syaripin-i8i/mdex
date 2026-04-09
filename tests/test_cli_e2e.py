from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_quality_repo_scan_first_related_enrich_e2e(tmp_path: Path) -> None:
    db_path = tmp_path / "quality_cli_e2e.db"
    output_json = tmp_path / "quality_cli_e2e.json"

    scan = _run_cli(
        "scan",
        "--root",
        "tests/fixtures/quality_repo",
        "--config",
        "control/scan_config.json",
        "--output",
        str(output_json),
        "--db",
        str(db_path),
    )
    assert scan.returncode == 0
    scan_payload = json.loads(scan.stdout)
    assert scan_payload["nodes"] == 6
    assert scan_payload["edges"]["total"] == 8
    assert scan_payload["edges"]["resolved"] == 6
    assert scan_payload["edges"]["unresolved"] == 2
    assert scan_payload["edges"]["resolution_rate"] == 75.0

    first = _run_cli("first", "design/root.md", "--db", str(db_path), "--limit", "2")
    assert first.returncode == 0
    first_payload = json.loads(first.stdout)
    assert [item["id"] for item in first_payload["prerequisites"]] == [
        "spec/b.md",
        "decision/a.md",
    ]

    related = _run_cli("related", "design/root.md", "--db", str(db_path), "--limit", "3")
    assert related.returncode == 0
    related_payload = json.loads(related.stdout)
    assert [item["id"] for item in related_payload["related"][:3]] == [
        "decision/a.md",
        "spec/b.md",
        "tasks/pending/T20260101000001.md",
    ]

    enrich = _run_cli(
        "enrich",
        "design/root.md",
        "--db",
        str(db_path),
        "--summary",
        "Root Design の要点を短く更新した summary",
    )
    assert enrich.returncode == 0
    enrich_payload = json.loads(enrich.stdout)
    assert enrich_payload["status"] == "enriched"
    assert enrich_payload["node_id"] == "design/root.md"
    assert enrich_payload["summary_source"] == "agent"
    assert enrich_payload["skipped"] is False
