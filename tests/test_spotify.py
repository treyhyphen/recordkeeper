"""Spotify snapshot helpers: normalization, pagination, change detection."""

from recordkeeper.spotify_backup import (
    _upsert_playlist,
    extract_track,
    item_track,
    paginate,
)


def test_extract_track_normalizes():
    track = {
        "uri": "spotify:track:abc",
        "name": "Song",
        "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
        "album": {"name": "Album"},
        "duration_ms": 123456,
    }
    t = extract_track(track)
    assert t is not None
    assert t["uri"] == "spotify:track:abc"
    assert t["name"] == "Song"
    assert t["artists"] == ["Artist A", "Artist B"]
    assert t["album"] == "Album"
    assert t["duration_ms"] == 123456


def test_extract_track_deleted_returns_none():
    assert extract_track(None) is None
    assert extract_track({}) is None


def test_item_track_handles_playlist_item_key():
    # playlist_items returns the track under `item` when additional_types is set
    item = {
        "added_at": "2026-08-27T18:51:05Z",
        "item": {
            "uri": "spotify:track:x",
            "name": "Song",
            "artists": [{"name": "A"}],
            "album": {"name": "Al"},
        },
    }
    assert item_track(item)["uri"] == "spotify:track:x"
    # current_user_saved_tracks returns it under `track`
    saved = {
        "track": {"uri": "spotify:track:y", "name": "T", "artists": [], "album": {}}
    }
    assert item_track(saved)["uri"] == "spotify:track:y"


class _FakeClient:
    def __init__(self, pages):
        self.pages = pages

    def next(self, results):
        return self.pages.pop(0) if self.pages else None


def test_paginate_follows_cursor():
    page1 = {"items": [1, 2], "next": "url"}
    page2 = {"items": [3]}
    results = list(paginate(_FakeClient([page2]), page1))
    assert [r["items"] for r in results] == [[1, 2], [3]]


class _Res:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, existing=None):
        self.existing = existing
        self.next_id = 100

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT"):
            return _Res(self.existing)
        if "INSERT" in sql:
            return _Res({"id": self.next_id})
        return _Res()


def test_upsert_playlist_new_marks_changed():
    item = {"id": "pid1", "name": "P", "owner": {}, "snapshot_id": "S1"}
    playlist_id, changed = _upsert_playlist(
        _Conn(existing=None), account_id=1, item=item
    )
    assert playlist_id == 100
    assert changed is True


def test_upsert_playlist_unchanged_snapshot_id():
    conn = _Conn(existing={"id": 5, "snapshot_id": "S1"})
    item = {"id": "pid1", "name": "P", "owner": {}, "snapshot_id": "S1"}
    playlist_id, changed = _upsert_playlist(conn, account_id=1, item=item)
    assert playlist_id == 5
    assert changed is False


def test_upsert_playlist_changed_snapshot_id():
    conn = _Conn(existing={"id": 5, "snapshot_id": "S1"})
    item = {"id": "pid1", "name": "P", "owner": {}, "snapshot_id": "S2"}
    playlist_id, changed = _upsert_playlist(conn, account_id=1, item=item)
    assert playlist_id == 5
    assert changed is True
