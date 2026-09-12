"""Archive integrity and restart regression tests (synthetic fixtures)."""

import json

import pytest

from recordkeeper.backup import backup, connect, export, tracks


def payload(page=1, pages=1, total=2):
    """Create explicitly synthetic identical occurrences to test preservation."""
    track = {"date": {"uts": "100"}, "artist": {"#text": "Fixture"}, "name": "Test"}
    return {
        "recenttracks": {
            "@attr": {"page": str(page), "totalPages": str(pages), "total": str(total)},
            "track": [track, track],
        }
    }


def test_repeat_plays_and_export(tmp_path):
    db = connect(tmp_path / "db")
    run, count = backup(db, "fixture", lambda *args, **kwargs: payload(), delay=0)
    assert count == 2
    path = export(db, run, tmp_path / "exports")
    assert len((path / "plays.csv").read_text().splitlines()) == 3
    assert len(tracks(json.loads((path / "pages.jsonl").read_text()))) == 2


def test_resume(tmp_path):
    db = connect(tmp_path / "db")

    def fetch(user, page, to=None):
        if page == 2:
            raise RuntimeError("simulated outage")
        return payload(page, 2, 4)

    with pytest.raises(RuntimeError):
        backup(db, "fixture", fetch, delay=0)
    seen = []

    def resume(user, page, to=None):
        seen.append(page)
        return payload(page, 2, 4)

    assert backup(db, "fixture", resume, delay=0)[1] == 4
    assert seen == [2]


def test_count_mismatch(tmp_path):
    db = connect(tmp_path / "db")
    with pytest.raises(ValueError, match="count mismatch"):
        backup(db, "fixture", lambda *args, **kwargs: payload(total=3), delay=0)
    with pytest.raises(ValueError):
        export(db, 1, tmp_path)


def test_now_playing_excluded():
    data = payload()
    data["recenttracks"]["track"].append({"@attr": {"nowplaying": "true"}})
    assert len(tracks(data)) == 2


def test_wrong_page_not_committed(tmp_path):
    db = connect(tmp_path / "db")
    with pytest.raises(ValueError, match="wrong page"):
        backup(db, "fixture", lambda *args, **kwargs: payload(page=2), delay=0)
    assert db.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0


def test_empty_history(tmp_path):
    db = connect(tmp_path / "db")
    data = payload(total=0)
    data["recenttracks"]["track"] = []
    assert backup(db, "fixture", lambda *args, **kwargs: data, delay=0)[1] == 0
