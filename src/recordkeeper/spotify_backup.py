"""Snapshot Spotify playlists and saved (liked) tracks into PostgreSQL.

Playlists are versioned: each backup stores the current `snapshot_id` on the
`playlists` row and only creates a new `playlist_snapshots` capture (with its
ordered items) when that id changes, so repeat runs are cheap and history is
preserved. Saved tracks are upserted per account.
"""

from __future__ import annotations

import json


def paginate(client, first: dict):
    """Yield each page, following Spotify's `next` cursor."""
    results = first
    while results:
        yield results
        results = client.next(results) if results.get("next") else None


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


def snapshot_playlists(conn, account_id: int, client) -> dict:
    """Versioned backup of every playlist the account can see."""
    stats = {"playlists": 0, "snapshots": 0, "items": 0}
    first = client.current_user_playlists(limit=50, offset=0)
    for page in paginate(client, first):
        for item in page["items"]:
            playlist_id, changed = _upsert_playlist(conn, account_id, item)
            stats["playlists"] += 1
            if not changed:
                continue
            items = [
                it
                for p in paginate(
                    client, client.playlist_items(item["id"], limit=100, offset=0)
                )
                for it in p["items"]
            ]
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
                track = extract_track(it.get("track"))
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


def snapshot_saved_tracks(conn, account_id: int, client) -> int:
    """Upsert the account's saved (liked) tracks."""
    count = 0
    first = client.current_user_saved_tracks(limit=50, offset=0)
    for page in paginate(client, first):
        for item in page["items"]:
            track = extract_track(item.get("track"))
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
