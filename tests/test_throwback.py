"""Tests for the Throwback Thursday playlist builder."""

from recordkeeper.throwback import (
    PLAYLIST_NAME,
    find_playlist,
    resolve_uris,
    select_candidates,
    sync_throwback,
)


class _Res:
    def __init__(self, fetchall=None, fetchone=None):
        self._fetchall = fetchall
        self._fetchone = fetchone

    def fetchall(self):
        return self._fetchall or []

    def fetchone(self):
        return self._fetchone


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.last_sql = None
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        return _Res(fetchall=self.rows)


def test_select_candidates_groups_and_filters():
    conn = _Conn(
        [
            {
                "artist_name": "King Buffalo",
                "track_name": "Grifter",
                "last_played": "2026-01-01",
                "plays": 5,
            }
        ]
    )
    rows = select_candidates(conn, 7, since_months=6, min_plays=3, limit=50)
    assert len(rows) == 1
    assert "GROUP BY artist_name, track_name" in conn.last_sql
    assert "interval '1 month'" in conn.last_sql
    assert conn.last_params == (7, 6, 3, 50)


def test_resolve_uris_skips_missing():
    class _Client:
        def search(self, q=None, type=None, limit=None):
            if "Grifter" in q:
                return {"tracks": {"items": [{"uri": "spotify:track:abc"}]}}
            return {"tracks": {"items": []}}

    rows = [
        {"track_name": "Grifter", "artist_name": "King Buffalo"},
        {"track_name": "Nothing", "artist_name": "Nobody"},
    ]
    assert resolve_uris(_Client(), rows, delay=0) == ["spotify:track:abc"]


def test_find_playlist_matches_by_name():
    class _Client:
        def current_user_playlists(self, limit=50, offset=0):
            return {
                "items": [
                    {"name": "Other", "id": "plOther"},
                    {"name": PLAYLIST_NAME, "id": "plX"},
                ],
                "next": None,
            }

    assert find_playlist(_Client()) == "plX"


def test_sync_throwback_dry_run_does_not_write():
    class _Client:
        def search(self, q=None, type=None, limit=None):
            return {"tracks": {"items": [{"uri": "spotify:track:abc"}]}}

        def current_user_playlists(self, limit=50, offset=0):
            return {"items": [], "next": None}

        def playlist_replace_items(self, *a, **kw):
            raise AssertionError("should not write in dry-run")

    conn = _Conn(
        [
            {
                "artist_name": "A",
                "track_name": "B",
                "last_played": "2026-01-01",
                "plays": 5,
            }
        ]
    )
    result = sync_throwback(conn, 7, _Client(), dry_run=True)
    assert result["resolved"] == 1
    assert result["replaced"] == 0
    assert result["created"] is False


def test_sync_throwback_apply_creates_and_replaces():
    class _Client:
        def __init__(self):
            self.created_args = None
            self.replaced_args = None

        def search(self, q=None, type=None, limit=None):
            return {"tracks": {"items": [{"uri": "spotify:track:abc"}]}}

        def current_user_playlists(self, limit=50, offset=0):
            return {"items": [], "next": None}

        def me(self):
            return {"id": "u1"}

        def user_playlist_create(self, user, name, public=False, description=None):
            self.created_args = (user, name, public)
            return {"id": "pl1"}

        def playlist_replace_items(self, playlist_id, uris):
            self.replaced_args = (playlist_id, uris)

    conn = _Conn(
        [
            {
                "artist_name": "A",
                "track_name": "B",
                "last_played": "2026-01-01",
                "plays": 5,
            }
        ]
    )
    client = _Client()
    result = sync_throwback(conn, 7, client, dry_run=False)
    assert result["created"] is True
    assert result["replaced"] == 1
    assert result["playlist_id"] == "pl1"
    assert client.created_args[1] == PLAYLIST_NAME
    assert client.replaced_args == ("pl1", ["spotify:track:abc"])
