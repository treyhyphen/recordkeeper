"""Tests for the Plex inventory sync."""

from recordkeeper.plex import _added_at, sync_inventory


class _T:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Res:
    def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.writes = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.writes.append((sql, params))
        return _Res()

    def commit(self):
        self.commits += 1


class _Plex:
    def __init__(self, sections):
        self._sections = sections

    @property
    def library(self):
        return self

    def sections(self):
        return self._sections


def _music_section():
    artist = _T(
        title="King Buffalo", ratingKey=1, guid="artist-1", addedAt=None, year=None
    )
    album = _T(title="Acheron", ratingKey=2, guid="album-1", addedAt=None, year=2021)
    track = _T(
        title="Zephyr",
        ratingKey=3,
        guid="track-1",
        addedAt=None,
        year=2021,
        index=1,
        duration=600000,
        grandparentTitle="King Buffalo",
    )
    artist.albums = lambda: [album]
    album.tracks = lambda: [track]
    section = _T(type="artist", title="Music")
    section.all = lambda: [artist]
    return section


def test_sync_inventory_skips_non_artist_sections():
    movie = _T(type="movie", title="Movies")
    movie.all = lambda: []
    conn = _Conn()
    stats = sync_inventory(conn, _Plex([movie]))
    assert stats == {"artists": 0, "albums": 0, "tracks": 0}
    assert conn.writes == []


def test_sync_inventory_upserts_artists_albums_tracks():
    conn = _Conn()
    stats = sync_inventory(conn, _Plex([_music_section()]))
    assert stats == {"artists": 1, "albums": 1, "tracks": 1}
    entity_types = [w[1][0] for w in conn.writes]
    assert entity_types == ["artist", "album", "track"]
    # track row carries artist + album names
    track_params = conn.writes[2][1]
    assert track_params[2] == "King Buffalo"  # artist_name
    assert track_params[3] == "Acheron"  # album_name
    assert conn.commits == 1


def test_added_at_naive_is_made_utc():
    from datetime import datetime

    dt = datetime(2026, 9, 1, 12, 0, 0)  # naive
    out = _added_at(_T(addedAt=dt))
    assert out.tzinfo is not None
    assert _added_at(_T(addedAt=None)) is None
