"""Tests for the Throwback Thursday playlist builder."""

from recordkeeper.throwback import (
    PLAYLIST_NAME,
    load_playlist_id,
    resolve_uris,
    save_playlist_id,
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


_ROW = {"artist_name": "A", "track_name": "B", "last_played": "2026-01-01", "plays": 5}


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


def test_playlist_id_roundtrip(tmp_path):
    assert load_playlist_id(tmp_path, "missing") is None
    save_playlist_id(tmp_path, "u1", "pl123")
    assert load_playlist_id(tmp_path, "u1") == "pl123"


def test_sync_throwback_dry_run_does_not_write():
    class _Client:
        def search(self, q=None, type=None, limit=None):
            return {"tracks": {"items": [{"uri": "spotify:track:abc"}]}}

        def playlist_replace_items(self, *a, **kw):
            raise AssertionError("should not write in dry-run")

    conn = _Conn([_ROW])
    result = sync_throwback(conn, 7, _Client(), dry_run=True)
    assert result["resolved"] == 1
    assert result["replaced"] == 0
    assert result["created"] is False


def test_sync_throwback_apply_creates_when_no_id():
    class _Client:
        def __init__(self):
            self.created_args = None
            self.replaced_args = None

        def search(self, q=None, type=None, limit=None):
            return {"tracks": {"items": [{"uri": "spotify:track:abc"}]}}

        def _post(self, path, payload=None):
            self.created_args = (path, payload)
            return {"id": "pl1"}

        def _put(self, path, payload=None):
            self.replaced_args = (path, payload)

    conn = _Conn([_ROW])
    client = _Client()
    result = sync_throwback(conn, 7, client, dry_run=False)
    assert result["created"] is True
    assert result["replaced"] == 1
    assert result["playlist_id"] == "pl1"
    assert client.created_args[0] == "me/playlists"
    assert client.created_args[1]["name"] == PLAYLIST_NAME
    assert client.replaced_args == (
        "playlists/pl1/items",
        {"uris": ["spotify:track:abc"]},
    )


def test_sync_throwback_apply_reuses_existing_id():
    class _Client:
        def __init__(self):
            self.replaced_args = None

        def search(self, q=None, type=None, limit=None):
            return {"tracks": {"items": [{"uri": "spotify:track:abc"}]}}

        def _post(self, path, payload=None):
            raise AssertionError("should not create when id is provided")

        def _put(self, path, payload=None):
            self.replaced_args = (path, payload)

    conn = _Conn([_ROW])
    client = _Client()
    result = sync_throwback(conn, 7, client, dry_run=False, playlist_id="pl-existing")
    assert result["created"] is False
    assert result["playlist_id"] == "pl-existing"
    assert client.replaced_args[0] == "playlists/pl-existing/items"
