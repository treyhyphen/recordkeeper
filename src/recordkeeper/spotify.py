"""Spotify Web API adapter: OAuth + read-only playlist/saved-track access.

Auth is OAuth 2.0 Authorization Code (confidential client). The one-time
interactive `spotify-auth` command exchanges a code for tokens; the refresh
token is persisted to a per-account cache file (gitignored, mode 0600) and
Spotipy refreshes the access token automatically. Credentials (client_id,
client_secret, optional redirect_uri) come from `accounts.json`.
"""

from __future__ import annotations

from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth

_SCOPE = "playlist-read-private playlist-read-collaborative user-library-read"
_DEFAULT_REDIRECT = "http://localhost:8888/callback"


def cache_path(username: str, data_dir: str = "data") -> Path:
    """Per-account token cache file (gitignored)."""
    path = Path(data_dir) / f"spotify-cache-{username}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


class Spotify:
    """Bound to one account; wraps Spotipy for auth, refresh, and pagination."""

    def __init__(
        self,
        username: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        data_dir: str = "data",
    ):
        self.username = username
        self.oauth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri or _DEFAULT_REDIRECT,
            scope=_SCOPE,
            cache_handler=spotipy.CacheFileHandler(
                cache_path=str(cache_path(username, data_dir))
            ),
            show_dialog=True,
        )
        self.client = spotipy.Spotify(auth_manager=self.oauth)

    def authorize_url(self) -> str:
        return self.oauth.get_authorize_url()

    def complete_auth(self, redirect_url: str) -> dict:
        _state, code = self.oauth.parse_auth_response_url(redirect_url)
        return self.oauth.get_access_token(code)

    def authorized(self) -> bool:
        return self.oauth.get_cached_token() is not None
