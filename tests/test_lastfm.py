"""Provider failure handling uses synthetic responses, never live account writes."""

import pytest

from recordkeeper.lastfm import LastFM


def test_permanent_error_redacts_key(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"error":10,"message":"invalid"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response())
    with pytest.raises(RuntimeError, match="API error 10") as exc:
        LastFM("synthetic-secret").fetch("fixture", 123, 1)
    assert "synthetic-secret" not in str(exc.value)
