from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mdex.context import project_actionable_digest, select_context, zero_hits_field
from mdex.start import build_start_payload
from mdex.store import list_index_metadata

DEFAULT_ARTIFACT_INDEX_STALE_AFTER_HOURS = 24

DEFAULT_INDEX_ALIASES = {
    "repo": ".mdex/mdex_index.db",
    "task": ".mdex/task_history.db",
    "tasks": ".mdex/task_history.db",
    "task_history": ".mdex/task_history.db",
    "memory": ".mdex/memory.db",
    "artifact": ".mdex/artifacts.db",
    "artifacts": ".mdex/artifacts.db",
}


def _coerce_include(value: str | None) -> list[str]:
    raw = str(value or "repo").strip()
    if not raw:
        return ["repo"]
    if raw.lower() in {"all", "*"}:
        return ["repo", "task", "memory"]
    aliases: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        alias = item.strip().lower()
        if not alias:
            continue
        if alias == "tasks":
            alias = "task"
        if alias == "task_history":
            alias = "task"
        if alias in seen:
            continue
        seen.add(alias)
        aliases.append(alias)
    return aliases or ["repo"]


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bounded_override(config: dict[str, Any], key: str, default: int) -> int:
    if key not in config:
        return default
    return max(1, min(default, _positive_int(config.get(key), default)))


def _repo_root_from_db_info(db_info: dict[str, Any]) -> Path:
    repo_root_raw = str(db_info.get("repo_root", "") or "").strip()
    return Path(repo_root_raw).resolve() if repo_root_raw else Path.cwd().resolve()


def _display_path(path: str | Path, repo_root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(repo_root.resolve())
    except ValueError:
        return str(resolved)
    return relative.as_posix() or "."


def _public_index_spec(spec: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    output = dict(spec)
    if "path" in output:
        output["path"] = _display_path(str(output["path"]), repo_root)
    return output


def _public_start_payload(payload: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    output = dict(payload)
    db = output.get("db")
    if isinstance(db, dict):
        public_db = dict(db)
        if "path" in public_db:
            public_db["path"] = _display_path(str(public_db["path"]), repo_root)
        output["db"] = public_db
    return output


def _parse_utc_timestamp(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _artifact_index_stale_after_hours(config: dict[str, Any]) -> int:
    if not isinstance(config, dict):
        config = {}
    raw = config.get("index_stale_after_hours")
    if raw is None:
        raw = config.get("stale_after_hours")
    return _positive_int(raw, DEFAULT_ARTIFACT_INDEX_STALE_AFTER_HOURS)


def _is_artifact_alias(alias: str) -> bool:
    return alias in {"artifact", "artifacts"}


def _index_timestamp(path: Path) -> tuple[str, datetime | None, str]:
    try:
        generated = str(list_index_metadata(str(path)).get("generated", "") or "").strip()
    except Exception:
        generated = ""
    parsed = _parse_utc_timestamp(generated)
    if parsed is not None:
        return generated, parsed, "index_metadata.generated"

    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return generated, None, "unavailable"
    return mtime.isoformat(), mtime, "db_mtime"


def _artifact_index_age(spec: dict[str, Any]) -> dict[str, Any]:
    stale_after_hours = _artifact_index_stale_after_hours(spec.get("config", {}))
    if not bool(spec.get("exists", False)):
        return {
            "ready": False,
            "generated": "",
            "source": "missing",
            "fresh": False,
            "stale": True,
            "age_hours": None,
            "age_days": None,
            "stale_after_hours": stale_after_hours,
            "reason": "index_db_missing",
        }

    generated, parsed, source = _index_timestamp(Path(str(spec["path"])))
    if parsed is None:
        return {
            "ready": True,
            "generated": generated,
            "source": source,
            "fresh": False,
            "stale": True,
            "age_hours": None,
            "age_days": None,
            "stale_after_hours": stale_after_hours,
            "reason": "missing_or_invalid_generated_timestamp",
        }

    age_hours = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0)
    age_days = age_hours / 24.0
    fresh = age_hours <= float(stale_after_hours)
    return {
        "ready": True,
        "generated": generated,
        "source": source,
        "fresh": fresh,
        "stale": not fresh,
        "age_hours": round(age_hours, 2),
        "age_days": round(age_days, 2),
        "stale_after_hours": stale_after_hours,
        "reason": "fresh_index" if fresh else "stale_index",
    }


def _with_artifact_index_age(spec: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    output = _public_index_spec(spec, repo_root)
    if _is_artifact_alias(str(spec.get("alias", ""))):
        output["artifacts_index_age"] = _artifact_index_age(spec)
    return output


def _artifact_scan_action(spec: dict[str, Any], repo_root: Path, *, reason: str) -> tuple[str, dict[str, Any]]:
    db_path = _display_path(str(spec.get("path", "")), repo_root)
    legacy = f"run mdex scan-artifacts --db {db_path}"
    structured = {
        "command": "mdex",
        "args": ["scan-artifacts", "--db", db_path],
        "reason": reason,
    }
    return legacy, structured


def _configured_indexes(db_info: dict[str, Any]) -> dict[str, Any]:
    config = db_info.get("config", {})
    if not isinstance(config, dict):
        return {}
    raw = config.get("indexes")
    if raw is None:
        raw = config.get("multi_index")
    return raw if isinstance(raw, dict) else {}


def _resolve_index_db(alias: str, db_info: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    repo_root = _repo_root_from_db_info(db_info)
    if alias == "repo":
        return Path(str(db_info["path"])).resolve(), str(db_info.get("source", "unknown")), {}

    configured = _configured_indexes(db_info)
    spec = configured.get(alias, {})
    if not isinstance(spec, dict):
        spec = {}
    raw_db = spec.get("db")
    if isinstance(raw_db, str) and raw_db.strip():
        candidate = Path(raw_db.strip())
        path = candidate.resolve() if candidate.is_absolute() else (repo_root / candidate).resolve()
        return path, f"config:{alias}", spec

    default_relative = DEFAULT_INDEX_ALIASES.get(alias, f".mdex/{alias}.db")
    return (repo_root / default_relative).resolve(), f"default:{alias}", spec


def resolve_index_specs(db_info: dict[str, Any], include: str | None) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for alias in _coerce_include(include):
        path, source, config = _resolve_index_db(alias, db_info)
        specs.append(
            {
                "alias": alias,
                "kind": alias,
                "path": str(path),
                "source": source,
                "exists": path.exists(),
                "config": config,
            }
        )
    return specs


def _dedupe_sequence(items: list[Any], *, key_fields: tuple[str, ...]) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            key = "|".join(str(item.get(field, "")) for field in key_fields)
        else:
            key = str(item)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def merge_actionable_digests(payloads: list[dict[str, Any]], *, digest: str) -> dict[str, Any]:
    digests = [item.get("actionable_digest") for item in payloads if isinstance(item.get("actionable_digest"), dict)]
    intent = next((str(item.get("intent", "")).strip() for item in digests if str(item.get("intent", "")).strip()), "")
    merged = {
        "intent": intent,
        "relevant_docs": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("relevant_docs", []) or [])],
            key_fields=("index", "id"),
        ),
        "relevant_artifacts": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("relevant_artifacts", []) or [])],
            key_fields=("index", "id"),
        ),
        "relevant_task_history": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("relevant_task_history", []) or [])],
            key_fields=("index", "id"),
        ),
        "likely_code_entrypoints": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("likely_code_entrypoints", []) or [])],
            key_fields=("index", "id", "path", "name"),
        ),
        "known_guardrails": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("known_guardrails", []) or [])],
            key_fields=("index", "id"),
        ),
        "discovery_candidates": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("discovery_candidates", []) or [])],
            key_fields=("index", "id"),
        ),
        "suggested_rg": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("suggested_rg", []) or [])],
            key_fields=("command", "pattern"),
        ),
        "context_gaps": _dedupe_sequence(
            [row for digest_item in digests for row in list(digest_item.get("context_gaps", []) or [])],
            key_fields=(),
        ),
    }
    return project_actionable_digest(merged, digest)


def _stamp_rows(rows: list[dict[str, Any]], alias: str) -> list[dict[str, Any]]:
    stamped: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["index"] = alias
        stamped.append(item)
    return stamped


def _stamp_digest(payload: dict[str, Any], alias: str) -> None:
    digest = payload.get("actionable_digest")
    if not isinstance(digest, dict):
        return
    for key in (
        "relevant_docs",
        "relevant_artifacts",
        "relevant_task_history",
        "likely_code_entrypoints",
        "known_guardrails",
        "discovery_candidates",
    ):
        rows = digest.get(key)
        if isinstance(rows, list):
            digest[key] = _stamp_rows([row for row in rows if isinstance(row, dict)], alias)


def _budgeted_merged_nodes(rows: list[dict[str, Any]], budget: int) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        estimated = _positive_int(row.get("estimated_tokens", 0), 0)
        projected = total + estimated
        if projected > budget:
            dropped.append(
                {
                    "id": str(row.get("id", "")),
                    "index": str(row.get("index", "")),
                    "score": round(float(row.get("score", 0.0) or 0.0), 3),
                    "estimated_tokens": estimated,
                    "projected_tokens": projected,
                    "budget_drop_reason": "multi_index_budget_exceeded",
                    "soft_cap": budget,
                }
            )
            continue
        selected.append(row)
        total = projected
    return selected, total, dropped


def build_multi_context_payload(
    query: str,
    db_info: dict[str, Any],
    *,
    include: str | None,
    budget: int,
    limit: int,
    include_content: bool,
    actionable: bool,
    digest: str,
    scoring_config: dict[str, Any] | None,
    scoring_config_source: str,
) -> dict[str, Any]:
    specs = resolve_index_specs(db_info, include)
    repo_root = _repo_root_from_db_info(db_info)
    per_index: dict[str, Any] = {}
    contexts: list[dict[str, Any]] = []
    safe_budget = _positive_int(budget, 4000)
    safe_limit = _positive_int(limit, 10)
    existing_specs = [spec for spec in specs if bool(spec.get("exists", False))]
    shared_budget = max(1, safe_budget // max(1, len(existing_specs)))
    shared_limit = max(1, safe_limit // max(1, len(existing_specs)))
    index_actions: list[str] = []
    index_actions_v2: list[dict[str, Any]] = []
    for spec in specs:
        alias = str(spec["alias"])
        public_spec = _with_artifact_index_age(spec, repo_root)
        if not bool(spec.get("exists", False)):
            per_index[alias] = {"ok": False, **public_spec, "reason": "index_db_missing"}
            if _is_artifact_alias(alias):
                legacy, structured = _artifact_scan_action(
                    spec,
                    repo_root,
                    reason="scan missing artifact index before querying the artifacts lane",
                )
                index_actions.append(legacy)
                index_actions_v2.append(structured)
            continue
        payload = select_context(
            query,
            str(spec["path"]),
            budget=_bounded_override(spec.get("config", {}), "budget", shared_budget),
            limit=_bounded_override(spec.get("config", {}), "limit", shared_limit),
            include_content=include_content,
            actionable=actionable,
            digest=digest,
            scoring_config=scoring_config,
            scoring_config_source=scoring_config_source,
        )
        _stamp_digest(payload, alias)
        payload["index"] = alias
        contexts.append(payload)
        per_index[alias] = {
            "ok": True,
            **public_spec,
            "summary": {
                "nodes": len(payload.get("nodes", [])),
                "total_tokens": int(payload.get("total_tokens", 0) or 0),
            },
        }
        age = public_spec.get("artifacts_index_age")
        if isinstance(age, dict) and bool(age.get("stale", False)):
            legacy, structured = _artifact_scan_action(
                spec,
                repo_root,
                reason="refresh stale artifact index before trusting artifacts lane coverage",
            )
            index_actions.append(legacy)
            index_actions_v2.append(structured)

    merged_nodes = [
        {**row, "index": str(payload.get("index", ""))}
        for payload in contexts
        for row in list(payload.get("nodes", []) or [])
        if isinstance(row, dict)
    ]
    merged_nodes.sort(key=lambda item: (-float(item.get("score", 0.0) or 0.0), str(item.get("index", "")), str(item.get("id", ""))))
    budgeted_nodes, merged_total_tokens, merged_dropped = _budgeted_merged_nodes(merged_nodes, safe_budget)
    output: dict[str, Any] = {
        "query": query,
        "multi_index": {"include": [spec["alias"] for spec in specs], "indexes": per_index},
        "per_index_context": {str(item.get("index", "")): item for item in contexts},
        "nodes": budgeted_nodes[:safe_limit],
        "total_tokens": merged_total_tokens,
        "budget": safe_budget,
        "budget_dropped_nodes": merged_dropped[:10],
    }
    # Claim only when every requested index was actually searched and each
    # bounded a true zero; a hit trimmed away by the merge budget is
    # truncation accounting, and an unsearched (missing) index leaves the
    # zero unbounded — neither may claim zero_hits.
    if (
        not output["nodes"]
        and contexts
        and len(contexts) == len(specs)
        and all("zero_hits" in item for item in contexts)
    ):
        output["zero_hits"] = zero_hits_field(query)
    if actionable:
        output["recommended_read_order"] = _dedupe_sequence(
            [row for item in contexts for row in _stamp_rows(list(item.get("recommended_read_order", []) or []), str(item.get("index", "")))],
            key_fields=("index", "id"),
        )
        output["recommended_next_actions"] = _dedupe_sequence(
            [row for item in contexts for row in list(item.get("recommended_next_actions", []) or [])]
            + index_actions,
            key_fields=(),
        )
        output["recommended_next_actions_v2"] = _dedupe_sequence(
            [row for item in contexts for row in list(item.get("recommended_next_actions_v2", []) or [])]
            + index_actions_v2,
            key_fields=("command", "args"),
        )
        output["deferred_nodes"] = _dedupe_sequence(
            [row for item in contexts for row in _stamp_rows(list(item.get("deferred_nodes", []) or []), str(item.get("index", "")))],
            key_fields=("index", "id"),
        )
        output["discovery_candidates"] = _dedupe_sequence(
            [row for item in contexts for row in _stamp_rows(list(item.get("discovery_candidates", []) or []), str(item.get("index", "")))],
            key_fields=("index", "id"),
        )[:3]
        output["confidence"] = round(max([float(item.get("confidence", 0.0) or 0.0) for item in contexts] or [0.0]), 2)
        output["why_this_set"] = _dedupe_sequence(
            [row for item in contexts for row in list(item.get("why_this_set", []) or [])],
            key_fields=(),
        )
        output["actionable_digest"] = merge_actionable_digests(contexts, digest=digest)
    return output


def build_multi_start_payload(
    task: str,
    db_info: dict[str, Any],
    *,
    include: str | None,
    budget: int,
    limit: int,
    include_content: bool,
    digest: str,
    scoring_config: dict[str, Any] | None,
    scoring_config_source: str,
) -> dict[str, Any]:
    specs = resolve_index_specs(db_info, include)
    repo_root = _repo_root_from_db_info(db_info)
    starts: list[dict[str, Any]] = []
    per_index: dict[str, Any] = {}
    safe_budget = _positive_int(budget, 4000)
    safe_limit = _positive_int(limit, 10)
    existing_specs = [spec for spec in specs if bool(spec.get("exists", False))]
    shared_budget = max(1, safe_budget // max(1, len(existing_specs)))
    shared_limit = max(1, safe_limit // max(1, len(existing_specs)))
    for spec in specs:
        alias = str(spec["alias"])
        public_spec = _with_artifact_index_age(spec, repo_root)
        if not bool(spec.get("exists", False)):
            per_index[alias] = {"ok": False, **public_spec, "reason": "index_db_missing"}
            continue
        payload = build_start_payload(
            task,
            str(spec["path"]),
            db_source=str(spec["source"]),
            budget=_bounded_override(spec.get("config", {}), "budget", shared_budget),
            limit=_bounded_override(spec.get("config", {}), "limit", shared_limit),
            include_content=include_content,
            digest=digest,
            scoring_config=scoring_config,
            scoring_config_source=scoring_config_source,
        )
        _stamp_digest(payload, alias)
        payload["index"] = alias
        starts.append(payload)
        per_index[alias] = {"ok": True, **public_spec, "index_status": payload.get("index_status", {})}

    contextish = build_multi_context_payload(
        task,
        db_info,
        include=include,
        budget=safe_budget,
        limit=safe_limit,
        include_content=include_content,
        actionable=True,
        digest=digest,
        scoring_config=scoring_config,
        scoring_config_source=scoring_config_source,
    )
    return {
        "task": task,
        "db": {
            "path": _display_path(str(db_info.get("path", "")), repo_root),
            "source": str(db_info.get("source", "unknown")),
        },
        "multi_index": {"include": [spec["alias"] for spec in specs], "indexes": per_index},
        "per_index_start": {
            str(item.get("index", "")): _public_start_payload(item, repo_root)
            for item in starts
        },
        "index_status": {
            "ready": bool(starts) and len(starts) == len(specs),
            "fresh": bool(starts)
            and len(starts) == len(specs)
            and all(bool(item.get("index_status", {}).get("fresh", False)) for item in starts),
            "missing": [str(spec["alias"]) for spec in specs if not bool(spec.get("exists", False))],
        },
        "entrypoint_reason": "multi_index_ranked_entrypoint_available" if starts else "no_available_indexes",
        **{key: value for key, value in contextish.items() if key not in {"query", "multi_index", "per_index_context"}},
    }
