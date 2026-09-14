"""Throwback Thursday: a weekly Spotify playlist of long-unplayed tracks.

Draws from the Last.fm scrobble history already in PostgreSQL: tracks the
account hasn't played in `since_months` (default 6) and that have at least
`min_plays` total plays (so they're real favourites, not one-off flukes). Each
candidate is resolved to a Spotify URI via search; tracks not found on Spotify
are skipped (the match is best-effort — take the top search result). The
playlist is created on first run and its contents replaced each week; its id is
persisted in a gitignored state file (avoiding `current_user_playlists`, which
the snapshot job can exhaust).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from spotipy.exceptions import SpotifyException

PLAYLIST_NAME = "Throwback Thursday"
DESCRIPTION = "50 tracks you haven't played in 6+ months — refreshed every Thursday."


def playlist_state_path(data_dir, username: str) -> Path:
    return Path(data_dir) / f"throwback-playlist-{username}.json"


def load_playlist_id(data_dir, username: str) -> str | None:
    path = playlist_state_path(data_dir, username)
    if path.exists():
        try:
            return json.loads(path.read_text()).get("playlist_id")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_playlist_id(data_dir, username: str, playlist_id: str) -> None:
    playlist_state_path(data_dir, username).write_text(
        json.dumps({"playlist_id": playlist_id})
    )


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


def resolve_uris(client, rows, delay: float = 0.25, limit: int = 50):
    """Map candidate (artist, track) pairs to Spotify track URIs via search."""
    uris = []
    for row in rows:
        query = f"track:{row['track_name']} artist:{row['artist_name']}"
        results = client.search(q=query, type="track", limit=1)
        items = (results.get("tracks") or {}).get("items") or []
        if items:
            uri = items[0].get("uri")
            if uri and uri.startswith("spotify:track:") and uri not in uris:
                uris.append(uri)
                if len(uris) == limit:
                    break
        if delay:
            time.sleep(delay)
    return uris


def _create_playlist(client, public: bool) -> str:
    # Feb-2026 migration retired POST /users/{id}/playlists (which Spotipy's
    # user_playlist_create still calls) in favour of POST /me/playlists.
    playlist = client._post(
        "me/playlists",
        payload={"name": PLAYLIST_NAME, "public": public, "description": DESCRIPTION},
    )
    return playlist["id"]


def _replace_items(client, playlist_id: str, uris: list[str]) -> None:
    # Feb-2026 migration renamed /playlists/{id}/tracks to /playlists/{id}/items.
    client._put(f"playlists/{playlist_id}/items", payload={"uris": uris})


def sync_throwback(
    conn,
    account_id: int,
    client,
    since_months: int = 6,
    limit: int = 50,
    min_plays: int = 3,
    dry_run: bool = True,
    public: bool = False,
    playlist_id: str | None = None,
) -> dict:
    """Select candidates, resolve to URIs, and create/replace the playlist."""
    if not 1 <= limit <= 100:
        raise ValueError("Throwback size must be between 1 and 100")
    # One randomized pool without repeated sampling; stop searches at the target.
    rows = select_candidates(conn, account_id, since_months, min_plays, None)
    uris = resolve_uris(client, rows, limit=limit)
    if not dry_run and len(uris) < limit:
        raise RuntimeError(
            f"Only {len(uris)}/{limit} unique tracks resolved; playlist unchanged"
        )
    created = False
    replaced = 0
    if not dry_run:
        if playlist_id is None:
            playlist_id = _create_playlist(client, public)
            created = True
        try:
            _replace_items(client, playlist_id, uris)
        except SpotifyException as exc:
            if exc.http_status != 404:
                raise
            # Playlist was deleted since we last ran — recreate it.
            playlist_id = _create_playlist(client, public)
            created = True
            _replace_items(client, playlist_id, uris)
        replaced = len(uris)
    return {
        "candidates": rows,
        "resolved": len(uris),
        "playlist_id": playlist_id,
        "created": created,
        "replaced": replaced,
    }
