from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from mdex import indexer
from mdex.indexer import write_sqlite
from mdex.locking import DbLockTimeoutError, db_lock_path, exclusive_db_lock
from mdex.scan_manifest import build_scan_manifest, set_scan_manifest
from mdex.store import apply_node_summary, get_node, list_index_metadata, update_node_summary


def _index(root: Path, summary: str) -> dict[str, Any]:
    return {
        "generated": "2026-07-10T00:00:00+00:00",
        "scan_root": str(root),
        "nodes": [
            {
                "id": "node.md",
                "title": "Node",
                "type": "design",
                "summary": summary,
                "updated": "2026-07-10T00:00:00+00:00",
            }
        ],
        "edges": [],
    }


def _scan_pair_index(
    root: Path,
    *,
    generation: str,
    db_path: Path,
    json_path: Path,
) -> dict[str, Any]:
    index = _index(root, generation)
    index["generated"] = generation
    index["scan_roots"] = [root.resolve().as_posix()]
    manifest = build_scan_manifest(
        repo_root=root,
        scan_roots=[root],
        node_id_root=root,
        config_path=root / "control" / "scan_config.json",
        config={"test_generation": generation},
        db_output=db_path,
        output_json=json_path,
        output_origin="arg",
        index_kind="repo",
    )
    set_scan_manifest(index, manifest)
    return index


def test_enrich_waits_for_scan_replace_and_override_is_not_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / ".mdex" / "index.db"
    write_sqlite(_index(tmp_path, "old seed"), str(db_path))

    snapshot_taken = threading.Event()
    release_scan = threading.Event()
    enrich_started = threading.Event()
    enrich_finished = threading.Event()
    errors: list[BaseException] = []
    enrich_results: list[bool] = []
    original_load = indexer._load_existing_overrides

    def paused_snapshot(path: Path) -> list[tuple[str, str, str, str]]:
        rows = original_load(path)
        snapshot_taken.set()
        if not release_scan.wait(timeout=5):
            raise TimeoutError("test did not release scan snapshot")
        return rows

    def run_scan() -> None:
        try:
            write_sqlite(_index(tmp_path, "new seed"), str(db_path), lock_timeout=5)
        except BaseException as exc:
            errors.append(exc)

    def run_enrich() -> None:
        enrich_started.set()
        try:
            enrich_results.append(
                update_node_summary(
                    str(db_path),
                    "node.md",
                    "concurrent agent summary",
                    lock_timeout=5,
                )
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            enrich_finished.set()

    monkeypatch.setattr(indexer, "_load_existing_overrides", paused_snapshot)
    scan_thread = threading.Thread(target=run_scan)
    enrich_thread = threading.Thread(target=run_enrich)
    scan_thread.start()
    assert snapshot_taken.wait(timeout=5)
    enrich_thread.start()
    assert enrich_started.wait(timeout=5)

    try:
        assert not enrich_finished.wait(timeout=0.1)
    finally:
        release_scan.set()

    scan_thread.join(timeout=5)
    enrich_thread.join(timeout=5)
    assert not scan_thread.is_alive()
    assert not enrich_thread.is_alive()
    assert errors == []
    assert enrich_results == [True]

    node = get_node(str(db_path), "node.md")
    assert node is not None
    assert node["summary"] == "concurrent agent summary"
    assert node["summary_source"] == "agent"
    assert db_lock_path(db_path).exists()


def test_update_node_summary_reports_database_lock_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / ".mdex" / "index.db"
    write_sqlite(_index(tmp_path, "seed"), str(db_path))

    with exclusive_db_lock(db_path):
        with pytest.raises(DbLockTimeoutError, match="waiting for database lock"):
            update_node_summary(
                str(db_path),
                "node.md",
                "blocked summary",
                lock_timeout=0.01,
            )


def test_write_sqlite_reports_database_lock_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / ".mdex" / "index.db"
    write_sqlite(_index(tmp_path, "seed"), str(db_path))

    with exclusive_db_lock(db_path):
        with pytest.raises(DbLockTimeoutError, match="waiting for database lock"):
            write_sqlite(
                _index(tmp_path, "blocked seed"),
                str(db_path),
                lock_timeout=0.01,
            )


def test_concurrent_scans_keep_shared_database_and_json_on_same_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".mdex" / "index.db"
    json_path = repo / ".mdex" / "index.json"
    index_a = _scan_pair_index(
        repo,
        generation="generation-a",
        db_path=db_path,
        json_path=json_path,
    )
    index_b = _scan_pair_index(
        repo,
        generation="generation-b",
        db_path=db_path,
        json_path=json_path,
    )
    first_json_ready = threading.Event()
    release_first_json = threading.Event()
    second_finished = threading.Event()
    errors: list[BaseException] = []
    original_write_json = indexer._write_json_unlocked

    def controlled_write_json(index: dict[str, Any], path: Path) -> None:
        if index["generated"] == "generation-a":
            first_json_ready.set()
            if not release_first_json.wait(timeout=5):
                raise TimeoutError("test did not release first JSON write")
        original_write_json(index, path)

    monkeypatch.setattr(indexer, "_write_json_unlocked", controlled_write_json)

    def run_scan(
        index: dict[str, Any],
        *,
        finished: threading.Event | None = None,
    ) -> None:
        try:
            indexer.write_scan_outputs(
                index,
                str(db_path),
                str(json_path),
                lock_timeout=5,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            if finished is not None:
                finished.set()

    first = threading.Thread(target=run_scan, args=(index_a,))
    second = threading.Thread(
        target=run_scan,
        args=(index_b,),
        kwargs={"finished": second_finished},
    )
    first.start()
    assert first_json_ready.wait(timeout=5)
    second.start()
    try:
        assert not second_finished.wait(timeout=0.2)
    finally:
        release_first_json.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    db_generation = list_index_metadata(str(db_path))["generated"]
    json_generation = str(json.loads(json_path.read_text(encoding="utf-8"))["generated"])
    assert db_generation == json_generation == "generation-b"


def test_concurrent_scans_reject_different_databases_sharing_one_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_a = repo / ".mdex" / "a.db"
    db_b = repo / ".mdex" / "b.db"
    shared_json = repo / ".mdex" / "shared.json"
    index_a = _scan_pair_index(
        repo,
        generation="generation-a",
        db_path=db_a,
        json_path=shared_json,
    )
    index_b = _scan_pair_index(
        repo,
        generation="generation-b",
        db_path=db_b,
        json_path=shared_json,
    )
    first_json_ready = threading.Event()
    release_first_json = threading.Event()
    second_finished = threading.Event()
    errors: list[BaseException] = []
    successes: list[str] = []
    original_write_json = indexer._write_json_unlocked

    def controlled_write_json(index: dict[str, Any], path: Path) -> None:
        if index["generated"] == "generation-a":
            first_json_ready.set()
            if not release_first_json.wait(timeout=5):
                raise TimeoutError("test did not release first JSON write")
        original_write_json(index, path)

    monkeypatch.setattr(indexer, "_write_json_unlocked", controlled_write_json)

    def run_scan(
        index: dict[str, Any],
        db_path: Path,
        generation: str,
        *,
        finished: threading.Event | None = None,
    ) -> None:
        try:
            indexer.write_scan_outputs(
                index,
                str(db_path),
                str(shared_json),
                lock_timeout=5,
            )
            successes.append(generation)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if finished is not None:
                finished.set()

    first = threading.Thread(target=run_scan, args=(index_a, db_a, "generation-a"))
    second = threading.Thread(
        target=run_scan,
        args=(index_b, db_b, "generation-b"),
        kwargs={"finished": second_finished},
    )
    first.start()
    assert first_json_ready.wait(timeout=5)
    second.start()
    try:
        assert not second_finished.wait(timeout=0.2)
    finally:
        release_first_json.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert successes == ["generation-a"]
    assert len(errors) == 1
    assert isinstance(errors[0], indexer.ScanOutputsWriteError)
    assert "owned by a different scan database" in str(errors[0])
    assert not db_b.exists()
    assert list_index_metadata(str(db_a))["generated"] == "generation-a"
    assert json.loads(shared_json.read_text(encoding="utf-8"))["generated"] == "generation-a"


def test_copied_database_cannot_claim_original_json_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    original_db = repo / ".mdex" / "original.db"
    clone_db = repo / ".mdex" / "clone.db"
    json_path = repo / ".mdex" / "index.json"
    original = _scan_pair_index(
        repo,
        generation="original",
        db_path=original_db,
        json_path=json_path,
    )
    indexer.write_scan_outputs(original, str(original_db), str(json_path))
    shutil.copy2(original_db, clone_db)

    clone_scan = _scan_pair_index(
        repo,
        generation="clone",
        db_path=clone_db,
        json_path=json_path,
    )
    with pytest.raises(indexer.ScanOutputsWriteError, match="one side") as exc_info:
        indexer.write_scan_outputs(clone_scan, str(clone_db), str(json_path))

    assert exc_info.value.db_written is False
    assert list_index_metadata(str(original_db))["generated"] == "original"
    assert list_index_metadata(str(clone_db))["generated"] == "original"
    assert json.loads(json_path.read_text(encoding="utf-8"))["generated"] == "original"


def test_copied_json_cannot_rebind_original_database(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".mdex" / "index.db"
    original_json = repo / ".mdex" / "original.json"
    copied_json = repo / ".mdex" / "copied.json"
    original = _scan_pair_index(
        repo,
        generation="original",
        db_path=db_path,
        json_path=original_json,
    )
    indexer.write_scan_outputs(original, str(db_path), str(original_json))
    shutil.copy2(original_json, copied_json)
    copied_scan = _scan_pair_index(
        repo,
        generation="copied",
        db_path=db_path,
        json_path=copied_json,
    )

    with pytest.raises(indexer.ScanOutputsWriteError, match="one side"):
        indexer.write_scan_outputs(copied_scan, str(db_path), str(copied_json))

    assert list_index_metadata(str(db_path))["generated"] == "original"
    assert json.loads(original_json.read_text(encoding="utf-8"))["generated"] == "original"
    assert json.loads(copied_json.read_text(encoding="utf-8"))["generated"] == "original"


def test_complete_copied_pair_can_be_relocated_together(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    original_db = repo / ".mdex" / "original.db"
    original_json = repo / ".mdex" / "original.json"
    copied_db = repo / ".mdex" / "copied.db"
    copied_json = repo / ".mdex" / "copied.json"
    original = _scan_pair_index(
        repo,
        generation="original",
        db_path=original_db,
        json_path=original_json,
    )
    indexer.write_scan_outputs(original, str(original_db), str(original_json))
    shutil.copy2(original_db, copied_db)
    shutil.copy2(original_json, copied_json)
    relocated = _scan_pair_index(
        repo,
        generation="relocated",
        db_path=copied_db,
        json_path=copied_json,
    )

    indexer.write_scan_outputs(relocated, str(copied_db), str(copied_json))

    assert list_index_metadata(str(copied_db))["generated"] == "relocated"
    assert json.loads(copied_json.read_text(encoding="utf-8"))["generated"] == "relocated"
    assert list_index_metadata(str(original_db))["generated"] == "original"
    assert json.loads(original_json.read_text(encoding="utf-8"))["generated"] == "original"


def test_database_cannot_rebind_to_missing_json_without_original_pair(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".mdex" / "index.db"
    original_json = repo / ".mdex" / "original.json"
    replacement_json = repo / ".mdex" / "replacement.json"
    original = _scan_pair_index(
        repo,
        generation="original",
        db_path=db_path,
        json_path=original_json,
    )
    indexer.write_scan_outputs(original, str(db_path), str(original_json))
    replacement = _scan_pair_index(
        repo,
        generation="replacement",
        db_path=db_path,
        json_path=replacement_json,
    )

    with pytest.raises(indexer.ScanOutputsWriteError, match="without its JSON pair"):
        indexer.write_scan_outputs(replacement, str(db_path), str(replacement_json))

    assert not replacement_json.exists()
    assert list_index_metadata(str(db_path))["generated"] == "original"
    assert json.loads(original_json.read_text(encoding="utf-8"))["generated"] == "original"


def test_legacy_pair_recovers_after_database_only_partial_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".mdex" / "index.db"
    json_path = repo / ".mdex" / "index.json"
    legacy = _index(repo, "legacy")
    legacy["scan_roots"] = [repo.resolve().as_posix()]
    write_sqlite(legacy, str(db_path))
    indexer.write_json(legacy, str(json_path))
    original_write_json = indexer._write_json_unlocked

    def fail_json(_index: dict[str, Any], _path: Path) -> None:
        raise OSError("simulated JSON failure")

    monkeypatch.setattr(indexer, "_write_json_unlocked", fail_json)
    first = _scan_pair_index(
        repo,
        generation="first-migration",
        db_path=db_path,
        json_path=json_path,
    )
    with pytest.raises(indexer.ScanOutputsWriteError) as exc_info:
        indexer.write_scan_outputs(first, str(db_path), str(json_path))
    assert exc_info.value.db_written is True
    assert exc_info.value.json_written is False
    partial_manifest = json.loads(list_index_metadata(str(db_path))["scan_manifest"])
    assert "previous_json_identity" in partial_manifest

    monkeypatch.setattr(indexer, "_write_json_unlocked", original_write_json)
    recovered = _scan_pair_index(
        repo,
        generation="recovered",
        db_path=db_path,
        json_path=json_path,
    )
    indexer.write_scan_outputs(recovered, str(db_path), str(json_path))

    db_metadata = list_index_metadata(str(db_path))
    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert db_metadata["generated"] == json_payload["generated"] == "recovered"
    assert db_metadata["scan_id"] == json_payload["scan_manifest"]["scan_id"]


def test_database_writes_and_enrichment_reject_hardlinked_database(tmp_path: Path) -> None:
    db_path = tmp_path / ".mdex" / "index.db"
    alias_path = tmp_path / ".mdex" / "alias.db"
    write_sqlite(_index(tmp_path, "seed"), str(db_path))
    try:
        os.link(db_path, alias_path)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(OSError, match="multiple hard links"):
        write_sqlite(_index(tmp_path, "replacement"), str(db_path))
    with pytest.raises(OSError, match="multiple hard links"):
        update_node_summary(str(alias_path), "node.md", "unsafe enrich")


def test_lock_file_hardlink_is_rejected_before_initialization(tmp_path: Path) -> None:
    db_path = tmp_path / ".mdex" / "index.db"
    db_path.parent.mkdir(parents=True)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"")
    try:
        os.link(sentinel, db_lock_path(db_path))
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(OSError, match="lock path must not have multiple hard links"):
        write_sqlite(_index(tmp_path, "seed"), str(db_path))

    assert sentinel.read_bytes() == b""
    assert not db_path.exists()


def test_concurrent_non_force_enrich_updates_only_once(tmp_path: Path) -> None:
    db_path = tmp_path / ".mdex" / "index.db"
    write_sqlite(_index(tmp_path, "seed"), str(db_path))
    barrier = threading.Barrier(2)
    results: list[dict[str, str]] = []

    def apply(summary: str) -> None:
        barrier.wait(timeout=5)
        results.append(
            apply_node_summary(
                str(db_path),
                "node.md",
                summary,
                overwrite_existing_agent=False,
            )
        )

    first = threading.Thread(target=apply, args=("first",))
    second = threading.Thread(target=apply, args=("second",))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(result["status"] for result in results) == ["skipped", "updated"]
    node = get_node(str(db_path), "node.md")
    assert node is not None
    assert node["summary"] in {"first", "second"}


def test_standalone_writers_cannot_split_manifest_backed_pair(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / ".mdex" / "index.db"
    json_path = repo / ".mdex" / "index.json"
    paired = _scan_pair_index(
        repo,
        generation="paired",
        db_path=db_path,
        json_path=json_path,
    )
    indexer.write_scan_outputs(paired, str(db_path), str(json_path))

    with pytest.raises(ValueError, match="write_scan_outputs"):
        write_sqlite(_index(repo, "standalone"), str(db_path))
    with pytest.raises(ValueError, match="write_scan_outputs"):
        indexer.write_json(_index(repo, "standalone"), str(json_path))

    assert list_index_metadata(str(db_path))["generated"] == "paired"
    assert json.loads(json_path.read_text(encoding="utf-8"))["generated"] == "paired"


def test_enrich_rejects_nodes_like_unowned_database_without_creating_tables(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "application.db"
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, summary TEXT)")
        connection.execute("INSERT INTO nodes VALUES ('node.md', 'sentinel')")
        connection.commit()

    with pytest.raises(ValueError, match="unowned SQLite database"):
        apply_node_summary(str(db_path), "node.md", "unsafe")

    with sqlite3.connect(str(db_path)) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert tables == {"nodes"}
        assert connection.execute("SELECT summary FROM nodes").fetchone() == ("sentinel",)


def test_graph_like_three_table_database_is_not_claimed_as_legacy_mdex(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / "graph.db"
    json_path = repo / "graph.json"
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, label TEXT)")
        connection.execute(
            "CREATE TABLE edges (src TEXT, dst TEXT, type TEXT, resolved INTEGER, "
            "PRIMARY KEY (src, dst, type))"
        )
        connection.execute(
            "CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO index_metadata VALUES (?, ?)",
            [
                ("generated", "legacy"),
                ("scan_root", str(repo)),
                ("scan_roots", json.dumps([str(repo)])),
            ],
        )
        connection.commit()

    incoming = _scan_pair_index(
        repo,
        generation="incoming",
        db_path=db_path,
        json_path=json_path,
    )
    with pytest.raises(indexer.ScanOutputsWriteError, match="unowned SQLite database"):
        indexer.write_scan_outputs(incoming, str(db_path), str(json_path))

    with sqlite3.connect(str(db_path)) as connection:
        assert connection.execute("SELECT label FROM nodes").fetchall() == []


def test_v040_legacy_pair_can_migrate_to_manifest_ownership(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = repo / "legacy.db"
    json_path = repo / "legacy.json"
    with sqlite3.connect(str(db_path)) as connection:
        connection.executescript(
            """
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY, title TEXT, type TEXT, project TEXT, status TEXT,
                summary TEXT, summary_source TEXT, summary_updated TEXT,
                estimated_tokens INTEGER NOT NULL DEFAULT 0, tags_json TEXT, updated TEXT,
                links_to_json TEXT, depends_on_json TEXT, relates_to_json TEXT
            );
            CREATE TABLE edges (
                src TEXT NOT NULL, dst TEXT NOT NULL, type TEXT NOT NULL,
                resolved INTEGER NOT NULL, PRIMARY KEY (src, dst, type)
            );
            CREATE TABLE index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE node_overrides (
                id TEXT PRIMARY KEY, summary TEXT NOT NULL,
                summary_source TEXT NOT NULL, summary_updated TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO index_metadata VALUES (?, ?)",
            [
                ("generated", "legacy"),
                ("scan_root", str(repo)),
                ("scan_roots", json.dumps([str(repo)])),
            ],
        )
        connection.commit()
    json_path.write_text(
        json.dumps({"generated": "legacy", "scan_roots": [str(repo)]}),
        encoding="utf-8",
    )

    incoming = _scan_pair_index(
        repo,
        generation="migrated",
        db_path=db_path,
        json_path=json_path,
    )
    indexer.write_scan_outputs(incoming, str(db_path), str(json_path))

    assert list_index_metadata(str(db_path))["scan_id"]
    assert json.loads(json_path.read_text(encoding="utf-8"))["scan_manifest"]["scan_id"]
