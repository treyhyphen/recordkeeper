"""Configuration loaded from environment and an optional local `.env` file.

Secrets never belong in this repository. Copy `.env.example` to `.env`, set the
values, and keep it mode 0600 (it is gitignored). The application reads the
process environment; KeePassXC or another secret store is a *deployment-time*
source used to populate `.env`, not an application dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    """Parse a minimal `KEY=VALUE` .env file into os.environ (no override).

    Supports `#` comments and single/double-quoted values. Existing environment
    variables always win so an exported value is never silently replaced.
    """
    target = Path(path)
    if not target.is_file():
        return
    for raw in target.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    lastfm_username: str | None = None
    lastfm_api_key: str | None = None
    lastfm_api_secret: str | None = None
    database_url: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            lastfm_username=os.environ.get("LASTFM_USERNAME"),
            lastfm_api_key=os.environ.get("LASTFM_API_KEY"),
            lastfm_api_secret=os.environ.get("LASTFM_API_SECRET"),
            database_url=os.environ.get("DATABASE_URL"),
        )

    def require_lastfm_read(self) -> tuple[str, str]:
        """Return (username, api_key), raising a clear error if either is unset."""
        if not self.lastfm_username:
            raise RuntimeError("LASTFM_USERNAME is not configured")
        if not self.lastfm_api_key:
            raise RuntimeError("LASTFM_API_KEY is not configured")
        return self.lastfm_username, self.lastfm_api_key
