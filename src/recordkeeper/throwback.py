"""Throwback Thursday: a weekly Spotify playlist of long-unplayed tracks.

Draws from the Last.fm scrobble history already in PostgreSQL: tracks the
account hasn't played in `since_months` (default 6) and that have at least
`min_plays` total plays (so they're real favourites, not one-off flukes). Each
candidate is resolved to a Spotify URI via search; tracks not found on Spotify
are skipped (the match is best-effort — take the top search result). The
playlist is created on first run and its contents replaced each week.
"""

from __future__ import annotations

import time

from spotipy.exceptions import SpotifyException

PLAYLIST_NAME = "Throwback Thursday"
DESCRIPTION = "50 tracks you haven't played in 6+ months — refreshed every Thursday."


def select_candidates(conn, account_id, since_months=6, min_plays=3, limit=50):
    """Return candidate rows: artist_name, track_name, last_played, plays."""
    return conn.execute(
        """
        SELECT artist_name, track_name, MAX(played_at) AS last_played,
               COUNT(*) AS plays
        FROM scrobbles
        WHERE account_id = %s
        GROUP BY artist_name, track_name
        HAVING MAX(played_at) < now() - (%s * interval '1 month')
           AND COUNT(*) >= %s
        ORDER BY random()
        LIMIT %s
        """,
        (account_id, since_months, min_plays, limit),
    ).fetchall()


def resolve_uris(client, rows, delay: float = 0.25):
    """Map candidate (artist, track) pairs to Spotify track URIs via search."""
    uris = []
    for row in rows:
        query = f"track:{row['track_name']} artist:{row['artist_name']}"
        try:
            results = client.search(q=query, type="track", limit=1)
        except SpotifyException:
            continue  # rate limit or transient — skip, don't abort the run
        items = (results.get("tracks") or {}).get("items") or []
        if items:
            uris.append(items[0]["uri"])
        if delay:
            time.sleep(delay)
    return uris


def find_playlist(client, name: str = PLAYLIST_NAME):
    """Return the playlist id for `name`, or None if it doesn't exist yet."""
    results = client.current_user_playlists(limit=50, offset=0)
    while results:
        for item in results.get("items") or []:
            if item.get("name") == name:
                return item["id"]
        if not results.get("next"):
            break
        results = client.next(results)
    return None


def sync_throwback(
    conn,
    account_id: int,
    client,
    since_months: int = 6,
    limit: int = 50,
    min_plays: int = 3,
    dry_run: bool = True,
    public: bool = False,
) -> dict:
    """Select candidates, resolve to URIs, and create/replace the playlist."""
    rows = select_candidates(conn, account_id, since_months, min_plays, limit)
    uris = resolve_uris(client, rows)
    playlist_id = find_playlist(client)
    created = False
    replaced = 0
    if not dry_run:
        if playlist_id is None:
            user_id = client.me()["id"]
            playlist = client.user_playlist_create(
                user_id, PLAYLIST_NAME, public=public, description=DESCRIPTION
            )
            playlist_id = playlist["id"]
            created = True
        client.playlist_replace_items(playlist_id, uris)
        replaced = len(uris)
    return {
        "candidates": rows,
        "resolved": len(uris),
        "playlist_id": playlist_id,
        "created": created,
        "replaced": replaced,
    }
