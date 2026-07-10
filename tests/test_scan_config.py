from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
import tomllib

from mdex import cli, finish, scan_config
from mdex.dbresolve import RuntimeContext
from mdex.scan_config import ScanConfigError, load_scan_config, load_scan_config_with_identity
from mdex.scan_manifest import SUPPORTED_DOCUMENT_INDEX_KINDS, build_scan_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_config(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _scan_args(repo: Path, config_path: Path, db_path: Path, output_path: Path) -> Namespace:
    return Namespace(
        root=str(repo),
        config=str(config_path),
        output=str(output_path),
        db=str(db_path),
        strict=False,
        incremental=False,
        node_id_root=None,
    )


@pytest.mark.parametrize(
    "path",
    [
        "control/scan_config.json",
        "control/task_scan_config.json",
        "examples/private/yura_memory_scan_config.json",
        "tests/fixtures/quality_scan_config.json",
    ],
)
def test_repository_scan_configs_validate(path: str) -> None:
    loaded = load_scan_config(PROJECT_ROOT / path)
    assert isinstance(loaded, dict)


def test_missing_optional_scan_config_keeps_empty_default_semantics(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    config, identity = load_scan_config_with_identity(missing, optional=True)

    assert config == {}
    assert identity == hashlib.sha256(b"").hexdigest()
    with pytest.raises(ScanConfigError, match="cannot read scan config"):
        load_scan_config(missing)


@pytest.mark.parametrize(
    ("payload", "error_path"),
    [
        ({"include_extensons": [".md"]}, "$"),
        ({"lane": "repo"}, "$"),
        ({"index_kind": "artifacts"}, "$.index_kind"),
        ({"scan_roots": []}, "$.scan_roots"),
        ({"scan_root": 42}, "$.scan_root"),
        ({"include_extensions": ".md"}, "$.include_extensions"),
        ({"include_extensions": [""]}, "$.include_extensions[0]"),
        ({"exclude_patterns": [""]}, "$.exclude_patterns[0]"),
        ({"use_default_exclude_patterns": "false"}, "$.use_default_exclude_patterns"),
        ({"node_type_map": {"task": "tasks"}}, "$.node_type_map.task"),
        ({"summary_max_chars": True}, "$.summary_max_chars"),
        ({"output_file": "   "}, "$.output_file"),
        ({"context_scoring": {"keyword": {"title": "heavy"}}}, "$.context_scoring.keyword.title"),
        ({"context_scoring": {"unknown_weight": 1}}, "$.context_scoring"),
        ({"context_scoring": {"soft_budget_multiplier": 0}}, "$.context_scoring.soft_budget_multiplier"),
        ({"synonyms": {"self post": 3}}, '$.synonyms["self post"]'),
        ({"search_synonyms": {"self post": []}}, '$.search_synonyms["self post"]'),
    ],
)
def test_invalid_scan_config_fails_before_outputs(
    payload: dict[str, Any],
    error_path: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.md").write_text("# Keep\n", encoding="utf-8")
    config_path = tmp_path / "scan.json"
    _write_config(config_path, payload)
    db_path = tmp_path / "index.db"
    output_path = tmp_path / "index.json"

    result = cli._cmd_scan(_scan_args(repo, config_path, db_path, output_path))

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["error"] == "scan failed"
    assert "invalid scan config" in error["detail"]
    assert f" at {error_path}:" in error["detail"]
    assert not db_path.exists()
    assert not output_path.exists()


def test_scan_config_accepts_supported_lane_scoring_and_synonym_shapes(tmp_path: Path) -> None:
    config_path = tmp_path / "scan.json"
    _write_config(
        config_path,
        {
            "index_kind": "memory",
            "scan_roots": ["memory"],
            "include_extensions": ["md", ".json", "md"],
            "exclude_patterns": ["tmp/**", "tmp/**"],
            "context_scoring": {
                "status_bonus": {"done": -0.5},
                "path_symbol_weight": 2.5,
                "synonyms": {"agent": "worker"},
            },
            "synonyms": {"self post": ["self_post", "spontaneous post"]},
            "search_synonyms": {"mdex": "indexer"},
        },
    )

    loaded = load_scan_config(config_path)
    assert loaded["index_kind"] == "memory"


def test_scan_config_rejects_nonstandard_json_numbers(tmp_path: Path) -> None:
    config_path = tmp_path / "scan.json"
    config_path.write_text('{"summary_max_chars": NaN}', encoding="utf-8")

    with pytest.raises(ScanConfigError, match="non-standard JSON constant"):
        load_scan_config(config_path)


def test_scan_config_rejects_overflowed_non_finite_numbers_with_path(tmp_path: Path) -> None:
    config_path = tmp_path / "scan.json"
    config_path.write_text(
        '{"context_scoring":{"keyword":{"title":1e309}}}',
        encoding="utf-8",
    )

    with pytest.raises(
        ScanConfigError,
        match=r"\$\.context_scoring\.keyword\.title: number must be finite",
    ):
        load_scan_config(config_path)


def test_scan_config_size_limit_uses_a_bounded_read(tmp_path: Path) -> None:
    config_path = tmp_path / "scan.json"
    config_path.write_bytes(b" " * (scan_config.MAX_SCAN_CONFIG_BYTES + 1))

    with pytest.raises(ScanConfigError, match="exceeds the 1 MiB safety limit"):
        load_scan_config(config_path)


def test_runtime_explicit_scan_config_must_exist_before_scan_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mdex").mkdir()
    _write_config(repo / ".mdex" / "config.json", {"scan_config": "control/typo.json"})
    (repo / "private.md").write_text("# Must not be scanned\n", encoding="utf-8")
    db_path = tmp_path / "index.db"
    output_path = tmp_path / "index.json"
    args = _scan_args(repo, repo / "unused.json", db_path, output_path)
    args.config = None
    monkeypatch.chdir(repo)

    result = cli._cmd_scan(args)

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["error"] == "scan failed"
    assert "cannot read scan config" in error["detail"]
    # The detail echoes the resolved path, whose separators are
    # platform-specific on Windows.
    assert str(Path("control") / "typo.json") in error["detail"]
    assert not db_path.exists()
    assert not output_path.exists()


@pytest.mark.parametrize("invalid_value", [None, "", 42, []])
def test_runtime_explicit_scan_config_requires_non_empty_string(
    invalid_value: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mdex").mkdir()
    _write_config(repo / ".mdex" / "config.json", {"scan_config": invalid_value})
    (repo / "keep.md").write_text("# Keep\n", encoding="utf-8")
    args = _scan_args(
        repo,
        repo / "unused.json",
        tmp_path / "index.db",
        tmp_path / "index.json",
    )
    args.config = None
    monkeypatch.chdir(repo)

    result = cli._cmd_scan(args)

    error = json.loads(capsys.readouterr().err)
    assert result == 2
    assert error["error"] == "scan failed"
    assert "runtime config scan_config must be a non-empty string" in error["detail"]


def test_cli_config_overrides_invalid_runtime_scan_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".mdex").mkdir()
    _write_config(repo / ".mdex" / "config.json", {"scan_config": 42})
    (repo / "keep.md").write_text("# Keep\n", encoding="utf-8")
    cli_config = tmp_path / "valid-scan.json"
    _write_config(cli_config, {"include_extensions": [".md"], "exclude_patterns": []})
    db_path = tmp_path / "index.db"
    output_path = tmp_path / "index.json"
    monkeypatch.chdir(repo)

    result = cli._cmd_scan(_scan_args(repo, cli_config, db_path, output_path))

    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert json.loads(captured.out)["nodes"] == 1
    assert db_path.exists()
    assert output_path.exists()


@pytest.mark.parametrize("empty_config", ["", "   "])
def test_cli_explicit_empty_config_path_fails_closed(
    empty_config: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "private.md").write_text("# Must not be scanned\n", encoding="utf-8")
    db_path = tmp_path / "index.db"
    output_path = tmp_path / "index.json"
    args = _scan_args(repo, repo / "unused.json", db_path, output_path)
    args.config = empty_config
    monkeypatch.chdir(repo)

    result = cli._cmd_scan(args)

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "--config must be a non-empty path" in json.loads(captured.err)["detail"]
    assert not db_path.exists()
    assert not output_path.exists()


def test_finish_scan_preflight_uses_same_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    scan_root = repo / "docs"
    scan_root.mkdir(parents=True)
    config_path = repo / "control" / "scan_config.json"
    config_path.parent.mkdir()
    valid_config = {"index_kind": "repo", "scan_roots": ["docs"], "include_extensions": [".md"]}
    _write_config(config_path, valid_config)
    db_path = repo / ".mdex" / "index.db"
    output_path = repo / ".mdex" / "index.json"
    manifest = build_scan_manifest(
        repo_root=repo,
        scan_roots=[scan_root],
        node_id_root=repo,
        config_path=config_path,
        config=valid_config,
        db_output=db_path,
        output_json=output_path,
        output_origin="config",
        index_kind="repo",
    )
    monkeypatch.setattr(
        finish,
        "list_index_metadata",
        lambda _db: {"scan_manifest": json.dumps(manifest)},
    )
    _write_config(config_path, {"include_extensions": [".md"], "context_scoring": {"keyword": {"title": "high"}}})
    context = RuntimeContext(repo_root=repo, config_path=repo / ".mdex" / "config.json", config={})

    with pytest.raises(ScanConfigError, match=r"\$\.context_scoring\.keyword\.title"):
        finish._prepare_scan(context, str(db_path))


def test_scan_config_schema_discovery_includes_installed_distribution_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_schema = tmp_path / "prefix" / "schemas" / scan_config.SCAN_CONFIG_SCHEMA_NAME
    installed_schema.parent.mkdir(parents=True)
    installed_schema.write_text(
        (PROJECT_ROOT / "schemas" / scan_config.SCAN_CONFIG_SCHEMA_NAME).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    class FakeDistribution:
        files = [Path("../../../schemas") / scan_config.SCAN_CONFIG_SCHEMA_NAME]

        def locate_file(self, _entry: Path) -> Path:
            return installed_schema

    monkeypatch.setattr(scan_config.metadata, "distribution", lambda _name: FakeDistribution())

    assert installed_schema.resolve() in scan_config._schema_candidates()
    assert scan_config._load_schema([installed_schema])["title"] == "mdex document scan configuration"


def test_scan_config_schema_discovery_supports_pip_target_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    module_path = target / "mdex" / "scan_config.py"
    module_path.parent.mkdir(parents=True)
    module_path.touch()
    installed_schema = target / "schemas" / scan_config.SCAN_CONFIG_SCHEMA_NAME
    installed_schema.parent.mkdir()
    installed_schema.write_text(
        (PROJECT_ROOT / "schemas" / scan_config.SCAN_CONFIG_SCHEMA_NAME).read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scan_config, "__file__", str(module_path))
    monkeypatch.setattr(
        scan_config.metadata,
        "distribution",
        lambda _name: (_ for _ in ()).throw(scan_config.metadata.PackageNotFoundError()),
    )

    candidates = scan_config._schema_candidates()

    assert candidates[0] == installed_schema
    assert scan_config._load_schema(candidates)["title"] == "mdex document scan configuration"


def test_scan_config_schema_missing_is_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ScanConfigError, match="scan config schema is missing"):
        scan_config._load_schema([tmp_path / "missing.schema.json"])


def test_scan_config_schema_is_packaged_with_runtime_validator_dependency() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    schema = scan_config._load_schema()

    assert "schemas/scan_config.schema.json" in project["tool"]["setuptools"]["data-files"]["schemas"]
    assert any(
        str(requirement).startswith("jsonschema>=")
        for requirement in project["project"]["dependencies"]
    )
    assert set(schema["properties"]["index_kind"]["enum"]) == SUPPORTED_DOCUMENT_INDEX_KINDS
