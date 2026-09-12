"""Configuration loaded from environment, `.env`, and an optional accounts file.

Secrets never belong in this repository. `.env` (mode 0600, gitignored) holds the
database URL; `accounts.json` (mode 0600, gitignored) holds one entry per
connected account across platforms. KeePassXC is a *deployment-time* source used
to populate these files, not an application dependency.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
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
class Account:
    """A connected account on a provider (e.g. one Last.fm or Spotify user)."""

    platform: str
    username: str
    display_name: str = ""
    enabled: bool = True
    credentials: dict = field(default_factory=dict)  # platform-specific secrets

    def credential(self, key: str) -> str | None:
        return self.credentials.get(key)


def load_accounts(path: str = "accounts.json") -> list[Account]:
    """Load accounts from a gitignored JSON file (absent file -> empty list).

    Any key besides platform/username/display_name/enabled is treated as a
    platform-specific credential (e.g. `api_key`, `api_secret` for Last.fm).
    """
    target = Path(path)
    if not target.is_file():
        return []
    data = json.loads(target.read_text())
    known = {"platform", "username", "display_name", "enabled"}
    accounts = []
    for raw in data.get("accounts", []):
        credentials = {
            k: v for k, v in raw.items() if k not in known and v not in (None, "")
        }
        accounts.append(
            Account(
                platform=raw["platform"],
                username=raw["username"],
                display_name=raw.get("display_name", ""),
                enabled=bool(raw.get("enabled", True)),
                credentials=credentials,
            )
        )
    return accounts


@dataclass(frozen=True)
class Config:
    database_url: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        return cls(database_url=os.environ.get("DATABASE_URL"))
