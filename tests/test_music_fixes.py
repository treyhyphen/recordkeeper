"""Regression coverage for missing provider metadata and replacement candidates."""

import pytest
from spotipy.exceptions import SpotifyException
from test_likes import _Conn as LikesConn
from test_likes import _saved
from test_throwback import _ROW, _Conn

from recordkeeper.likes import sync_loves
from recordkeeper.throwback import resolve_uris, sync_throwback


def test_replacements_and_deduplication():
    """Missing and duplicate matches consume candidates, not playlist slots."""

    class Client:
        def __init__(self):
            self.responses = iter([[], ["a"], ["a"], ["b"]])

        def search(self, **kwargs):
            return {
                "tracks": {
                    "items": [
                        {"uri": "spotify:track:" + x} for x in next(self.responses)
                    ]
                }
            }

    assert resolve_uris(Client(), [_ROW] * 5, delay=0, limit=2) == [
        "spotify:track:a",
        "spotify:track:b",
    ]


def test_rate_limit_aborts():
    """Provider failures must not silently empty the playlist."""

    class Client:
        def search(self, **kwargs):
            raise SpotifyException(429, -1, "rate limited")

    with pytest.raises(SpotifyException):
        sync_throwback(_Conn([_ROW]), 1, Client(), dry_run=False)


def test_insufficient_matches_preserves_playlist():
    """No write occurs when the eligible pool is exhausted."""

    class Client:
        def search(self, **kwargs):
            return {"tracks": {"items": []}}

    with pytest.raises(RuntimeError, match="playlist unchanged"):
        sync_throwback(_Conn([_ROW]), 1, Client(), dry_run=False)


@pytest.mark.parametrize("preview", [True, False])
def test_blank_metadata_never_calls_lastfm(preview):
    """Invalid saved tracks are accounted for locally, without provider calls."""
    conn = LikesConn(
        [_saved("spotify:track:a", "", []), _saved("spotify:track:b", " ", ["A"])]
    )

    def unexpected(*args):
        raise AssertionError("Last.fm must not receive blank metadata")

    result = sync_loves(conn, 1, 2, unexpected, correct=unexpected, dry_run=preview)
    assert result["skipped"] == 2
    assert result["failed"] == 0
    assert conn.commits == (0 if preview else 2)
