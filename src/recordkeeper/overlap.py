"""Read-only, account-scoped comparisons of current Spotify snapshots.

Songs use exact Spotify URIs (not fuzzy titles); repeated entries count once.
Artists use Unicode-preserving casefolded names. No provider calls or writes.
"""

from itertools import combinations


def similarity(left, right):
    """Return shared count, Jaccard percent and directional containment."""
    shared = len(left & right)
    union = len(left | right)
    return {
        "shared": shared,
        "overall": 100 * shared / union if union else 0,
        "a_in_b": 100 * shared / len(left) if left else 0,
        "b_in_a": 100 * shared / len(right) if right else 0,
    }


def compare(playlists):
    """Rank nonzero same-account pairs; never treat unknown contents as empty."""
    results = []
    for a, b in combinations(playlists, 2):
        if (
            a["account_id"] != b["account_id"]
            or not a["available"]
            or not b["available"]
        ):
            continue
        songs = similarity(a["songs"], b["songs"])
        artists = similarity(a["artists"], b["artists"])
        if not songs["shared"] and not artists["shared"]:
            continue
        candidate = songs["shared"] >= 5 and max(songs["a_in_b"], songs["b_in_a"]) >= 80
        results.append(
            {"a": a, "b": b, "songs": songs, "artists": artists, "candidate": candidate}
        )
    return sorted(
        results,
        key=lambda r: (
            -r["songs"]["overall"],
            -r["songs"]["shared"],
            r["a"]["id"],
            r["b"]["id"],
        ),
    )


def report(conn, account_id):
    """Load current snapshots only and return coverage plus ranked comparisons."""
    rows = conn.execute(
        """
        SELECT p.id, p.account_id, p.name, ps.id AS capture_id, ps.captured_at,
               i.provider_uri, i.artist_names
        FROM playlists p
        LEFT JOIN LATERAL (
            SELECT id, captured_at FROM playlist_snapshots s
            WHERE s.playlist_id = p.id AND s.snapshot_id = p.snapshot_id
            ORDER BY captured_at DESC, id DESC LIMIT 1
        ) ps ON true
        LEFT JOIN playlist_snapshot_items i ON i.snapshot_id = ps.id
        WHERE p.account_id = %s AND p.provider = 'spotify'
        ORDER BY p.id
    """,
        (account_id,),
    ).fetchall()
    playlists = {}
    for row in rows:
        item = playlists.setdefault(
            row["id"],
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "name": row["name"],
                "available": row["capture_id"] is not None,
                "captured_at": row["captured_at"],
                "songs": set(),
                "artists": set(),
            },
        )
        uri = row["provider_uri"]
        if uri and uri.startswith("spotify:track:"):
            item["songs"].add(uri)
            item["artists"].update(
                n.strip().casefold()
                for n in row["artist_names"] or []
                if n and n.strip()
            )
    values = list(playlists.values())
    return {
        "pairs": compare(values),
        "total": len(values),
        "available": sum(p["available"] for p in values),
        "unavailable": [p for p in values if not p["available"]],
    }
