from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_REFRESH_PATTERNS = (
    ".mdex/config.json",
    "control/scan_config.json",
    "AGENT.md",
    "README.md",
    "CHANGELOG.md",
    "docs/",
    "schemas/",
    "mdex/",
    "tests/",
    "tools/",
)

TASK_REFRESH_PATTERNS = (
    "tasks/",
    "control/task_scan_config.json",
)

INDEX_ENGINE_REFRESH_PATTERNS = (
    "mdex/builder.py",
    "mdex/indexer.py",
    "mdex/parser.py",
    "mdex/scanner.py",
    "mdex/store.py",
    "mdex/tokens.py",
)

MEMORY_REFRESH_PATTERNS = (
    "memory/",
    "memories/",
    ".mdex/memory",
)


def _to_posix(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = _to_posix(path)
    for pattern in patterns:
        clean = pattern.strip()
        if not clean:
            continue
        if clean.endswith("/"):
            if normalized.startswith(clean):
                return True
            continue
        if normalized == clean or normalized.startswith(f"{clean}/"):
            return True
    return False


def classify_refresh_targets(changed_files: list[str]) -> dict[str, Any]:
    normalized = [_to_posix(path) for path in changed_files if str(path).strip()]
    repo_files = [path for path in normalized if _matches(path, REPO_REFRESH_PATTERNS)]
    task_files = [
        path
        for path in normalized
        if _matches(path, TASK_REFRESH_PATTERNS) or _matches(path, INDEX_ENGINE_REFRESH_PATTERNS)
    ]
    memory_files = [path for path in normalized if _matches(path, MEMORY_REFRESH_PATTERNS)]

    return {
        "changed_files": normalized,
        "repo": {
            "needed": bool(repo_files),
            "reason": "repo-index source changed" if repo_files else "no repo-index source change detected",
            "files": repo_files,
            "command": [sys.executable, "-m", "mdex.cli", "scan"],
        },
        "task": {
            "needed": bool(task_files),
            "reason": "task-history source changed" if task_files else "no task-history source change detected",
            "files": task_files,
            "command": [
                sys.executable,
                "-m",
                "mdex.cli",
                "scan",
                "--root",
                "tasks",
                "--node-id-root",
                ".",
                "--config",
                "control/task_scan_config.json",
                "--db",
                ".mdex/task_history.db",
                "--output",
                ".mdex/task_history.json",
            ],
        },
        "memory": {
            "needed": bool(memory_files),
            "reason": "memory-index source changed" if memory_files else "no memory-index source change detected",
            "files": memory_files,
            "command": None,
            "note": "configure a memory index command in the owning repo wrapper",
        },
    }


def _git_changed_files(repo_root: Path) -> list[str]:
    head_result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=False,
        check=False,
    )
    head_exists = head_result.returncode == 0

    cached_result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-only", "-z"],
        cwd=repo_root,
        capture_output=True,
        text=False,
        check=False,
    )
    if cached_result.returncode != 0:
        detail = cached_result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "git diff --cached failed")

    diff_stdout = b""
    if head_exists:
        diff_result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "diff", "--name-only", "HEAD", "-z"],
            cwd=repo_root,
            capture_output=True,
            text=False,
            check=False,
        )
        if diff_result.returncode != 0:
            detail = diff_result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "git diff failed")
        diff_stdout = diff_result.stdout

    untracked_result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        capture_output=True,
        text=False,
        check=False,
    )
    if untracked_result.returncode != 0:
        detail = untracked_result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "git ls-files failed")

    return sorted(
        {
            path
            for output in (cached_result.stdout, diff_stdout, untracked_result.stdout)
            for path in output.decode("utf-8", errors="surrogateescape").split("\0")
            if path
        }
    )


def _run_repo_scan(repo_root: Path, command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    payload: dict[str, Any] = {
        "command": command,
        "exit_code": result.returncode,
    }
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        try:
            payload["stdout_json"] = json.loads(stdout)
        except json.JSONDecodeError:
            payload["stdout"] = stdout
    if stderr:
        try:
            payload["stderr_json"] = json.loads(stderr)
        except json.JSONDecodeError:
            payload["stderr"] = stderr
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh mdex-related context indexes based on changed files.")
    parser.add_argument("paths", nargs="*", help="Changed files. Defaults to git diff --name-only HEAD.")
    parser.add_argument("--repo-root", default=".", help="Repository root for git and mdex scan.")
    parser.add_argument("--dry-run", action="store_true", help="Only report required refreshes.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        changed_files = list(args.paths) if args.paths else _git_changed_files(repo_root)
        report = classify_refresh_targets(changed_files)
        report["dry_run"] = bool(args.dry_run)
        report["executed"] = []

        if report["repo"]["needed"] and not args.dry_run:
            report["executed"].append({"target": "repo", **_run_repo_scan(repo_root, report["repo"]["command"])})
        if report["task"]["needed"] and not args.dry_run:
            report["executed"].append({"target": "task", **_run_repo_scan(repo_root, report["task"]["command"])})

        print(json.dumps(report, ensure_ascii=False, indent=2))
        if any(item.get("exit_code", 0) != 0 for item in report["executed"]):
            return 2
        return 0
    except Exception as exc:
        print(json.dumps({"error": "context refresh failed", "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
