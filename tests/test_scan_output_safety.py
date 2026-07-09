from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mdex import indexer
from mdex.output_paths import ensure_distinct_scan_outputs
from mdex.store import list_index_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT)
        if not existing_pythonpath
        else f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
    )
    return subprocess.run(
        [sys.executable, "-m", "mdex.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,
        env=env,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _repo_with_source(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "source.md").write_text("# Source\n", encoding="utf-8")
    return repo


def _scan_manifest(db_path: Path) -> dict[str, object]:
    metadata = list_index_metadata(str(db_path))
    assert "scan_manifest" in metadata, "scan must persist a manifest for safe follow-up operations"
    loaded = json.loads(metadata["scan_manifest"])
    assert isinstance(loaded, dict)
    return loaded


def test_write_json_is_atomic_and_cleans_up_temp_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "index.json"
    output.write_text("original\n", encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(indexer.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        indexer.write_json({"nodes": [], "edges": []}, str(output))

    assert output.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".index.json.*.tmp")) == []


def test_write_sqlite_cleans_up_temp_when_schema_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "index.db"

    def fail_schema(_cursor: sqlite3.Cursor) -> None:
        raise sqlite3.OperationalError("schema failed")

    monkeypatch.setattr(indexer, "_create_schema", fail_schema)

    with pytest.raises(sqlite3.OperationalError, match="schema failed"):
        indexer.write_sqlite(
            {"generated": "now", "scan_root": str(tmp_path), "nodes": [], "edges": []},
            str(output),
        )

    assert not output.exists()
    assert list(tmp_path.glob(".index.*.tmp")) == []


def test_scan_rejects_identical_db_and_json_output(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    output = repo / "index.db"

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(output),
        "--output",
        str(output),
        cwd=repo,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "scan failed"
    assert "must be different" in payload["detail"]
    assert not output.exists()


def test_scan_rejects_hardlinked_db_and_json_output(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    db_path = repo / "index.db"
    json_path = repo / "index.json"
    db_path.write_text("sentinel", encoding="utf-8")
    try:
        os.link(db_path, json_path)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(db_path),
        "--output",
        str(json_path),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "must be different" in json.loads(result.stderr)["detail"]
    assert db_path.read_text(encoding="utf-8") == "sentinel"


def test_scan_rejects_json_output_symlinked_to_db(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    db_path = repo / "index.db"
    json_path = repo / "index.json"
    db_path.write_text("sentinel", encoding="utf-8")
    try:
        json_path.symlink_to(db_path)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(db_path),
        "--output",
        str(json_path),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "must be different" in json.loads(result.stderr)["detail"]
    assert db_path.read_text(encoding="utf-8") == "sentinel"


@pytest.mark.parametrize("reverse", [False, True])
def test_scan_rejects_outputs_that_collide_with_pair_lock_paths(
    tmp_path: Path,
    reverse: bool,
) -> None:
    repo = _repo_with_source(tmp_path)
    if reverse:
        db_path = repo / ".mdex" / ".index.json.lock"
        json_path = repo / ".mdex" / "index.json"
    else:
        db_path = repo / ".mdex" / "index.db"
        json_path = repo / ".mdex" / ".index.db.lock"

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(db_path),
        "--output",
        str(json_path),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "reserved .lock filename suffix" in json.loads(result.stderr)["detail"]
    assert not db_path.exists()
    assert not json_path.exists()


def test_scan_rejects_reserved_lock_suffix_from_config(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    _write_json(
        repo / "control" / "scan_config.json",
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/custom.lock",
        },
    )

    result = _run_cli("scan", "--db", str(repo / ".mdex" / "index.db"), cwd=repo)

    assert result.returncode == 2
    assert "reserved .lock filename suffix" in json.loads(result.stderr)["detail"]


@pytest.mark.parametrize(
    ("db_name", "json_name"),
    [
        ("index.db", "INDEX.DB"),
        ("café.db", "cafe\u0301.db"),
        ("index.db", ".INDEX.DB.LOCK"),
    ],
)
def test_output_identity_is_conservative_across_case_and_unicode_aliases(
    tmp_path: Path,
    db_name: str,
    json_name: str,
) -> None:
    with pytest.raises(ValueError, match="must be different|lock paths|reserved .lock"):
        ensure_distinct_scan_outputs(tmp_path / db_name, tmp_path / json_name)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "index.db.",
        "index.json ",
        "CON.db",
        "nul.json",
        "CONIN$.db",
        "CONOUT$.json",
        "COM¹.log",
        "LPT².txt",
        "CON .txt",
        "AUX  .txt",
    ],
)
def test_scan_output_names_reject_windows_aliases(tmp_path: Path, unsafe_name: str) -> None:
    with pytest.raises(ValueError, match="dot or space|reserved on Windows"):
        ensure_distinct_scan_outputs(tmp_path / unsafe_name, tmp_path / "safe.json")


def test_scan_rejects_indexed_source_that_collides_with_output_lock(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    lock_source = repo / ".index.db.lock"
    lock_source.write_bytes(b"")
    config_path = repo / "control" / "scan_config.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".lock"],
            "use_default_exclude_patterns": False,
            "exclude_patterns": [],
            "output_file": ".mdex/unused.json",
        },
    )

    result = _run_cli(
        "scan",
        "--config",
        str(config_path),
        "--db",
        str(repo / "index.db"),
        "--output",
        str(repo / "index.json"),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "lock path must not overwrite indexed source" in json.loads(result.stderr)["detail"]
    assert lock_source.read_bytes() == b""


def test_scan_rejects_config_output_outside_repo(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    escaped = tmp_path / "escaped.json"
    escaped.write_text("sentinel", encoding="utf-8")
    _write_json(
        repo / "control" / "scan_config.json",
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": "../escaped.json",
        },
    )

    result = _run_cli("scan", "--db", str(repo / "index.db"), cwd=repo)

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "scan failed"
    assert "output_file must stay within repo" in payload["detail"]
    assert escaped.read_text(encoding="utf-8") == "sentinel"
    assert not (repo / "index.db").exists()


def test_scan_rejects_config_output_symlinked_outside_repo(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    escaped = tmp_path / "escaped.json"
    escaped.write_text("sentinel", encoding="utf-8")
    output_link = repo / "linked.json"
    try:
        output_link.symlink_to(escaped)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    _write_json(
        repo / "control" / "scan_config.json",
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": "linked.json",
        },
    )

    result = _run_cli("scan", "--db", str(repo / "index.db"), cwd=repo)

    assert result.returncode == 2
    assert "output_file must stay within repo" in json.loads(result.stderr)["detail"]
    assert escaped.read_text(encoding="utf-8") == "sentinel"


def test_scan_allows_explicit_output_outside_repo(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    output = tmp_path / "explicit.json"

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(repo / "index.db"),
        "--output",
        str(output),
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert "_source_paths" not in json.loads(output.read_text(encoding="utf-8"))


def test_scan_rejects_json_output_that_is_an_indexed_source(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    source = repo / "source.md"

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(repo / "index.db"),
        "--output",
        str(source),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "must not overwrite indexed source" in json.loads(result.stderr)["detail"]
    assert source.read_text(encoding="utf-8") == "# Source\n"
    assert not (repo / "index.db").exists()


def test_scan_rejects_database_output_that_is_an_indexed_source(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    source = repo / "source.md"

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(source),
        "--output",
        str(repo / "index.json"),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "must not overwrite indexed source" in json.loads(result.stderr)["detail"]
    assert source.read_text(encoding="utf-8") == "# Source\n"
    assert not (repo / "index.json").exists()


def test_scan_persists_manifest_with_scope_config_outputs_and_lane(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    config_path = repo / "control" / "scan_config.json"
    db_path = repo / ".mdex" / "index.db"
    output_path = repo / ".mdex" / "explicit.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/configured.json",
        },
    )

    result = _run_cli(
        "scan",
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        "--output",
        str(output_path),
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    manifest = _scan_manifest(db_path)
    assert manifest["scan_roots"] == [repo.resolve().as_posix()]
    assert manifest["config_identity"] == {
        "path": config_path.resolve().as_posix(),
        "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    }
    assert manifest["output"] == {
        "db": db_path.resolve().as_posix(),
        "json": output_path.resolve().as_posix(),
    }
    assert manifest["index_kind"] == "repo"


@pytest.mark.parametrize("protected_relative", [".git/config", "pyproject.toml"])
def test_scan_rejects_config_output_that_overwrites_existing_repository_control_file(
    tmp_path: Path,
    protected_relative: str,
) -> None:
    repo = _repo_with_source(tmp_path)
    protected_path = repo / protected_relative
    protected_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel = "repository control data\n"
    protected_path.write_text(sentinel, encoding="utf-8")
    config_path = repo / "control" / "scan_config.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".json"],
            "output_file": protected_relative,
        },
    )
    db_path = repo / ".mdex" / "index.db"

    result = _run_cli("scan", "--config", str(config_path), "--db", str(db_path), cwd=repo)

    assert result.returncode == 2
    assert "must not overwrite existing repository file" in json.loads(result.stderr)["detail"]
    assert protected_path.read_text(encoding="utf-8") == sentinel
    assert not db_path.exists()


@pytest.mark.parametrize("protected_relative", [".git/config", "pyproject.toml"])
def test_scan_rejects_config_database_that_overwrites_existing_repository_control_file(
    tmp_path: Path,
    protected_relative: str,
) -> None:
    repo = _repo_with_source(tmp_path)
    protected_path = repo / protected_relative
    protected_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel = "repository control data\n"
    protected_path.write_text(sentinel, encoding="utf-8")
    _write_json(repo / ".mdex" / "config.json", {"db": protected_relative})

    result = _run_cli("scan", "--root", str(repo), cwd=repo)

    assert result.returncode == 2
    assert "must not overwrite existing repository file" in json.loads(result.stderr)["detail"]
    assert protected_path.read_text(encoding="utf-8") == sentinel


def test_scan_rejects_existing_unowned_sqlite_database(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    app_db = repo / ".mdex" / "application.db"
    app_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(app_db)) as connection:
        connection.execute("CREATE TABLE application_data (value TEXT)")
        connection.execute("INSERT INTO application_data VALUES ('sentinel')")
        connection.commit()

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(app_db),
        "--output",
        str(repo / ".mdex" / "index.json"),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "unowned SQLite database" in json.loads(result.stderr)["detail"]
    with sqlite3.connect(str(app_db)) as connection:
        assert connection.execute("SELECT value FROM application_data").fetchone() == ("sentinel",)


def test_scan_rejects_missing_root_without_emptying_existing_index(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    db_path = repo / ".mdex" / "index.db"
    output_path = repo / ".mdex" / "index.json"
    first = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--db",
        str(db_path),
        "--output",
        str(output_path),
        cwd=repo,
    )
    assert first.returncode == 0, first.stderr
    original_db_generation = list_index_metadata(str(db_path))["generated"]
    original_json = output_path.read_bytes()

    second = _run_cli(
        "scan",
        "--root",
        str(repo / "missing"),
        "--db",
        str(db_path),
        "--output",
        str(output_path),
        cwd=repo,
    )

    assert second.returncode == 2
    assert "scan root is missing or not a directory" in json.loads(second.stderr)["detail"]
    assert list_index_metadata(str(db_path))["generated"] == original_db_generation
    assert output_path.read_bytes() == original_json


@pytest.mark.parametrize("node_root_kind", ["missing", "sibling"])
def test_scan_rejects_invalid_node_id_root_before_writes(
    tmp_path: Path,
    node_root_kind: str,
) -> None:
    repo = _repo_with_source(tmp_path)
    node_root = tmp_path / node_root_kind
    if node_root_kind == "sibling":
        node_root.mkdir()
    db_path = repo / ".mdex" / "index.db"
    output_path = repo / ".mdex" / "index.json"

    result = _run_cli(
        "scan",
        "--root",
        str(repo),
        "--node-id-root",
        str(node_root),
        "--db",
        str(db_path),
        "--output",
        str(output_path),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "node-id root" in json.loads(result.stderr)["detail"]
    assert not db_path.exists()
    assert not output_path.exists()


@pytest.mark.parametrize("output_key", ["db", "output"])
def test_scan_artifacts_rejects_configured_output_that_overwrites_repository_file(
    tmp_path: Path,
    output_key: str,
) -> None:
    repo = _repo_with_source(tmp_path)
    outputs = repo / "outputs"
    outputs.mkdir()
    (outputs / "artifact.json").write_text('{"summary":"artifact"}\n', encoding="utf-8")
    protected_path = repo / "pyproject.toml"
    sentinel = "repository control data\n"
    protected_path.write_text(sentinel, encoding="utf-8")
    artifact_config: dict[str, object] = {
        "roots": ["outputs"],
        "db": ".mdex/artifacts.db",
        "output": ".mdex/artifacts.json",
    }
    artifact_config[output_key] = "pyproject.toml"
    _write_json(repo / ".mdex" / "config.json", {"indexes": {"artifacts": artifact_config}})

    result = _run_cli("scan-artifacts", cwd=repo)

    assert result.returncode == 2
    assert "must not overwrite existing repository file" in json.loads(result.stderr)["detail"]
    assert protected_path.read_text(encoding="utf-8") == sentinel


def test_scan_artifacts_rejects_identical_db_and_json_output(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    outputs = repo / "outputs"
    outputs.mkdir()
    (outputs / "artifact.json").write_text('{"summary":"artifact"}\n', encoding="utf-8")
    output = repo / "artifacts.db"

    result = _run_cli(
        "scan-artifacts",
        "--root",
        str(outputs),
        "--db",
        str(output),
        "--output",
        str(output),
        cwd=repo,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "scan-artifacts failed"
    assert "must be different" in payload["detail"]
    assert not output.exists()


def test_scan_artifacts_rejects_config_output_outside_repo(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    outputs = repo / "outputs"
    outputs.mkdir()
    (outputs / "artifact.json").write_text('{"summary":"artifact"}\n', encoding="utf-8")
    escaped = tmp_path / "artifacts.json"
    escaped.write_text("sentinel", encoding="utf-8")
    _write_json(
        repo / ".mdex" / "config.json",
        {
            "indexes": {
                "artifacts": {
                    "roots": ["outputs"],
                    "output": "../artifacts.json",
                }
            }
        },
    )

    result = _run_cli("scan-artifacts", cwd=repo)

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["error"] == "scan-artifacts failed"
    assert "indexes.artifacts.output must stay within repo" in payload["detail"]
    assert escaped.read_text(encoding="utf-8") == "sentinel"


def test_scan_artifacts_allows_explicit_output_outside_repo(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    outputs = repo / "outputs"
    outputs.mkdir()
    (outputs / "artifact.json").write_text('{"summary":"artifact"}\n', encoding="utf-8")
    output = tmp_path / "explicit-artifacts.json"

    result = _run_cli(
        "scan-artifacts",
        "--root",
        str(outputs),
        "--db",
        str(repo / "artifacts.db"),
        "--output",
        str(output),
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()


def test_scan_artifacts_rejects_output_that_is_an_indexed_source(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    outputs = repo / "outputs"
    outputs.mkdir()
    source = outputs / "artifact.json"
    source.write_text('{"summary":"artifact"}\n', encoding="utf-8")

    result = _run_cli(
        "scan-artifacts",
        "--root",
        str(outputs),
        "--db",
        str(repo / "artifacts.db"),
        "--output",
        str(source),
        cwd=repo,
    )

    assert result.returncode == 2
    assert "must not overwrite indexed source" in json.loads(result.stderr)["detail"]
    assert source.read_text(encoding="utf-8") == '{"summary":"artifact"}\n'


def test_scan_artifacts_rejects_disappeared_root_without_emptying_index(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    outputs = repo / "outputs"
    outputs.mkdir()
    source = outputs / "artifact.json"
    source.write_text('{"summary":"artifact"}\n', encoding="utf-8")
    db_path = repo / ".mdex" / "artifacts.db"
    output_path = repo / ".mdex" / "artifacts.json"
    first = _run_cli(
        "scan-artifacts",
        "--root",
        str(outputs),
        "--db",
        str(db_path),
        "--output",
        str(output_path),
        cwd=repo,
    )
    assert first.returncode == 0, first.stderr
    original_generation = list_index_metadata(str(db_path))["generated"]
    original_json = output_path.read_bytes()
    source.unlink()
    outputs.rmdir()

    second = _run_cli(
        "scan-artifacts",
        "--root",
        str(outputs),
        "--db",
        str(db_path),
        "--output",
        str(output_path),
        cwd=repo,
    )

    assert second.returncode == 2
    assert "artifact scan root is missing or not a directory" in json.loads(second.stderr)["detail"]
    assert list_index_metadata(str(db_path))["generated"] == original_generation
    assert output_path.read_bytes() == original_json


@pytest.mark.parametrize("source", ["arg", "config"])
def test_scan_artifacts_rejects_empty_effective_root_set(tmp_path: Path, source: str) -> None:
    repo = _repo_with_source(tmp_path)
    if source == "config":
        _write_json(
            repo / ".mdex" / "config.json",
            {"indexes": {"artifacts": {"roots": [""], "db": ".mdex/artifacts.db"}}},
        )
        args: tuple[str, ...] = ("scan-artifacts",)
    else:
        args = ("scan-artifacts", "--root", "")

    result = _run_cli(*args, cwd=repo)

    assert result.returncode == 2
    assert "at least one valid artifact scan root" in json.loads(result.stderr)["detail"]
    assert not (repo / ".mdex" / "artifacts.db").exists()


def test_finish_scan_keeps_sqlite_and_json_in_sync(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    config_path = repo / "control" / "scan_config.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/index.json",
        },
    )
    db_path = repo / ".mdex" / "index.db"
    json_path = repo / ".mdex" / "index.json"

    scanned = _run_cli(
        "scan",
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        cwd=repo,
    )
    assert scanned.returncode == 0, scanned.stderr

    finished = _run_cli(
        "finish",
        "--task",
        "verify sync",
        "--db",
        str(db_path),
        "--scan",
        cwd=repo,
    )
    assert finished.returncode == 0, finished.stderr

    doctor = _run_cli(
        "doctor",
        "--db",
        str(db_path),
        "--json-index",
        str(json_path),
        cwd=repo,
    )
    assert doctor.returncode == 0, doctor.stderr
    checks = {row["name"]: row for row in json.loads(doctor.stdout)["checks"]}
    assert checks["json_sqlite_sync"] == {
        "name": "json_sqlite_sync",
        "status": "ok",
        "findings": [],
    }


def test_finish_scan_preserves_strict_manifest_behavior(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    config_path = repo / "control" / "scan_config.json"
    db_path = repo / ".mdex" / "index.db"
    output_path = repo / ".mdex" / "index.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".json"],
            "output_file": ".mdex/index.json",
        },
    )
    scanned = _run_cli(
        "scan",
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        "--strict",
        cwd=repo,
    )
    assert scanned.returncode == 0, scanned.stderr
    manifest = _scan_manifest(db_path)
    assert manifest["strict"] is True
    original_generation = list_index_metadata(str(db_path))["generated"]
    original_json = output_path.read_bytes()
    (repo / "broken.json").write_bytes(b"\xff\xfe")

    finished = _run_cli(
        "finish",
        "--task",
        "strict rescan",
        "--db",
        str(db_path),
        "--scan",
        cwd=repo,
    )

    assert finished.returncode == 2
    assert list_index_metadata(str(db_path))["generated"] == original_generation
    assert output_path.read_bytes() == original_json


def test_finish_scan_rejects_output_collision_with_nonzero_exit(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    config_path = repo / "control" / "scan_config.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/index.json",
        },
    )
    db_path = repo / ".mdex" / "index.db"
    scanned = _run_cli(
        "scan",
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        cwd=repo,
    )
    assert scanned.returncode == 0, scanned.stderr

    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/index.db",
        },
    )
    finished = _run_cli(
        "finish",
        "--task",
        "reject collision",
        "--db",
        str(db_path),
        "--scan",
        cwd=repo,
    )

    assert finished.returncode == 2
    payload = json.loads(finished.stderr)
    assert payload["error"] == "scan failed"
    assert "scan configuration changed" in payload["detail"]


def test_finish_scan_rejects_or_reuses_original_cli_output_path(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    config_path = repo / "control" / "scan_config.json"
    configured_output = repo / ".mdex" / "configured.json"
    original_output = repo / ".mdex" / "explicit.json"
    db_path = repo / ".mdex" / "index.db"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/configured.json",
        },
    )
    scanned = _run_cli(
        "scan",
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        "--output",
        str(original_output),
        cwd=repo,
    )
    assert scanned.returncode == 0, scanned.stderr
    original_generation = str(json.loads(original_output.read_text(encoding="utf-8"))["generated"])

    finished = _run_cli(
        "finish",
        "--task",
        "preserve explicit output identity",
        "--db",
        str(db_path),
        "--scan",
        cwd=repo,
    )

    final_db_generation = list_index_metadata(str(db_path))["generated"]
    if finished.returncode == 0:
        assert str(json.loads(original_output.read_text(encoding="utf-8"))["generated"]) == final_db_generation
    else:
        assert finished.returncode == 2
        assert final_db_generation == original_generation
        assert str(json.loads(original_output.read_text(encoding="utf-8"))["generated"]) == original_generation
        assert not configured_output.exists()


def test_doctor_resolves_json_from_database_manifest_before_scan_config(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    config_path = repo / "control" / "scan_config.json"
    configured_output = repo / ".mdex" / "configured.json"
    explicit_output = repo / ".mdex" / "explicit.json"
    db_path = repo / ".mdex" / "index.db"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/configured.json",
        },
    )
    scanned = _run_cli(
        "scan",
        "--config",
        str(config_path),
        "--db",
        str(db_path),
        "--output",
        str(explicit_output),
        cwd=repo,
    )
    assert scanned.returncode == 0, scanned.stderr
    configured_output.write_text("not the active index\n", encoding="utf-8")

    doctor = _run_cli("doctor", "--db", str(db_path), cwd=repo)

    assert doctor.returncode == 0, doctor.stderr
    checks = {row["name"]: row for row in json.loads(doctor.stdout)["checks"]}
    assert checks["json_sqlite_sync"] == {
        "name": "json_sqlite_sync",
        "status": "ok",
        "findings": [],
    }


@pytest.mark.parametrize("mutation", ["missing", "invalid", "wrong_pair"])
def test_doctor_warns_for_invalid_or_mismatched_json_manifest(
    tmp_path: Path,
    mutation: str,
) -> None:
    repo = _repo_with_source(tmp_path)
    config_path = repo / "control" / "scan_config.json"
    db_path = repo / ".mdex" / "index.db"
    output_path = repo / ".mdex" / "index.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/index.json",
        },
    )
    scanned = _run_cli("scan", "--config", str(config_path), "--db", str(db_path), cwd=repo)
    assert scanned.returncode == 0, scanned.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload.pop("scan_manifest")
    elif mutation == "invalid":
        payload["scan_manifest"] = "invalid"
    else:
        payload["scan_manifest"]["output"]["json"] = str(repo / ".mdex" / "other.json")
    output_path.write_text(json.dumps(payload), encoding="utf-8")

    doctor = _run_cli(
        "doctor",
        "--db",
        str(db_path),
        "--json-index",
        str(output_path),
        cwd=repo,
    )

    assert doctor.returncode == 0, doctor.stderr
    checks = {row["name"]: row for row in json.loads(doctor.stdout)["checks"]}
    assert checks["json_sqlite_sync"]["status"] == "warning"
    assert checks["json_sqlite_sync"]["findings"]


def test_finish_scan_rejects_or_reuses_original_scan_config_identity(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    original_config = repo / "control" / "original.json"
    other_config = repo / "control" / "other.json"
    original_output = repo / ".mdex" / "original.json"
    other_output = repo / ".mdex" / "other.json"
    db_path = repo / ".mdex" / "index.db"
    _write_json(
        original_config,
        {
            "scan_roots": ["."],
            "include_extensions": [".md"],
            "output_file": ".mdex/original.json",
        },
    )
    scanned = _run_cli("scan", "--config", str(original_config), "--db", str(db_path), cwd=repo)
    assert scanned.returncode == 0, scanned.stderr
    original_generation = list_index_metadata(str(db_path))["generated"]
    _write_json(
        other_config,
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".json"],
            "output_file": ".mdex/other.json",
        },
    )
    _write_json(repo / ".mdex" / "config.json", {"scan_config": "control/other.json"})

    finished = _run_cli(
        "finish",
        "--task",
        "preserve scan config identity",
        "--db",
        str(db_path),
        "--scan",
        cwd=repo,
    )

    final_db_generation = list_index_metadata(str(db_path))["generated"]
    if finished.returncode == 0:
        assert str(json.loads(original_output.read_text(encoding="utf-8"))["generated"]) == final_db_generation
        assert not other_output.exists()
    else:
        assert finished.returncode == 2
        assert final_db_generation == original_generation
        assert not other_output.exists()


def test_finish_scan_rejects_artifact_lane_even_when_roots_match(tmp_path: Path) -> None:
    repo = _repo_with_source(tmp_path)
    (repo / "artifact.json").write_text('{"summary":"artifact"}\n', encoding="utf-8")
    _write_json(
        repo / "control" / "scan_config.json",
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".json"],
            "output_file": ".mdex/main.json",
        },
    )
    artifact_db = repo / ".mdex" / "artifacts.db"
    artifact_json = repo / ".mdex" / "artifacts.json"
    scanned = _run_cli(
        "scan-artifacts",
        "--root",
        str(repo),
        "--db",
        str(artifact_db),
        "--output",
        str(artifact_json),
        cwd=repo,
    )
    assert scanned.returncode == 0, scanned.stderr
    original_generation = list_index_metadata(str(artifact_db))["generated"]

    finished = _run_cli(
        "finish",
        "--task",
        "do not replace artifact lane",
        "--db",
        str(artifact_db),
        "--scan",
        cwd=repo,
    )

    assert finished.returncode == 2
    assert list_index_metadata(str(artifact_db))["generated"] == original_generation


@pytest.mark.parametrize("protected_relative", [".git/config", "pyproject.toml"])
def test_finish_scan_rejects_config_output_that_overwrites_existing_repository_control_file(
    tmp_path: Path,
    protected_relative: str,
) -> None:
    repo = _repo_with_source(tmp_path)
    protected_path = repo / protected_relative
    protected_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel = "repository control data\n"
    protected_path.write_text(sentinel, encoding="utf-8")
    config_path = repo / "control" / "scan_config.json"
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".json"],
            "output_file": ".mdex/index.json",
        },
    )
    db_path = repo / ".mdex" / "index.db"
    scanned = _run_cli("scan", "--config", str(config_path), "--db", str(db_path), cwd=repo)
    assert scanned.returncode == 0, scanned.stderr
    _write_json(
        config_path,
        {
            "scan_roots": ["."],
            "include_extensions": [".md", ".json"],
            "output_file": protected_relative,
        },
    )

    finished = _run_cli(
        "finish",
        "--task",
        "protect repository control file",
        "--db",
        str(db_path),
        "--scan",
        cwd=repo,
    )

    assert finished.returncode == 2
    assert protected_path.read_text(encoding="utf-8") == sentinel
