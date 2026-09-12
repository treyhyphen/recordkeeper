"""Command-line entry points for backup, sync, and local monitoring."""

import argparse
import fcntl
import json
import os
from pathlib import Path

from .accounts import ensure_accounts, select_lastfm_account
from .backup import backup, connect, export
from .config import Config, load_accounts, load_dotenv
from .db import connect as db_connect
from .db import migrate
from .lastfm import LastFM
from .sync import sync_scrobbles


def _lock(directory: Path):
    """Acquire the exclusive worker lock, raising if another worker is active."""
    lock = (directory / "worker.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Another worker is active") from None
    return lock


def main():
    os.umask(0o077)
    load_dotenv()
    config = Config.from_env()
    accounts = load_accounts()
    parser = argparse.ArgumentParser(
        description="Recordkeeper: your music, accounted for"
    )
    parser.add_argument("--data", default="data")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("backup")
    start.add_argument("--user", default=None, help="Last.fm username")
    start.add_argument(
        "--cutoff", type=int, help="UTC epoch upper boundary for a new snapshot"
    )
    commands.add_parser("status")
    output = commands.add_parser("export")
    output.add_argument("run_id", type=int)
    commands.add_parser("abandon")
    commands.add_parser("migrate", help="apply pending PostgreSQL schema migrations")
    commands.add_parser("accounts", help="list configured accounts")
    sync = commands.add_parser(
        "sync", help="incrementally sync Last.fm scrobbles into PostgreSQL"
    )
    sync.add_argument("--user", default=None, help="limit sync to one Last.fm account")

    args = parser.parse_args()

    if args.command == "migrate":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        applied = migrate(config.database_url)
        print("applied migrations:", applied or "none (already up to date)")
        return

    directory = Path(args.data)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    if args.command == "accounts":
        for acct in accounts:
            creds = ",".join(sorted(acct.credentials)) or "-"
            state = "enabled" if acct.enabled else "disabled"
            print(f"{acct.platform}\t{acct.username}\t{state}\tcreds:{creds}")
        return

    if args.command == "sync":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        selected = [a for a in accounts if a.platform == "lastfm" and a.enabled]
        if args.user:
            selected = [a for a in selected if a.username == args.user]
        if not selected:
            parser.exit(1, "No enabled Last.fm accounts configured\n")
        try:
            with _lock(directory):
                with db_connect(config.database_url) as conn:
                    ids = ensure_accounts(conn, accounts)
                    for acct in selected:
                        api_key = acct.credential("api_key")
                        if not api_key:
                            print(f"skip {acct.username}: missing api_key")
                            continue
                        account_id = ids[(acct.platform, acct.username)]
                        inserted = sync_scrobbles(
                            conn, account_id, acct.username, LastFM(api_key).fetch
                        )
                        print(f"{acct.username}: synced {inserted} new scrobbles")
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return

    with connect(directory / "archive.sqlite3") as db:
        if args.command == "status":
            rows = db.execute(
                "SELECT id,username,cutoff,next_page,expected,status FROM runs"
            ).fetchall()
            print(json.dumps(rows))
            return
        with _lock(directory):
            try:
                if args.command == "abandon":
                    db.execute(
                        "UPDATE runs SET status='abandoned' WHERE status='running'"
                    )
                    db.commit()
                    return
                if args.command == "backup":
                    acct = select_lastfm_account(accounts, args.user)
                    api_key = acct.credential("api_key")
                    if not api_key:
                        parser.exit(1, f"No api_key for account {acct.username}\n")
                    run_id, count = backup(
                        db, acct.username, LastFM(api_key).fetch, cutoff=args.cutoff
                    )
                    print(f"Verified snapshot {run_id}: {count} plays")
                else:
                    run_id = args.run_id
                print(export(db, run_id, directory / "exports"))
            except (RuntimeError, ValueError, KeyError) as exc:
                parser.exit(1, f"{exc}\n")
