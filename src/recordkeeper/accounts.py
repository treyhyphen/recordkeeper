"""Account registry: mirror configured accounts into PostgreSQL."""

from __future__ import annotations


def ensure_accounts(conn, accounts) -> dict[tuple[str, str], int]:
    """Upsert configured accounts and return {(platform, username): id}.

    Credentials never enter the database: only metadata (platform, username,
    display name, enabled flag) is mirrored so tables can reference accounts by
    id. Secrets stay in the gitignored `accounts.json` and are read at runtime.
    """
    result: dict[tuple[str, str], int] = {}
    for acct in accounts:
        row = conn.execute(
            """
            INSERT INTO accounts (platform, username, display_name, enabled)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (platform, username) DO UPDATE
              SET display_name = EXCLUDED.display_name,
                  enabled = EXCLUDED.enabled,
                  updated_at = now()
            RETURNING id
            """,
            (acct.platform, acct.username, acct.display_name, acct.enabled),
        ).fetchone()
        result[(acct.platform, acct.username)] = row["id"]
    conn.commit()
    return result


def select_lastfm_account(accounts, username=None):
    """Return an enabled Last.fm account (optionally narrowed by username)."""
    enabled = [a for a in accounts if a.platform == "lastfm" and a.enabled]
    if not enabled:
        raise RuntimeError("No enabled Last.fm account configured (accounts.json)")
    if username:
        for acct in enabled:
            if acct.username == username:
                return acct
        raise RuntimeError(f"No enabled Last.fm account named {username!r}")
    return enabled[0]
