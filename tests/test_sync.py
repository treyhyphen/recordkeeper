"""Incremental scrobble sync: normalization, watermark stop, pagination."""

from datetime import datetime, timezone

from recordkeeper.sync import extract_play, fetch_new_plays, sync_scrobbles


def _ts(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _track(uts, artist="Artist", name="Track", nowplaying=False, mbid=""):
    item = {
        "artist": {"#text": artist},
        "name": name,
        "album": {"#text": "Album"},
        "date": {"uts": str(uts)},
    }
    if mbid:
        item["mbid"] = mbid
    if nowplaying:
        item["@attr"] = {"nowplaying": "true"}
    return item


def _page(items, page, total_pages):
    return {
        "recenttracks": {
            "@attr": {"page": str(page), "totalPages": str(total_pages)},
            "track": items,
        }
    }


def test_extract_play_normalizes():
    item = {
        "artist": {"#text": "Radiohead"},
        "name": "Karma Police",
        "album": {"#text": "OK Computer"},
        "date": {"uts": "1000000000"},
        "loved": "1",
    }
    play = extract_play(item)
    assert play["artist_name"] == "Radiohead"
    assert play["track_name"] == "Karma Police"
    assert play["album_name"] == "OK Computer"
    assert play["loved"] is True
    assert play["played_at"] == _ts(1000000000)
    assert play["mbid"] is None


def test_fetch_new_plays_stops_at_watermark():
    pages = [
        _page([_track(100), _track(90)], 1, 2),
        _page([_track(50), _track(40)], 2, 2),
    ]

    def fetch(username, page, to=None):
        return pages[page - 1]

    plays = list(fetch_new_plays(fetch, "u", watermark=_ts(50), delay=0))
    assert [p["played_at"] for p in plays] == [_ts(100), _ts(90)]


def test_fetch_new_plays_skips_now_playing():
    pages = [_page([_track(100, nowplaying=True), _track(90)], 1, 1)]

    def fetch(username, page, to=None):
        return pages[page - 1]

    plays = list(fetch_new_plays(fetch, "u", watermark=None, delay=0))
    assert [p["played_at"] for p in plays] == [_ts(90)]


def test_fetch_new_plays_full_history_without_watermark():
    pages = [_page([_track(30), _track(20)], 1, 2), _page([_track(10)], 2, 2)]

    def fetch(username, page, to=None):
        return pages[page - 1]

    plays = list(fetch_new_plays(fetch, "u", watermark=None, delay=0))
    assert [p["played_at"] for p in plays] == [_ts(30), _ts(20), _ts(10)]


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, watermark=None):
        self.watermark = watermark
        self.inserts = []
        self.committed = 0

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT"):
            return _Result({"w": self.watermark})
        self.inserts.append(params)
        return _Result()

    def commit(self):
        self.committed += 1


def test_sync_scrobbles_deep_ignores_watermark():
    pages = [_page([_track(100), _track(90)], 1, 2), _page([_track(80)], 2, 2)]

    def fetch(username, page, to=None):
        return pages[page - 1]

    # watermark (95) would normally stop after the first page; deep must ignore it
    conn = _FakeConn(watermark=_ts(95))
    inserted = sync_scrobbles(
        conn, account_id=7, username="u", fetch=fetch, delay=0, deep=True
    )
    assert inserted == 3
    assert [p[1] for p in conn.inserts] == [_ts(100), _ts(90), _ts(80)]


def test_sync_scrobbles_inserts_and_commits():
    pages = [_page([_track(100), _track(90)], 1, 1)]

    def fetch(username, page, to=None):
        return pages[page - 1]

    conn = _FakeConn(watermark=_ts(50))
    inserted = sync_scrobbles(conn, account_id=7, username="u", fetch=fetch, delay=0)
    assert inserted == 2
    assert len(conn.inserts) == 2
    assert conn.committed == 1
    account_id, played_at, source, artist, track = conn.inserts[0][:5]
    assert account_id == 7
    assert source == "lastfm"
    assert played_at == _ts(100)
