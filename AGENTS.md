# Recordkeeper
Python-first modular monolith. Keep provider adapters separate from backup logic.
Never commit credentials or listening history. Credentials live in KeePassXC
(and are deployed to hosts via a gitignored `.env`, never committed).
Account writes are disabled until separately implemented and approved.
Test every behavior change, including interrupted pagination and repeat listens.
Do not claim backup completeness from a successful HTTP response alone.
Use uv.lock for reproducibility. Add UI incrementally after verified ingestion.

## Data store

PostgreSQL 16 is the canonical store, run via `docker compose up -d db` (see
`compose.yaml`; data dir bind-mounted at `/srv/recordkeeper/db` on the root LV).
Schema lives under `db/migrations/*.sql` and is applied idempotently with
`uv run recordkeeper migrate` (runner records versions in `schema_migrations`).

Schema principles (see `0001_init.sql`):
- Canonical entities (`artists`/`albums`/`tracks`) hold provider-agnostic
  identity; `external_identifiers` maps Spotify/MusicBrainz/Plex IDs onto them
  and is the backbone of cross-source matching.
- `scrobbles` is append-only, preserves repeat listens, and is deduped by the
  unique key `(source, artist_name, track_name, played_at)` — never by title.
- Provider payloads are kept as JSONB alongside typed columns.
- `sync_ledger` records idempotent cross-service writes (likes→loves,
  vinyl→scrobble); `recommendations` holds the support-artists shortlist state.

Never put a migration's transaction in the SQL file itself — the runner wraps
each file in `with conn.transaction()`. Do not add `BEGIN;`/`COMMIT;` to a
migration. The runner owns `schema_migrations`; do not recreate it in a
migration file.

## Backups

teletraan-1 pulls a read-only `backup-src` rrsync export of `data/` (`.snapshot/`
+ `exports/`) daily at 03:00 UTC. The toolbox `recordkeeper-snapshot.timer`
(02:30 UTC) stages a consistent SQLite `.backup` and a `pg_dump -Fc` dump into
`data/.snapshot/` so the puller never reads live files. Verify dumps with
`pg_restore` into a scratch DB before trusting them.
