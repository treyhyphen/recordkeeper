"""Command-line entry points for backup, sync, and local monitoring."""

import argparse
import fcntl
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pylast
from spotipy.exceptions import SpotifyException

from .accounts import (
    ensure_accounts,
    select_lastfm_account,
    select_plex_account,
    select_spotify_account,
)
from .backup import backup, connect, export
from .config import Config, load_accounts, load_dotenv
from .db import connect as db_connect
from .db import migrate
from .lastfm import LastFM
from .likes import (
    build_network,
    complete_session,
    get_auth_url,
    save_session_key,
    sync_loves,
)
from .plex import connect as plex_connect
from .plex import sync_inventory
from .spotify import Spotify
from .spotify_backup import snapshot_account
from .support import sync_recommendations
from .sync import sync_scrobbles
from .throwback import load_playlist_id, save_playlist_id, sync_throwback
from .vinyl import detect_imports, scrobble_imports


def _spotify_client(acct, directory: Path) -> Spotify:
    client_id = acct.credential("client_id")
    client_secret = acct.credential("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError(f"No client_id/client_secret for account {acct.username}")
    redirect_uri = acct.credential("redirect_uri")
    return Spotify(
        acct.username, client_id, client_secret, redirect_uri, data_dir=str(directory)
    )


def _lock(directory: Path, name: str = "worker"):
    """Acquire an exclusive per-command lock, raising if that command is active.

    Each subcommand uses its own lock file so a long-running job (e.g. the
    likes→loves backfill) doesn't block unrelated jobs that touch different
    external APIs.
    """
    lock = (directory / f"{name}.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError(f"Another {name} worker is active") from None
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
    sync.add_argument(
        "--deep",
        action="store_true",
        help="full-history re-fetch to catch backdated scrobbles",
    )
    auth = commands.add_parser(
        "spotify-auth", help="interactive OAuth for a Spotify account"
    )
    auth.add_argument("--user", default=None, help="Spotify account username")
    spotify_backup = commands.add_parser(
        "spotify-backup", help="snapshot Spotify playlists and saved tracks"
    )
    spotify_backup.add_argument(
        "--user", default=None, help="limit backup to one Spotify account"
    )
    lastfm_auth = commands.add_parser(
        "lastfm-auth", help="one-time interactive Last.fm web auth (track.love)"
    )
    lastfm_auth.add_argument("--user", default=None, help="Last.fm account username")
    likes_sync = commands.add_parser(
        "likes-sync",
        help="sync Spotify likes to Last.fm loves (preview by default)",
    )
    likes_sync.add_argument("--user", default=None, help="Spotify account (source)")
    likes_sync.add_argument(
        "--lastfm-user", default=None, help="Last.fm account (target)"
    )
    likes_sync.add_argument("--apply", action="store_true", help="perform loves")
    likes_sync.add_argument(
        "--limit", type=int, default=None, help="cap tracks (testing)"
    )
    likes_sync.add_argument(
        "--no-correct", action="store_true", help="skip canonical-title correction"
    )
    plex_inventory = commands.add_parser(
        "plex-inventory", help="sync the Plex music library into plex_items"
    )
    plex_inventory.add_argument("--user", default=None, help="Plex account username")
    plex_inventory.add_argument(
        "--section", default=None, help="limit to one library section title"
    )
    vinyl_sync = commands.add_parser(
        "vinyl-sync",
        help="detect + scrobble vinyl imports (preview by default)",
    )
    vinyl_sync.add_argument("--section", default=None, help="Plex music section title")
    vinyl_sync.add_argument("--apply", action="store_true", help="perform scrobbles")
    vinyl_sync.add_argument(
        "--limit", type=int, default=None, help="cap imports (testing)"
    )
    throwback = commands.add_parser(
        "throwback",
        help="build the Throwback Thursday playlist (preview by default)",
    )
    throwback.add_argument(
        "--user", default=None, help="Spotify account (playlist target)"
    )
    throwback.add_argument(
        "--lastfm-user", default=None, help="Last.fm account (scrobble source)"
    )
    throwback.add_argument(
        "--apply", action="store_true", help="create/replace playlist"
    )
    throwback.add_argument(
        "--since-months", type=int, default=6, help="unplayed threshold (months)"
    )
    throwback.add_argument("--limit", type=int, default=50, help="number of tracks")
    throwback.add_argument("--public", action="store_true", help="make playlist public")
    support = commands.add_parser(
        "support-artists",
        help="build the support-these-artists shortlist (preview by default)",
    )
    support.add_argument(
        "--user", default=None, help="Last.fm account (scrobble source)"
    )
    support.add_argument("--apply", action="store_true", help="store suggestions")
    support.add_argument(
        "--min-plays", type=int, default=10, help="minimum plays to qualify"
    )
    support.add_argument("--limit", type=int, default=50, help="number of suggestions")

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
            with _lock(directory, "sync"):
                with db_connect(config.database_url) as conn:
                    ids = ensure_accounts(conn, accounts)
                    for acct in selected:
                        api_key = acct.credential("api_key")
                        if not api_key:
                            print(f"skip {acct.username}: missing api_key")
                            continue
                        account_id = ids[(acct.platform, acct.username)]
                        inserted = sync_scrobbles(
                            conn,
                            account_id,
                            acct.username,
                            LastFM(api_key).fetch,
                            deep=args.deep,
                        )
                        print(f"{acct.username}: synced {inserted} new scrobbles")
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.command == "spotify-auth":
        acct = select_spotify_account(accounts, args.user)
        sp = _spotify_client(acct, directory)
        print("Open this URL in a browser and authorize Recordkeeper:\n")
        print(sp.authorize_url())
        print()
        redirect = input(
            "Paste the full redirect URL (the one your browser lands on): "
        ).strip()
        sp.complete_auth(redirect)
        print("Authorized: access token cached, refresh token stored for future runs.")
        return

    if args.command == "spotify-backup":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        selected = [a for a in accounts if a.platform == "spotify" and a.enabled]
        if args.user:
            selected = [a for a in selected if a.username == args.user]
        if not selected:
            parser.exit(1, "No enabled Spotify accounts configured\n")
        try:
            with _lock(directory, "spotify-backup"):
                with db_connect(config.database_url) as conn:
                    ids = ensure_accounts(conn, accounts)
                    for acct in selected:
                        sp = _spotify_client(acct, directory)
                        if not sp.authorized():
                            print(
                                f"{acct.username}: not authorized; run spotify-auth first"
                            )
                            continue
                        account_id = ids[(acct.platform, acct.username)]
                        stats = snapshot_account(conn, account_id, sp.client)
                        print(f"{acct.username}: {stats}")
        except SpotifyException as exc:
            if exc.http_status == 429:
                print("Spotify rate-limited; will resume on the next scheduled run")
            else:
                parser.exit(1, f"Spotify error {exc.http_status}\n")
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.command == "lastfm-auth":
        acct = select_lastfm_account(accounts, args.user)
        url = get_auth_url(acct)
        print("Open this URL in a browser and authorize Recordkeeper:\n")
        print(url)
        print()
        input("Press Enter once you have approved the request: ")
        token = parse_qs(urlparse(url).query)["token"][0]
        session_key, username = complete_session(acct, token)
        save_session_key(acct.username, session_key, str(directory))
        print(f"Authenticated as {username}; session key cached for {acct.username}.")
        return

    if args.command == "likes-sync":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        spotify_acct = select_spotify_account(accounts, args.user)
        lastfm_acct = select_lastfm_account(accounts, args.lastfm_user)
        try:
            network = build_network(lastfm_acct, str(directory))
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")

        def love(artist, title):
            pylast.Track(artist, title, network).love()

        def correct(artist, title):
            return pylast.Track(artist, title, network).get_correction()

        try:
            with _lock(directory, "likes"):
                with db_connect(config.database_url) as conn:
                    ids = ensure_accounts(conn, accounts)
                    spotify_id = ids[(spotify_acct.platform, spotify_acct.username)]
                    lastfm_id = ids[(lastfm_acct.platform, lastfm_acct.username)]
                    result = sync_loves(
                        conn,
                        spotify_id,
                        lastfm_id,
                        love=love,
                        correct=None if args.no_correct else correct,
                        dry_run=not args.apply,
                        limit=args.limit,
                    )
                    mode = "preview" if not args.apply else "applied"
                    print(
                        f"{spotify_acct.username} -> {lastfm_acct.username} ({mode}): {result}"
                    )
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.command == "plex-inventory":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        acct = select_plex_account(accounts, args.user)
        base_url = acct.credential("base_url")
        token = acct.credential("token")
        if not base_url or not token:
            parser.exit(1, f"No base_url/token for Plex account {acct.username}\n")
        try:
            plex = plex_connect(base_url, token)
            with db_connect(config.database_url) as conn:
                stats = sync_inventory(conn, plex, section_title=args.section)
                print(f"{acct.username}: {stats}")
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.command == "vinyl-sync":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        plex_acct = select_plex_account(accounts, None)
        lastfm_acct = select_lastfm_account(accounts, None)
        try:
            plex = plex_connect(
                plex_acct.credential("base_url"), plex_acct.credential("token")
            )
            network = build_network(lastfm_acct, str(directory))
            with db_connect(config.database_url) as conn:
                ids = ensure_accounts(conn, accounts)
                lastfm_id = ids[(lastfm_acct.platform, lastfm_acct.username)]
                detected = detect_imports(conn, plex, section_title=args.section)
                print(f"detected: {detected}")
                result = scrobble_imports(
                    conn, lastfm_id, network, dry_run=not args.apply, limit=args.limit
                )
                mode = "preview" if not args.apply else "applied"
                print(f"({mode}): {result}")
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.command == "throwback":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        acct = select_spotify_account(accounts, args.user)
        lastfm_acct = select_lastfm_account(accounts, args.lastfm_user)
        try:
            with _lock(directory, "throwback"):
                with db_connect(config.database_url) as conn:
                    sp = _spotify_client(acct, directory)
                    if not sp.authorized():
                        parser.exit(1, "not authorized; run spotify-auth first\n")
                    ids = ensure_accounts(conn, accounts)
                    account_id = ids[(lastfm_acct.platform, lastfm_acct.username)]
                    result = sync_throwback(
                        conn,
                        account_id,
                        sp.client,
                        since_months=args.since_months,
                        limit=args.limit,
                        dry_run=not args.apply,
                        public=args.public,
                        playlist_id=load_playlist_id(directory, acct.username),
                    )
                    if args.apply and result["playlist_id"]:
                        save_playlist_id(
                            directory, acct.username, result["playlist_id"]
                        )
                    mode = "preview" if not args.apply else "applied"
                    print(
                        f"({mode}) candidates={len(result['candidates'])} "
                        f"resolved={result['resolved']} playlist_id={result['playlist_id']}"
                    )
                    if not args.apply:
                        for r in result["candidates"]:
                            print(
                                f"  {r['artist_name']} — {r['track_name']}"
                                f"  (last {r['last_played']:%Y-%m-%d}, {r['plays']} plays)"
                            )
                    else:
                        print(
                            f"  created={result['created']} replaced={result['replaced']}"
                        )
        except SpotifyException as exc:
            if exc.http_status == 429:
                print("Spotify rate-limited; will resume on the next scheduled run")
            else:
                parser.exit(1, f"Spotify error {exc.http_status}\n")
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")
        return

    if args.command == "support-artists":
        if not config.database_url:
            parser.exit(1, "DATABASE_URL is not configured\n")
        acct = select_lastfm_account(accounts, args.user)
        try:
            with db_connect(config.database_url) as conn:
                ids = ensure_accounts(conn, accounts)
                account_id = ids[(acct.platform, acct.username)]
                result = sync_recommendations(
                    conn,
                    account_id,
                    min_plays=args.min_plays,
                    limit=args.limit,
                    dry_run=not args.apply,
                )
                mode = "preview" if not args.apply else "applied"
                print(f"({mode}) suggested={result['suggested']}")
                for r in result["rows"]:
                    print(
                        f"  {r['artist_name']}"
                        f"  ({r['plays']} plays, last {r['last_played']:%Y-%m-%d})"
                    )
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
        with _lock(directory, "backup"):
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
