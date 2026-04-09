from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from runtime.reader import read_node_text
from runtime.store import get_node, get_scan_root, resolve_node_id_from_path, update_node_summary

ENRICH_PROMPT = (
    "別のAIエージェントがタスク着手前にこのファイルを読むべきか判断するための情報を2〜3文で書いてください。"
    "何が書かれているか・どんな制約や判断を含むか・いつ参照すべきかを含めること。"
)

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")


def resolve_node_id(node_or_path: str, db_path: str, *, path_mode: bool = False) -> str | None:
    if path_mode:
        return resolve_node_id_from_path(db_path, node_or_path)
    clean = node_or_path.strip()
    if not clean:
        return None
    return clean


def _should_skip_existing(summary: str, force: bool) -> bool:
    if force:
        return False
    return len(summary.strip()) >= 80


def _generate_summary_with_anthropic(content: str, *, api_key: str, model: str) -> str:
    try:
        import anthropic  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"anthropic sdk unavailable: {exc}") from exc

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=220,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{ENRICH_PROMPT}\n\n"
                    "## 対象ファイル本文\n"
                    f"{content[:16000]}"
                ),
            }
        ],
    )
    chunks: list[str] = []
    for item in getattr(response, "content", []) or []:
        text = getattr(item, "text", "")
        if text:
            chunks.append(str(text))
    summary = "\n".join(chunks).strip()
    if not summary:
        raise RuntimeError("empty response from anthropic")
    return summary


def enrich_node(
    node_id: str,
    db_path: str,
    *,
    force: bool = False,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    node = get_node(db_path, node_id)
    if node is None:
        return {"status": "error", "error": "node not found", "node_id": node_id}

    existing_summary = str(node.get("summary", "") or "")
    if _should_skip_existing(existing_summary, force):
        return {
            "status": "skipped",
            "reason": "summary already sufficient",
            "node_id": node_id,
        }

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "skipped",
            "reason": "missing ANTHROPIC_API_KEY",
            "node_id": node_id,
        }

    scan_root = get_scan_root(db_path, default=".")
    try:
        content = read_node_text(scan_root, node_id)
    except FileNotFoundError:
        return {"status": "error", "error": "node file not found", "node_id": node_id}

    try:
        summary = _generate_summary_with_anthropic(content, api_key=api_key, model=model)
    except Exception as exc:
        return {
            "status": "error",
            "error": "enrich failed",
            "node_id": node_id,
            "detail": str(exc),
        }

    updated = update_node_summary(db_path, node_id, summary)
    if not updated:
        return {"status": "error", "error": "failed to persist summary", "node_id": node_id}

    return {
        "status": "enriched",
        "node_id": node_id,
        "summary": summary,
        "scan_root": Path(scan_root).as_posix(),
    }
