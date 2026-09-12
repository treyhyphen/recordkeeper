"""Read-only Last.fm raw-page adapter.

pylast's public history iterator omits raw responses and resumable page control,
so the archival adapter uses the documented JSON read endpoint through Python
urllib. pylast remains installed and locked for future account operations.
The API key is supplied by the caller (from configuration); this module never
reads secrets itself so it stays portable across hosts and containers.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class LastFM:
    """Rate-limited read-only recent-track client with bounded retries."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, username: str, cutoff: int, page: int) -> dict:
        """Fetch one page; never leak credential-bearing URLs in exceptions."""
        query = urllib.parse.urlencode(
            {
                "method": "user.getRecentTracks",
                "user": username,
                "api_key": self.api_key,
                "format": "json",
                "limit": 200,
                "to": cutoff,
                "page": page,
            }
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(
                    "https://ws.audioscrobbler.com/2.0/?" + query, timeout=45
                ) as response:
                    data = json.load(response)
                if "error" not in data:
                    return data
                if int(data["error"]) not in (8, 11, 16, 29):
                    raise RuntimeError(f"Last.fm API error {data['error']}")
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise RuntimeError(f"Last.fm HTTP error {exc.code}") from None
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(min(60, 2 ** (attempt + 1)))
        raise RuntimeError(
            "Last.fm request failed after bounded retries; rerun to resume"
        )
