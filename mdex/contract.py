from __future__ import annotations

from typing import Any

from mdex import __version__

SCHEMA_BASE_URL = "https://github.com/syaripin-i8i/mdex/schemas"


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
