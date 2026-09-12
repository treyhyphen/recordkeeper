"""Vinyl-import → Last.fm scrobble automation.

The vinyls directory is the Plex "Music" library (`/mnt/Media/Music/Vinyl Rips`).
`recordkeeper vinyl-sync` detects albums newly added to that section, estimates a
listening session (ending near the album's Plex `added_at` — the user listens to
confirm a rip before importing it), and scrobbles each track with explicitly
estimated timestamps. Albums older than Last.fm's 14-day scrobble window are
baselined (`skipped`), never silently given a wrong timestamp.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

TASK = "vinyl_scrobble"
SCRUBBLE_WINDOW = timedelta(days=14)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def estimate_times(added_at: datetime, durations_ms: list[int]) -> list[datetime]:
    """Estimate each track's start time so the session ends at `added_at`.

    Track *i* starts at `added_at - (total_duration - cumulative_before_i)`,
    i.e. the last track finishes at the import time and every prior track is
    spaced by its duration working backwards.
    """
    total = sum(d for d in durations_ms)
    cumulative = 0
    out = []
    for d in durations_ms:
        out.append(added_at - timedelta(milliseconds=(total - cumulative)))
        cumulative += d
    return out


def detect_imports(
    conn, plex, section_title: str = "Music", now: datetime | None = None
) -> dict:
    """Stage vinyl albums newly present in the Plex music section.

    New albums are recorded in `vinyl_imports` (keyed on Plex ratingKey) with
    per-track `estimated_played_at`. Albums within Last.fm's 14-day scrobble
    window get status `new`; older albums are baselined as `skipped`.
    """
    now = _aware(now or datetime.now(timezone.utc))
    stats = {"new": 0, "baselined": 0}
    for section in plex.library.sections():
        if section.type != "artist" or (
            section_title and section.title != section_title
        ):
            continue
        for artist in section.all():
            for album in artist.albums():
                rating_key = str(album.ratingKey)
                exists = conn.execute(
                    "SELECT 1 FROM vinyl_imports WHERE plex_rating_key = %s",
                    (rating_key,),
                ).fetchone()
                if exists:
                    continue
                added = _aware(album.addedAt)
                tracks = album.tracks()
                durations = [int(t.duration or 0) for t in tracks]
                starts = estimate_times(added, durations)
                in_window = (now - added) <= SCRUBBLE_WINDOW and added <= now
                status = "new" if in_window else "skipped"
                locations = getattr(album, "locations", []) or []
                directory = locations[0] if locations else None
                import_row = conn.execute(
                    """
                    INSERT INTO vinyl_imports
                        (plex_rating_key, directory_path, detected_at, status,
                         track_count, raw)
                    VALUES (%s, %s, now(), %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        rating_key,
                        directory,
                        status,
                        len(tracks),
                        json.dumps(
                            {
                                "added_at": added.isoformat(),
                                "album": album.title,
                                "artist": artist.title,
                            }
                        ),
                    ),
                ).fetchone()
                for i, t in enumerate(tracks):
                    conn.execute(
                        """
                        INSERT INTO vinyl_import_tracks
                            (import_id, position, title, duration_ms,
                             estimated_played_at, scrobbled)
                        VALUES (%s, %s, %s, %s, %s, false)
                        """,
                        (import_row["id"], i, t.title, t.duration, starts[i]),
                    )
                stats["new" if status == "new" else "baselined"] += 1
    conn.commit()
    return stats


def scrobble_imports(
    conn,
    lastfm_account_id: int,
    network,
    dry_run: bool = True,
    limit: int | None = None,
    delay: float = 0.4,
) -> dict:
    """Scrobble the tracks of `new` vinyl imports, marking them done.

    Artist/album names are read back from `plex_items` (keyed on the same
    ratingKey). Each scrobble is committed per track so an interruption never
    loses completed work, and `sync_ledger` records the write for idempotency.
    """
    import time

    rows = conn.execute(
        "SELECT id, plex_rating_key FROM vinyl_imports WHERE status = 'new' "
        "ORDER BY id LIMIT %s",
        (limit,),
    ).fetchall()
    stats = {"imports": 0, "tracks": 0, "failed": 0}
    for row in rows:
        album = conn.execute(
            "SELECT title, artist_name FROM plex_items "
            "WHERE plex_rating_key = %s AND entity_type = 'album'",
            (row["plex_rating_key"],),
        ).fetchone()
        if album is None:
            continue
        tracks = conn.execute(
            "SELECT id, title, duration_ms, estimated_played_at, scrobbled "
            "FROM vinyl_import_tracks WHERE import_id = %s ORDER BY position",
            (row["id"],),
        ).fetchall()
        for t in tracks:
            if t["scrobbled"]:
                continue
            artist = album["artist_name"]
            title = t["title"]
            ts = int(t["estimated_played_at"].timestamp())
            duration = int((t["duration_ms"] or 0) / 1000)
            if dry_run:
                print(
                    f"  {artist} — {title}  @ {t['estimated_played_at']:%Y-%m-%d %H:%M}"
                )
                stats["tracks"] += 1
                continue
            try:
                network.scrobble(
                    artist, title, ts, album=album["title"], duration=duration
                )
                conn.execute(
                    "UPDATE vinyl_import_tracks SET scrobbled = true WHERE id = %s",
                    (t["id"],),
                )
                conn.execute(
                    """
                    INSERT INTO sync_ledger
                        (account_id, task_type, source_key, target_key, status, synced_at)
                    VALUES (%s, %s, %s, %s, 'synced', now())
                    ON CONFLICT (account_id, task_type, source_key) DO UPDATE
                      SET status = 'synced', synced_at = now()
                    """,
                    (
                        lastfm_account_id,
                        TASK,
                        f"plex:{row['plex_rating_key']}:{t['id']}",
                        f"{artist} - {title}",
                    ),
                )
                stats["tracks"] += 1
            except Exception as exc:  # noqa: BLE001 - record and continue
                stats["failed"] += 1
                print(f"  FAILED {artist} — {title}: {exc}")
            conn.commit()
            time.sleep(delay)
        if not dry_run:
            conn.execute(
                "UPDATE vinyl_imports SET status = 'scrobbled' WHERE id = %s",
                (row["id"],),
            )
            conn.commit()
        stats["imports"] += 1
    return stats
