"""Load a `plays.csv` export into the `scrobbles` table.

CSV columns (header required): timestamp (unix), artist, track, album, mbid.
The `scrobbles` unique key on (source, artist_name, track_name, played_at) makes
re-runs idempotent: duplicate plays are skipped, not double-counted.

Usage: uv run python scripts/import_scrobbles.py data/exports/run-3/plays.csv
"""

import argparse
import csv
import sys
import uuid
from datetime import datetime, timezone

from recordkeeper.config import Config, load_dotenv
from recordkeeper.db import connect


def _uuid_or_none(value: str) -> uuid.UUID | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def main() -> None:
    load_dotenv()
    config = Config.from_env()
    if not config.database_url:
        sys.exit("DATABASE_URL is not configured")
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    args = parser.parse_args()

    with connect(config.database_url) as conn:
        # Stage into a temp table, then insert with ON CONFLICT so it is
        # idempotent and tolerates any duplicate (artist, track, played_at).
        conn.execute(
            """CREATE TEMP TABLE _scrobble_stage (
                   played_at timestamptz, artist_name text,
                   track_name text, album_name text, mbid uuid
               ) ON COMMIT DROP"""
        )
        with conn.cursor() as cur:
            with cur.copy(
                "COPY _scrobble_stage (played_at, artist_name, track_name, album_name, mbid) FROM STDIN"
            ) as copy:
                with open(args.csv_path, newline="") as f:
                    reader = csv.DictReader(f)
                    loaded = 0
                    for row in reader:
                        played = datetime.fromtimestamp(
                            int(row["timestamp"]), tz=timezone.utc
                        )
                        album = (row.get("album") or "").strip() or None
                        copy.write_row(
                            (
                                played,
                                row["artist"],
                                row["track"],
                                album,
                                _uuid_or_none(row.get("mbid", "")),
                            )
                        )
                        loaded += 1
        result = conn.execute(
            """INSERT INTO scrobbles
                   (played_at, source, artist_name, track_name, album_name, mbid)
               SELECT played_at, 'lastfm', artist_name, track_name, album_name, mbid
               FROM _scrobble_stage
               ON CONFLICT (source, artist_name, track_name, played_at) DO NOTHING"""
        )
        total = conn.execute("SELECT count(*) AS n FROM scrobbles").fetchone()["n"]
        print(
            f"staged {loaded} rows; inserted {result.rowcount}; scrobbles total = {total}"
        )


if __name__ == "__main__":
    main()
