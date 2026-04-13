from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Thin wrapper for `mdex start`.")
    parser.add_argument("task", help="Task description")
    parser.add_argument("--db", help="DB path")
    parser.add_argument("--budget", type=int, default=4000)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--include-content", action="store_true")
    args = parser.parse_args()

    command = ["mdex", "start", args.task, "--budget", str(args.budget), "--limit", str(args.limit)]
    if args.db:
        command.extend(["--db", args.db])
    if args.include_content:
        command.append("--include-content")

    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
