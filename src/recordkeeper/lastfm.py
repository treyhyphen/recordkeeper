"""Read-only Last.fm raw-page adapter.

pylast's public history iterator omits raw responses and page checkpoints.
Use the documented JSON endpoint for lossless archival; retain pylast for
future account operations rather than depending on its private internals.
"""

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def vault_key():
    """Read the API key from KeePassXC without printing it or using argv secrets."""
    home = Path.home() / ".hermes/secrets"
    result = subprocess.run(
        [
            "keepassxc-cli",
            "show",
            "--no-password",
            "--key-file",
            str(home / "hermes-credentials.key"),
            "-s",
            "-a",
            "Password",
            str(home / "hermes-credentials.kdbx"),
            "Recordkeeper/Last.fm API key",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Cannot read Recordkeeper Last.fm API key from vault")
    return result.stdout.strip()


class LastFM:
    """Rate-limited read-only recent-track client with bounded retries."""

    def __init__(self, api_key):
        """Retain the key in process memory only."""
        self.api_key = api_key

    def fetch(self, username, cutoff, page):
        """Fetch one page; never leak credential-bearing URLs in exceptions."""
        query = urllib.parse.urlencode(
            dict(
                method="user.getRecentTracks",
                user=username,
                api_key=self.api_key,
                format="json",
                limit=200,
                to=cutoff,
                page=page,
            )
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
