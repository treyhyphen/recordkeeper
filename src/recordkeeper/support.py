"""Support-these-artists shortlist: rank by plays, exclude what's already owned.

Ownership is signalled by the Plex inventory (`plex_items` of entity_type
'artist' = the vinyl rips). Artists you listen to a lot but don't own on vinyl
are surfaced as `suggested` recommendations, ranked by total plays. Plex
presence is only an exclusion signal; the `recommendations.status` field carries
manual overrides ('purchased', 'owned physically', etc. via the frontend later).
"""

from __future__ import annotations


def _upsert_artist(conn, name: str) -> int:
    """Resolve an artist name to a canonical `artists` row, returning its id."""
    return conn.execute(
        "INSERT INTO artists (name) VALUES (%s) "
        "ON CONFLICT ((lower(name))) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id",
        (name,),
    ).fetchone()["id"]


def build_shortlist(conn, account_id: int, min_plays: int = 10, limit: int = 50):
    """Top artists by play count that aren't owned on vinyl.

    Returns rows with `artist_name`, `plays`, `last_played`. Ownership is a
    case-insensitive match against the Plex artist inventory.
    """
    return conn.execute(
        """
        SELECT s.artist_name, COUNT(*) AS plays, MAX(s.played_at) AS last_played
        FROM scrobbles s
        WHERE s.account_id = %s
          AND NOT EXISTS (
              SELECT 1 FROM plex_items p
              WHERE p.entity_type = 'artist'
                AND lower(p.artist_name) = lower(s.artist_name)
          )
        GROUP BY s.artist_name
        HAVING COUNT(*) >= %s
        ORDER BY plays DESC, last_played DESC
        LIMIT %s
        """,
        (account_id, min_plays, limit),
    ).fetchall()


def sync_recommendations(
    conn,
    account_id: int,
    min_plays: int = 10,
    limit: int = 50,
    dry_run: bool = True,
) -> dict:
    """Upsert the shortlist into `recommendations` (status 'suggested')."""
    rows = build_shortlist(conn, account_id, min_plays, limit)
    if dry_run:
        return {"suggested": len(rows), "rows": rows}
    for row in rows:
        artist_id = _upsert_artist(conn, row["artist_name"])
        reason = f"{row['plays']} plays, last {row['last_played']:%Y-%m-%d}"
        conn.execute(
            """
            INSERT INTO recommendations (account_id, artist_id, reason, score, status)
            VALUES (%s, %s, %s, %s, 'suggested')
            ON CONFLICT (account_id, artist_id) DO UPDATE
              SET reason = EXCLUDED.reason, score = EXCLUDED.score, updated_at = now()
            """,
            (account_id, artist_id, reason, row["plays"]),
        )
    conn.commit()
    return {"suggested": len(rows), "rows": rows}
