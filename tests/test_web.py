"""Smoke tests for the read-only web UI (server-rendered pages)."""

from fastapi.testclient import TestClient

from recordkeeper import web


class _Res:
    def __init__(self, data):
        self._data = data

    def fetchone(self):
        return self._data

    def fetchall(self):
        return self._data


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "count(*)" in sql:
            return _Res(
                {
                    "scrobbles": 5,
                    "artists": 3,
                    "tracks": 10,
                    "playlists": 2,
                    "saved_tracks": 4,
                    "plex_artists": 1,
                    "plex_albums": 1,
                    "plex_tracks": 8,
                    "vinyl_imports": 1,
                    "recommendations": 3,
                    "loved_tracks": 2,
                }
            )
        if "GROUP BY artist_name" in sql:
            return _Res(
                [{"artist_name": "King Buffalo", "plays": 100, "last_played": None}]
            )
        if "FROM recommendations" in sql:
            return _Res(
                [
                    {
                        "name": "Singto Conley",
                        "score": 2911,
                        "status": "suggested",
                        "reason": "2911 plays",
                        "created_at": None,
                    }
                ]
            )
        return _Res(
            [
                {
                    "artist_name": "King Buffalo",
                    "track_name": "Grifter",
                    "album_name": "Acheron",
                    "played_at": None,
                    "source": "lastfm",
                }
            ]
        )


def _client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(web, "connect", lambda url: _Conn())
    return TestClient(web.app)


def test_dashboard_renders(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "King Buffalo" in r.text
    assert "Scrobbles" in r.text


def test_support_renders(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/support")
    assert r.status_code == 200
    assert "Singto Conley" in r.text


def test_vinyl_renders(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/vinyl")
    assert r.status_code == 200


def test_missing_db_returns_503(monkeypatch):
    def boom(url):
        raise RuntimeError("no db")

    monkeypatch.setattr(web, "connect", boom)
    client = TestClient(web.app)
    r = client.get("/")
    assert r.status_code == 503
