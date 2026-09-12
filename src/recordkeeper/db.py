"""PostgreSQL connection and schema-migration runner.

Migrations live under `db/migrations/*.sql` and are applied in filename order,
each inside its own transaction, with applied versions recorded in the
`schema_migrations` table so runs are idempotent and resumable.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def connect(url: str) -> psycopg.Connection:
    """Open a connection with row-dict rows (caller owns commit/close)."""
    return psycopg.connect(url, row_factory=dict_row)


def migrate(url: str, migrations_dir: str = "db/migrations") -> list[str]:
    """Apply any pending migrations and return the list of newly applied ones."""
    applied: list[str] = []
    with psycopg.connect(url) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version    TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        conn.commit()
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(Path(migrations_dir).glob("*.sql")):
            version = path.name
            if version in done:
                continue
            sql = path.read_text()
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
                )
            applied.append(version)
    return applied
