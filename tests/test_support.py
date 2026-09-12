"""Tests for the support-these-artists shortlist."""

from datetime import datetime, timezone

from recordkeeper.support import build_shortlist, sync_recommendations


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
        self.writes = []
        self.commits = 0
        self._artist_id = 1

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        if "INSERT INTO artists" in sql:
            return _Res(fetchone={"id": self._artist_id})
        if "INSERT INTO recommendations" in sql:
            self.writes.append((sql, params))
            return _Res()
        return _Res(fetchall=self.rows)

    def commit(self):
        self.commits += 1


_ROW = {
    "artist_name": "King Buffalo",
    "plays": 100,
    "last_played": datetime(2026, 1, 1, tzinfo=timezone.utc),
}


def test_build_shortlist_excludes_owned():
    conn = _Conn([_ROW])
    rows = build_shortlist(conn, 7, min_plays=10, limit=50)
    assert len(rows) == 1
    assert "NOT EXISTS" in conn.last_sql
    assert "entity_type = 'artist'" in conn.last_sql
    assert conn.last_params == (7, 10, 50)


def test_sync_recommendations_dry_run_does_not_write():
    conn = _Conn([_ROW])
    result = sync_recommendations(conn, 7, dry_run=True)
    assert result["suggested"] == 1
    assert conn.writes == []
    assert conn.commits == 0


def test_sync_recommendations_apply_upserts():
    conn = _Conn([_ROW])
    result = sync_recommendations(conn, 7, dry_run=False)
    assert result["suggested"] == 1
    assert len(conn.writes) == 1
    sql, params = conn.writes[0]
    assert "'suggested'" in sql
    assert params[0] == 7  # account_id
    assert params[1] == 1  # artist_id
    assert params[3] == 100  # score = plays
    assert "100 plays" in params[2]  # reason
    assert conn.commits == 1
