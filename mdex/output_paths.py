from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from mdex.locking import resource_lock_path
from mdex.path_identity import paths_have_same_identity


_WINDOWS_RESERVED_BASENAMES = {
    "aux",
    "clock$",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
    *(f"com{number}" for number in "¹²³"),
    *(f"lpt{number}" for number in "¹²³"),
}


class ScanIndex(dict[str, Any]):
    def __init__(
        self,
        *args: Any,
        source_paths: Iterable[str | Path] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.source_paths = tuple(Path(path).resolve() for path in source_paths)


def ensure_portable_output_name(path: str | Path) -> None:
    candidate = Path(path)
    anchor = candidate.anchor
    for component in candidate.parts:
        if component in {anchor, candidate.drive, "/", "\\"}:
            continue
        if not component or component.rstrip(" .") != component:
            raise ValueError(
                f"scan output path components must not end in a dot or space: {path}"
            )
        if ":" in component:
            raise ValueError(f"scan output path components must not contain a colon: {path}")
        device_base = component.split(".", 1)[0].rstrip(" ").casefold()
        if device_base in _WINDOWS_RESERVED_BASENAMES:
            raise ValueError(f"scan output path component is reserved on Windows: {path}")


def ensure_scan_output_resource(path: str | Path) -> None:
    ensure_portable_output_name(path)
    if Path(path).name.casefold().endswith(".lock"):
        raise ValueError(f"scan outputs must not use the reserved .lock filename suffix: {path}")


def repo_contained_output_path(repo_root: Path, value: str, *, key: str) -> Path:
    root = repo_root.resolve()
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{key} must stay within repo: {resolved}") from exc
    return resolved


def configured_generated_output_path(
    repo_root: Path,
    value: str | Path,
    *,
    key: str,
) -> Path:
    """Resolve a config/default output and confine it to ``.mdex``.

    Command-line paths remain an explicit escape hatch. Repository configuration
    is not allowed to turn an ordinary source or Git-control file into a generated
    output target.
    """

    resolved = repo_contained_output_path(repo_root, str(value), key=key)
    generated_root = (repo_root.resolve() / ".mdex").resolve()
    try:
        relative = resolved.relative_to(generated_root)
    except ValueError as exc:
        if resolved.exists():
            raise ValueError(
                f"{key} must not overwrite existing repository file outside .mdex: {resolved}"
            ) from exc
        raise ValueError(f"{key} must stay within the repo .mdex directory: {resolved}") from exc
    if not relative.parts:
        raise ValueError(f"{key} must name a file within the repo .mdex directory")
    try:
        ensure_scan_output_resource(resolved)
    except ValueError as exc:
        raise ValueError(f"{key}: {exc}") from exc
    return resolved


def paths_refer_to_same_file(first: str | Path, second: str | Path) -> bool:
    return paths_have_same_identity(first, second)


def ensure_distinct_scan_outputs(db_path: str | Path, output_path: str | Path) -> None:
    for path in (Path(db_path), Path(output_path)):
        ensure_scan_output_resource(path)
    if paths_refer_to_same_file(db_path, output_path):
        raise ValueError("database and JSON output paths must be different")
    db_lock = resource_lock_path(db_path)
    json_lock = resource_lock_path(output_path)
    if paths_refer_to_same_file(output_path, db_lock) or paths_refer_to_same_file(db_path, json_lock):
        raise ValueError("database and JSON outputs must not collide with scan lock paths")


def ensure_outputs_do_not_overwrite_sources(
    index: dict[str, object],
    *output_paths: str | Path,
) -> None:
    sources = [Path(value) for value in getattr(index, "source_paths", ())]
    for output_path in output_paths:
        protected_outputs = (Path(output_path), resource_lock_path(output_path))
        for protected_path in protected_outputs:
            for source_path in sources:
                if paths_refer_to_same_file(protected_path, source_path):
                    raise ValueError(
                        "scan output or lock path must not overwrite indexed source: "
                        f"{source_path.resolve()}"
                    )
