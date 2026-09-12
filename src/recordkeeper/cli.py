"""Command-line entry points for backup and local monitoring."""

import argparse
import fcntl
import json
import os
from pathlib import Path

from .backup import backup, connect, export
from .lastfm import LastFM, vault_key


def main():
    """Run an exclusive backup, inspect progress, or export a completed run."""
    os.umask(0o077)
    parser = argparse.ArgumentParser(
        description="Recordkeeper: your music, accounted for"
    )
    parser.add_argument("--data", default="data")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("backup")
    start.add_argument("--user", required=True)
    start.add_argument(
        "--cutoff", type=int, help="UTC epoch upper boundary for a new snapshot"
    )
    commands.add_parser("status")
    output = commands.add_parser("export")
    output.add_argument("run_id", type=int)
    commands.add_parser("abandon")
    args = parser.parse_args()
    directory = Path(args.data)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect(directory / "archive.sqlite3") as db:
        if args.command == "status":
            rows = db.execute(
                "SELECT id,username,cutoff,next_page,expected,status FROM runs"
            ).fetchall()
            print(json.dumps(rows))
            return
        with (directory / "worker.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.exit(1, "Another worker is active\n")
            try:
                if args.command == "abandon":
                    db.execute(
                        "UPDATE runs SET status='abandoned' WHERE status='running'"
                    )
                    db.commit()
                    return
                if args.command == "backup":
                    run_id, count = backup(
                        db, args.user, LastFM(vault_key()).fetch, cutoff=args.cutoff
                    )
                    print(f"Verified snapshot {run_id}: {count} plays")
                else:
                    run_id = args.run_id
                print(export(db, run_id, directory / "exports"))
            except (RuntimeError, ValueError, KeyError) as exc:
                parser.exit(1, f"{exc}\n")
