from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Thin wrapper for `mdex finish`.")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--db", help="DB path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary-file", help="Summary file path")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--changed-files-from-git", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    command = ["mdex", "finish", "--task", args.task, "--limit", str(args.limit)]
    if args.db:
        command.extend(["--db", args.db])
    if args.dry_run:
        command.append("--dry-run")
    if args.summary_file:
        command.extend(["--summary-file", args.summary_file])
    if args.scan:
        command.append("--scan")
    if args.changed_files_from_git:
        command.append("--changed-files-from-git")

    completed = subprocess.run(command, check=False, text=True, encoding="utf-8", capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
