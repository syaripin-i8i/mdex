from __future__ import annotations

from typing import Any

from mdex import __version__

SCHEMA_BASE_URL = "https://github.com/syaripin-i8i/mdex/schemas"

ERROR_CODE_BY_MESSAGE = {
    "db not found": "db_not_found",
    "invalid arguments": "invalid_arguments",
    "db resolution failed": "db_resolution_failed",
    "scan failed": "scan_failed",
    "doctor failed": "doctor_failed",
    "failed to load nodes": "load_nodes_failed",
    "invalid node id": "invalid_node_id",
    "node not indexed": "node_not_indexed",
    "node file not found": "node_file_not_found",
    "node not found": "node_not_found",
    "failed to load graph": "load_graph_failed",
    "failed to load stale nodes": "load_stale_nodes_failed",
    "context selection failed": "context_selection_failed",
    "start failed": "start_failed",
    "path must be absolute": "path_must_be_absolute",
    "summary file not found": "summary_file_not_found",
    "failed to read summary file": "read_summary_file_failed",
    "summary is required": "summary_required",
    "title is required": "invalid_arguments",
    "enrich failed": "enrich_failed",
    "not a git repository": "not_a_git_repository",
    "failed to collect git changed files": "collect_git_changed_files_failed",
    "impact failed": "impact_failed",
    "finish failed": "finish_failed",
    "failed to load runtime config": "load_runtime_config_failed",
    "new failed": "new_failed",
    "stamp failed": "stamp_failed",
}


def contract_metadata(command: str) -> dict[str, str]:
    clean_command = command.strip().lower() or "error"
    return {
        "contract_schema": f"{SCHEMA_BASE_URL}/{clean_command}.schema.json",
        "contract_version": __version__,
    }


def with_contract_metadata(payload: dict[str, Any], command: str) -> dict[str, Any]:
    return {
        **contract_metadata(command),
        **payload,
    }


def error_code(error: str) -> str:
    return ERROR_CODE_BY_MESSAGE.get(str(error).strip().lower(), "command_failed")


def with_error_contract(payload: dict[str, Any]) -> dict[str, Any]:
    output = dict(payload)
    output.setdefault("error", "command failed")
    output.setdefault("code", error_code(str(output.get("error", ""))))
    return with_contract_metadata(output, "error")
