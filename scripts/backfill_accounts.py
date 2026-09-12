"""One-time backfill: assign the sole legacy Last.fm account to unlinked scrobbles.

Before multi-account support, every imported scrobble had `account_id = NULL`.
This links those rows to the single enabled Last.fm account. It refuses to run
unless there is exactly one enabled Last.fm account, because with several
accounts there is no way to know which rows belong to whom.
"""

from recordkeeper.accounts import ensure_accounts
from recordkeeper.config import Config, load_accounts, load_dotenv
from recordkeeper.db import connect


def main() -> None:
    load_dotenv()
    config = Config.from_env()
    if not config.database_url:
        raise SystemExit("DATABASE_URL is not configured")
    accounts = load_accounts()
    lastfm = [a for a in accounts if a.platform == "lastfm" and a.enabled]
    if len(lastfm) != 1:
        raise SystemExit(
            f"refusing backfill: expected exactly 1 enabled Last.fm account, found {len(lastfm)}"
        )
    acct = lastfm[0]
    with connect(config.database_url) as conn:
        ids = ensure_accounts(conn, accounts)
        account_id = ids[(acct.platform, acct.username)]
        result = conn.execute(
            "UPDATE scrobbles SET account_id = %s "
            "WHERE account_id IS NULL AND source = 'lastfm'",
            (account_id,),
        )
        conn.commit()
        print(
            f"assigned {acct.username} (id={account_id}) to {result.rowcount} scrobbles"
        )


if __name__ == "__main__":
    main()
