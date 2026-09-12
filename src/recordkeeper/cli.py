"""Command-line entry points for backup and local monitoring."""

import argparse
import fcntl
import json
import os
from pathlib import Path

from .backup import backup, connect, export
from .config import Config, load_dotenv
from .db import migrate
from .lastfm import LastFM


def main():
    """Run an exclusive backup, inspect progress, or export a completed run."""
    os.umask(0o077)
    load_dotenv()
    config = Config.from_env()
    parser = argparse.ArgumentParser(
        description="Recordkeeper: your music, accounted for"
    )
    parser.add_argument("--data", default="data")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("backup")
    start.add_argument(
        "--user", default=None, help="Last.fm username (default: $LASTFM_USERNAME)"
    )
    start.add_argument(
        "--cutoff", type=int, help="UTC epoch upper boundary for a new snapshot"
    )
    commands.add_parser("status")
    output = commands.add_parser("export")
    output.add_argument("run_id", type=int)
    commands.add_parser("abandon")
    commands.add_parser("migrate", help="apply pending PostgreSQL schema migrations")
    args = parser.parse_args()
    if args.command == "migrate":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        applied = migrate(config.database_url)
        print("applied migrations:", applied or "none (already up to date)")
        return
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
                    username, api_key = config.require_lastfm_read()
                    if args.user:
                        username = args.user
                    run_id, count = backup(
                        db, username, LastFM(api_key).fetch, cutoff=args.cutoff
                    )
                    print(f"Verified snapshot {run_id}: {count} plays")
                else:
                    run_id = args.run_id
                print(export(db, run_id, directory / "exports"))
            except (RuntimeError, ValueError, KeyError) as exc:
                parser.exit(1, f"{exc}\n")
