"""Tests for the vinyl-import → Last.fm scrobble automation."""

from datetime import datetime, timedelta, timezone

from recordkeeper.vinyl import detect_imports, estimate_times, scrobble_imports


class _Res:
    def __init__(self, fetchone=None, fetchall=None):
        self._fetchone = fetchone
        self._fetchall = fetchall

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return self._fetchall if self._fetchall is not None else []


class _Conn:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.writes = []
        self.commits = 0
        self._id = 1

    def execute(self, sql, params=None):
        sql = sql.strip()
        if sql.startswith("SELECT 1 FROM vinyl_imports"):
            return _Res(fetchone={"1": 1} if params[0] in self.existing else None)
        if "INSERT INTO vinyl_imports" in sql and "RETURNING id" in sql:
            self.writes.append(("import", params))
            row = {"id": self._id}
            self._id += 1
            return _Res(fetchone=row)
        if "INSERT INTO vinyl_import_tracks" in sql:
            self.writes.append(("track", params))
        return _Res()

    def commit(self):
        self.commits += 1


class _Track:
    def __init__(self, title, duration):
        self.title = title
        self.duration = duration


class _Album:
    def __init__(self, title, rating_key, added_at, tracks):
        self.title = title
        self.ratingKey = rating_key
        self.addedAt = added_at
        self._tracks = tracks
        self.locations = []

    def tracks(self):
        return self._tracks


class _Artist:
    def __init__(self, title, albums):
        self.title = title
        self._albums = albums

    def albums(self):
        return self._albums


class _Section:
    def __init__(self, title, artists):
        self.type = "artist"
        self.title = title
        self._artists = artists

    def all(self):
        return self._artists


class _Library:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


class _Plex:
    def __init__(self, sections):
        self._sections = sections

    @property
    def library(self):
        return _Library(self._sections)


def _music(albums):
    return _Plex([_Section("Music", [_Artist("King Buffalo", albums)])])


def _album(rating_key, added_at):
    return _Album(
        "Acheron",
        rating_key,
        added_at,
        [_Track("Zephyr", 300000), _Track("Mercury", 240000)],
    )


def test_estimate_times_ends_session_at_added_at():
    added = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    starts = estimate_times(added, [100000, 200000, 300000])  # 100s/200s/300s
    assert starts[0] == added - timedelta(seconds=600)
    assert starts[1] == added - timedelta(seconds=500)
    assert starts[2] == added - timedelta(seconds=300)
    assert starts[2] + timedelta(seconds=300) == added  # session ends at import


def test_detect_imports_baselines_old_albums():
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)  # > 14 days ago
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    conn = _Conn()
    stats = detect_imports(conn, _music([_album("rk-1", old)]), now=now)
    assert stats == {"new": 0, "baselined": 1}
    import_params = [w[1] for w in conn.writes if w[0] == "import"][0]
    assert import_params[2] == "skipped"  # status


def test_detect_imports_marks_recent_as_new():
    recent = datetime(2026, 9, 11, tzinfo=timezone.utc)  # within 14 days
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    conn = _Conn()
    stats = detect_imports(conn, _music([_album("rk-2", recent)]), now=now)
    assert stats == {"new": 1, "baselined": 0}
    import_params = [w[1] for w in conn.writes if w[0] == "import"][0]
    assert import_params[2] == "new"


def test_detect_imports_skips_existing():
    added = datetime(2026, 9, 11, tzinfo=timezone.utc)
    conn = _Conn(existing={"rk-3"})
    stats = detect_imports(conn, _music([_album("rk-3", added)]), now=added)
    assert stats == {"new": 0, "baselined": 0}
    assert conn.writes == []


def test_scrobble_imports_apply_scrobbles_and_records_ledger():
    class _Network:
        def __init__(self):
            self.scrobbles = []

        def scrobble(self, artist, title, ts, album=None, duration=None):
            self.scrobbles.append((artist, title, ts, album, duration))

    network = _Network()

    class _ScrobbleConn:
        def __init__(self):
            self.imports = [{"id": 1, "plex_rating_key": "rk-1"}]
            self.album = {"title": "Acheron", "artist_name": "King Buffalo"}
            self.tracks = [
                {
                    "id": 10,
                    "title": "Zephyr",
                    "duration_ms": 300000,
                    "estimated_played_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
                    "scrobbled": False,
                }
            ]
            self.updates = []
            self.commits = 0

        def execute(self, sql, params=None):
            s = sql.strip()
            if "FROM vinyl_imports WHERE status" in s:
                return _Res(fetchall=self.imports)
            if "FROM plex_items" in s:
                return _Res(fetchone=self.album)
            if "FROM vinyl_import_tracks WHERE import_id" in s:
                return _Res(fetchall=self.tracks)
            if s.startswith("UPDATE") or "INSERT INTO sync_ledger" in s:
                self.updates.append((s, params))
            return _Res()

        def commit(self):
            self.commits += 1

    conn = _ScrobbleConn()
    result = scrobble_imports(conn, 2, network, dry_run=False)
    assert result["tracks"] == 1
    assert len(network.scrobbles) == 1
    artist, title, ts, album, duration = network.scrobbles[0]
    assert (artist, title, album) == ("King Buffalo", "Zephyr", "Acheron")
    assert duration == 300  # ms → seconds
    assert any("INSERT INTO sync_ledger" in u[0] for u in conn.updates)
