"""Plex Media Server adapter: inventory the music library into `plex_items`.

Read-only. Enumerates the `artist`-type library sections (artists → albums →
tracks) and upserts them into `plex_items`, keyed on Plex's `ratingKey`. This
table is the "what is already owned" backbone for the vinyl-scrobble and
support-these-artists features.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from plexapi.server import PlexServer


def connect(base_url: str, token: str) -> PlexServer:
    return PlexServer(base_url, token, timeout=30)


def _added_at(obj) -> datetime | None:
    dt = getattr(obj, "addedAt", None)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _raw(obj) -> dict:
    return {
        "year": getattr(obj, "year", None),
        "index": getattr(obj, "index", None),
        "duration": getattr(obj, "duration", None),
    }


def _upsert(
    conn,
    entity_type,
    title,
    artist_name,
    album_name,
    rating_key,
    guid,
    library_section,
    added_at,
    raw,
):
    conn.execute(
        """
        INSERT INTO plex_items
            (entity_type, title, artist_name, album_name, plex_rating_key,
             plex_guid, mbid, library_section, added_at, raw)
        VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s::jsonb)
        ON CONFLICT (plex_rating_key) DO UPDATE
          SET title = EXCLUDED.title,
              artist_name = EXCLUDED.artist_name,
              album_name = EXCLUDED.album_name,
              plex_guid = EXCLUDED.plex_guid,
              library_section = EXCLUDED.library_section,
              added_at = EXCLUDED.added_at,
              raw = EXCLUDED.raw
        """,
        (
            entity_type,
            title,
            artist_name,
            album_name,
            rating_key,
            guid,
            library_section,
            added_at,
            json.dumps(raw),
        ),
    )


def sync_inventory(conn, plex, section_title: str | None = None) -> dict:
    """Upsert every artist/album/track in the Plex music sections."""
    stats = {"artists": 0, "albums": 0, "tracks": 0}
    for section in plex.library.sections():
        if section.type != "artist":
            continue
        if section_title and section.title != section_title:
            continue
        for artist in section.all():
            _upsert(
                conn,
                "artist",
                artist.title,
                artist.title,
                None,
                str(artist.ratingKey),
                artist.guid,
                section.title,
                _added_at(artist),
                _raw(artist),
            )
            stats["artists"] += 1
            for album in artist.albums():
                _upsert(
                    conn,
                    "album",
                    album.title,
                    artist.title,
                    None,
                    str(album.ratingKey),
                    album.guid,
                    section.title,
                    _added_at(album),
                    _raw(album),
                )
                stats["albums"] += 1
                for track in album.tracks():
                    _upsert(
                        conn,
                        "track",
                        track.title,
                        getattr(track, "grandparentTitle", artist.title),
                        album.title,
                        str(track.ratingKey),
                        track.guid,
                        section.title,
                        _added_at(track),
                        _raw(track),
                    )
                    stats["tracks"] += 1
    conn.commit()
    return stats
