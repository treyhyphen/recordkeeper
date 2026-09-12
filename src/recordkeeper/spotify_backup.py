"""Snapshot Spotify playlists and saved (liked) tracks into PostgreSQL.

Playlists are versioned: each backup stores the current `snapshot_id` on the
`playlists` row and only creates a new `playlist_snapshots` capture (with its
ordered items) when that id changes, so repeat runs are cheap and history is
preserved. Saved tracks are upserted per account.
"""

from __future__ import annotations

import json
import time

from spotipy.exceptions import SpotifyException


def paginate(client, first: dict, delay: float = 0.0):
    """Yield each page, following Spotify's `next` cursor, throttled by `delay`."""
    results = first
    while results:
        yield results
        if not results.get("next"):
            return
        if delay:
            time.sleep(delay)
        results = client.next(results)


def extract_track(track: dict | None) -> dict | None:
    """Normalize a Spotify track object to the fields we store (None if deleted)."""
    if not track:
        return None
    return {
        "uri": track.get("uri"),
        "name": track.get("name"),
        "artists": [a.get("name") for a in track.get("artists", []) if a.get("name")],
        "album": (track.get("album") or {}).get("name"),
        "duration_ms": track.get("duration_ms"),
    }


def item_track(item: dict) -> dict | None:
    """Extract the embedded track object from a playlist/saved-track item.

    `playlist_items` returns the track under `item` (when `additional_types` is
    set) or `track`; `current_user_saved_tracks` uses `track`.
    """
    return item.get("track") or item.get("item")


def _upsert_playlist(conn, account_id: int, item: dict) -> tuple[int, bool]:
    """Insert/update a playlist row; return (playlist_id, changed)."""
    existing = conn.execute(
        "SELECT id, snapshot_id FROM playlists "
        "WHERE account_id = %s AND provider = 'spotify' AND provider_playlist_id = %s",
        (account_id, item["id"]),
    ).fetchone()
    owner = item.get("owner") or {}
    snapshot_id = item.get("snapshot_id")
    if existing is None:
        row = conn.execute(
            """
            INSERT INTO playlists (account_id, provider, provider_playlist_id, name,
                description, owner_id, owner_name, is_public, is_collaborative,
                snapshot_id, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                account_id,
                "spotify",
                item["id"],
                item.get("name"),
                item.get("description"),
                owner.get("id"),
                owner.get("display_name"),
                item.get("public"),
                item.get("collaborative"),
                snapshot_id,
                json.dumps(item),
            ),
        ).fetchone()
        return row["id"], True
    conn.execute(
        """
        UPDATE playlists SET name = %s, description = %s, owner_id = %s,
            owner_name = %s, is_public = %s, is_collaborative = %s,
            snapshot_id = %s, raw = %s::jsonb, updated_at = now()
        WHERE id = %s
        """,
        (
            item.get("name"),
            item.get("description"),
            owner.get("id"),
            owner.get("display_name"),
            item.get("public"),
            item.get("collaborative"),
            snapshot_id,
            json.dumps(item),
            existing["id"],
        ),
    )
    return existing["id"], existing["snapshot_id"] != snapshot_id


def snapshot_playlists(conn, account_id: int, client, delay: float = 0.2) -> dict:
    """Versioned backup of every playlist the account can see.

    Playlist metadata is stored for every visible playlist, but item contents
    are only fetched for playlists the account owns or collaborates on; Spotify
    returns 403 for followed playlists owned by others (a 2026 policy), which
    are skipped rather than aborting the run. `delay` throttles requests to stay
    within Spotify's rate limits.
    """
    stats = {"playlists": 0, "snapshots": 0, "items": 0, "skipped_items": 0}
    first = client.current_user_playlists(limit=50, offset=0)
    for page in paginate(client, first, delay=delay):
        for item in page["items"]:
            playlist_id, changed = _upsert_playlist(conn, account_id, item)
            stats["playlists"] += 1
            if not changed:
                continue
            try:
                items = [
                    it
                    for p in paginate(
                        client,
                        client.playlist_items(item["id"], limit=100, offset=0),
                        delay=delay,
                    )
                    for it in p["items"]
                ]
            except SpotifyException as exc:
                if exc.http_status in (403, 404):
                    stats["skipped_items"] += 1
                    continue
                raise
            snapshot = conn.execute(
                """
                INSERT INTO playlist_snapshots (playlist_id, snapshot_id, track_count, raw)
                VALUES (%s, %s, %s, %s::jsonb) RETURNING id
                """,
                (
                    playlist_id,
                    item.get("snapshot_id"),
                    len(items),
                    json.dumps({"snapshot_id": item.get("snapshot_id")}),
                ),
            ).fetchone()
            stats["snapshots"] += 1
            for position, it in enumerate(items):
                track = extract_track(item_track(it))
                if track is None:
                    continue
                conn.execute(
                    """
                    INSERT INTO playlist_snapshot_items
                        (snapshot_id, position, provider_uri, track_name,
                         artist_names, album_name, added_at, raw)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        snapshot["id"],
                        position,
                        track["uri"],
                        track["name"],
                        track["artists"],
                        track["album"],
                        it.get("added_at"),
                        json.dumps(it),
                    ),
                )
                stats["items"] += 1
    return stats


def snapshot_saved_tracks(conn, account_id: int, client, delay: float = 0.2) -> int:
    """Upsert the account's saved (liked) tracks."""
    count = 0
    first = client.current_user_saved_tracks(limit=50, offset=0)
    for page in paginate(client, first, delay=delay):
        for item in page["items"]:
            track = extract_track(item_track(item))
            if track is None:
                continue
            conn.execute(
                """
                INSERT INTO saved_tracks (account_id, provider_uri, track_name,
                    artist_names, album_name, saved_at, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (account_id, provider_uri) DO UPDATE
                    SET track_name = EXCLUDED.track_name,
                        artist_names = EXCLUDED.artist_names,
                        album_name = EXCLUDED.album_name,
                        saved_at = EXCLUDED.saved_at,
                        raw = EXCLUDED.raw
                """,
                (
                    account_id,
                    track["uri"],
                    track["name"],
                    track["artists"],
                    track["album"],
                    item.get("added_at"),
                    json.dumps(item),
                ),
            )
            count += 1
    return count


def snapshot_account(conn, account_id: int, client) -> dict:
    """Snapshot playlists + saved tracks for one account, then commit."""
    stats = snapshot_playlists(conn, account_id, client)
    stats["saved"] = snapshot_saved_tracks(conn, account_id, client)
    conn.commit()
    return stats
