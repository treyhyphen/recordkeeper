"""Incremental Last.fm scrobble sync into PostgreSQL.

Unlike the raw-page archive (backup.py), this upserts typed rows into the
`scrobbles` table so listening history is queryable and stays current. Fetching
is incremental: it pages newest-first and stops at the account's high-water mark
(the latest `played_at` already stored), so a normal run only touches the few
pages covering new listens. It is idempotent (unique key + ON CONFLICT DO
NOTHING) and preserves legitimate repeat listens.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from .backup import tracks


def _uuid_or_none(value: str) -> uuid.UUID | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def extract_play(item: dict) -> dict:
    """Normalize one raw recenttrack item into a play dict."""
    played = datetime.fromtimestamp(int(item["date"]["uts"]), tz=timezone.utc)
    return {
        "played_at": played,
        "artist_name": item["artist"]["#text"],
        "track_name": item["name"],
        "album_name": item.get("album", {}).get("#text") or None,
        "mbid": _uuid_or_none(item.get("mbid", "")),
        "loved": item.get("loved") == "1",
        "raw": item,
    }


def fetch_new_plays(fetch, username: str, watermark, delay: float = 0.3):
    """Yield normalized plays newer than `watermark`, newest-first.

    `watermark` is an aware datetime or None (None forces a full-history pull).
    Iteration stops at the first play at or before the watermark, which is
    correct for the chronological, newest-first ordering Last.fm returns.
    """
    page = 1
    while True:
        payload = fetch(username, page)
        meta = payload["recenttracks"]["@attr"]
        pages = max(1, int(meta["totalPages"]))
        items = tracks(payload)
        if not items:
            return
        for item in items:
            play = extract_play(item)
            if watermark is not None and play["played_at"] <= watermark:
                return
            yield play
        if page >= pages:
            return
        page += 1
        if delay:
            time.sleep(delay)


def sync_scrobbles(
    conn,
    account_id: int,
    username: str,
    fetch,
    delay: float = 0.3,
    deep: bool = False,
) -> int:
    """Incrementally sync an account's scrobbles into Postgres (idempotent).

    `deep=True` ignores the high-water mark and re-fetches the entire history so
    backdated scrobbles (e.g. a manual scrobble with an old timestamp) are caught
    on the next periodic deep pass; existing rows are skipped via ON CONFLICT.
    """
    watermark = None
    if not deep:
        watermark = conn.execute(
            "SELECT max(played_at) AS w FROM scrobbles WHERE account_id = %s",
            (account_id,),
        ).fetchone()["w"]
    inserted = 0
    for play in fetch_new_plays(fetch, username, watermark, delay=delay):
        conn.execute(
            """
            INSERT INTO scrobbles
                (account_id, played_at, source, artist_name, track_name,
                 album_name, mbid, loved, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                account_id,
                play["played_at"],
                "lastfm",
                play["artist_name"],
                play["track_name"],
                play["album_name"],
                play["mbid"],
                play["loved"],
                json.dumps(play["raw"]),
            ),
        )
        inserted += 1
    conn.commit()
    return inserted
