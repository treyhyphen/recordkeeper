"""Spotify likes → Last.fm loves sync (one-way, additive).

`recordkeeper likes-sync` resolves each Spotify saved track to its Last.fm
canonical track and loves it there, recording the write in `sync_ledger` for
idempotency. Preview mode (default) reports the resolution without writing;
`--apply` performs the loves. Unliking a track on Spotify never unloves it on
Last.fm — the sync is strictly additive.

The authenticated Last.fm write path uses pylast (already a dependency). A
long-lived session key is cached in `data/lastfm-session-<username>.json`
(gitignored, mode 0600) via the one-time `lastfm-auth` flow.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

import pylast

TASK = "likes_to_loves"


def session_path(username: str, data_dir: str = "data") -> Path:
    """Per-account Last.fm session-key cache (gitignored)."""
    path = Path(data_dir) / f"lastfm-session-{username}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def load_session_key(username: str, data_dir: str = "data") -> str | None:
    path = session_path(username, data_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text()).get("session_key")
    except (json.JSONDecodeError, OSError):
        return None


def save_session_key(username: str, session_key: str, data_dir: str = "data") -> None:
    path = session_path(username, data_dir)
    path.write_text(json.dumps({"session_key": session_key}))
    path.chmod(0o600)


def build_network(acct, data_dir: str = "data") -> pylast.LastFMNetwork:
    """Build an authenticated pylast network for one Last.fm account."""
    api_key = acct.credential("api_key")
    api_secret = acct.credential("api_secret")
    if not (api_key and api_secret):
        raise RuntimeError(
            f"Last.fm account {acct.username} missing api_key/api_secret"
        )
    session_key = load_session_key(acct.username, data_dir)
    if not session_key:
        raise RuntimeError(
            f"Last.fm account {acct.username} not authenticated; run lastfm-auth first"
        )
    return pylast.LastFMNetwork(
        api_key=api_key,
        api_secret=api_secret,
        session_key=session_key,
        username=acct.username,
    )


def get_auth_url(acct) -> str:
    """Return the web-auth URL the user must open to authorize Recordkeeper."""
    api_key = acct.credential("api_key")
    api_secret = acct.credential("api_secret")
    if not (api_key and api_secret):
        raise RuntimeError(
            f"Last.fm account {acct.username} missing api_key/api_secret"
        )
    network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
    return pylast.SessionKeyGenerator(network).get_web_auth_url()


def complete_session(acct, token: str) -> tuple[str, str]:
    """Exchange an authorized token for a (session_key, username)."""
    api_key = acct.credential("api_key")
    api_secret = acct.credential("api_secret")
    network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
    sg = pylast.SessionKeyGenerator(network)
    return sg.get_web_auth_session_key_username("", token=token)


def normalize(value: str) -> str:
    """Case/punctuation/diacritic-insensitive key for fuzzy track matching."""
    s = unicodedata.normalize("NFKD", value)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"\b(feat\.?|ft\.?|featuring)\b.*$", " ", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _primary_artist(names) -> str:
    return names[0] if names else ""


def sync_loves(
    conn,
    spotify_account_id: int,
    lastfm_account_id: int,
    love,
    correct=None,
    dry_run: bool = True,
    limit: int | None = None,
    delay: float = 0.5,
) -> dict:
    """Sync Spotify saved tracks to Last.fm loves, reporting a summary.

    `love(artist, title)` performs the write; `correct(artist, title)` returns a
    canonical title (or None) used to preview the resolution. `dry_run` reports
    without writing; otherwise each success/failure is recorded in `sync_ledger`
    so reruns only touch new or previously-failed tracks.
    """
    rows = conn.execute(
        """
        SELECT s.provider_uri, s.track_name, s.artist_names
        FROM saved_tracks s
        WHERE s.account_id = %s
          AND NOT EXISTS (
              SELECT 1 FROM sync_ledger l
              WHERE l.account_id = %s
                AND l.task_type = %s
                AND l.source_key = s.provider_uri
                AND l.status IN ('synced', 'skipped')
          )
        ORDER BY s.id
        LIMIT %s
        """,
        (spotify_account_id, lastfm_account_id, TASK, limit),
    ).fetchall()

    loved = corrected = failed = 0
    for row in rows:
        artist = _primary_artist(row["artist_names"])
        title = row["track_name"]
        source = row["provider_uri"]
        resolved = title
        if correct is not None:
            try:
                corr = correct(artist, title)
            except pylast.WSError:
                corr = None
            if corr and normalize(corr) != normalize(title):
                resolved = corr
                corrected += 1
        target = f"{artist} - {resolved}"
        if dry_run:
            flag = "  (corrected)" if resolved != title else ""
            print(f"  {artist} — {resolved}{flag}")
            loved += 1
            continue
        try:
            love(artist, resolved)
            conn.execute(
                """
                INSERT INTO sync_ledger
                    (account_id, task_type, source_key, target_key, status, synced_at)
                VALUES (%s, %s, %s, %s, 'synced', now())
                ON CONFLICT (account_id, task_type, source_key) DO UPDATE
                  SET status = 'synced', target_key = EXCLUDED.target_key,
                      synced_at = now()
                """,
                (lastfm_account_id, TASK, source, target),
            )
            loved += 1
        except pylast.WSError as exc:
            status = "skipped" if getattr(exc, "status", None) == 6 else "failed"
            failed += 1
            conn.execute(
                """
                INSERT INTO sync_ledger
                    (account_id, task_type, source_key, target_key, status, attempts,
                     last_attempt_at, last_error)
                VALUES (%s, %s, %s, %s, %s, 1, now(), %s)
                ON CONFLICT (account_id, task_type, source_key) DO UPDATE
                  SET status = EXCLUDED.status, attempts = sync_ledger.attempts + 1,
                      last_attempt_at = now(), last_error = EXCLUDED.last_error
                """,
                (lastfm_account_id, TASK, source, target, status, str(exc)[:500]),
            )
            print(f"  FAILED {artist} — {resolved}: {exc}")
        except Exception as exc:  # noqa: BLE001 - record and continue
            failed += 1
            conn.execute(
                """
                INSERT INTO sync_ledger
                    (account_id, task_type, source_key, target_key, status, attempts,
                     last_attempt_at, last_error)
                VALUES (%s, %s, %s, %s, 'failed', 1, now(), %s)
                ON CONFLICT (account_id, task_type, source_key) DO UPDATE
                  SET status = 'failed', attempts = sync_ledger.attempts + 1,
                      last_attempt_at = now(), last_error = EXCLUDED.last_error
                """,
                (lastfm_account_id, TASK, source, target, str(exc)[:500]),
            )
            print(f"  FAILED {artist} — {resolved}: {exc}")
        if not dry_run:
            # Commit per track so a long backfill is durable and resumable:
            # an interruption rolls back only the in-flight track, never hours
            # of already-synced work.
            conn.commit()
        time.sleep(delay)

    return {"candidates": loved, "corrected": corrected, "failed": failed}
