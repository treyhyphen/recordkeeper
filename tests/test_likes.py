"""Tests for the Spotify-likes → Last.fm-loves sync."""

from recordkeeper.likes import normalize, sync_loves


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, unsynced):
        self.unsynced = unsynced
        self.writes = []
        self.commits = 0

    def execute(self, sql, params=None):
        if sql.lstrip().startswith("SELECT"):
            return _Res(self.unsynced)
        self.writes.append((sql, params))
        return _Res([])

    def commit(self):
        self.commits += 1


def _saved(uri, title, artists):
    return {"provider_uri": uri, "track_name": title, "artist_names": artists}


def test_normalize_strips_punctuation_case_and_diacritics():
    assert normalize("King Buffalo") == "king buffalo"
    assert normalize("Song (feat. Someone)") == "song"
    assert normalize("A&B") == "a and b"
    assert normalize("Café Del Mar") == "cafe del mar"


def test_preview_reports_without_writing():
    conn = _Conn([_saved("spotify:track:1", "Grifter", ["King Buffalo"])])
    result = sync_loves(conn, 1, 2, love=lambda *a: None, dry_run=True)
    assert result["candidates"] == 1
    assert conn.writes == []


def test_apply_loves_and_records_ledger():
    loved = []
    conn = _Conn([_saved("spotify:track:1", "Grifter", ["King Buffalo"])])

    def love(artist, title):
        loved.append((artist, title))

    result = sync_loves(conn, 1, 2, love=love, dry_run=False)
    assert loved == [("King Buffalo", "Grifter")]
    assert result["candidates"] == 1
    assert any("INSERT INTO sync_ledger" in w[0] for w in conn.writes)
    assert conn.commits == 1


def test_correction_uses_canonical_title():
    def correct(artist, title):
        return "Grifter (Remastered)"

    conn = _Conn([_saved("spotify:track:1", "Grifter", ["King Buffalo"])])
    result = sync_loves(conn, 1, 2, love=lambda *a: None, correct=correct, dry_run=True)
    assert result["corrected"] == 1
