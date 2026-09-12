# Recordkeeper
Python-first modular monolith. Keep provider adapters separate from backup logic.
Never commit credentials or listening history. Credentials live in KeePassXC
(and are deployed to hosts via gitignored `.env` + `accounts.json`, never committed).
Account writes are disabled until separately implemented and approved.
Test every behavior change, including interrupted pagination and repeat listens.
Do not claim backup completeness from a successful HTTP response alone.
Use uv.lock for reproducibility. Add UI incrementally after verified ingestion.

## Data store

PostgreSQL 16 is the canonical store, run via `docker compose up -d db` (see
`compose.yaml`; data dir bind-mounted at `/srv/recordkeeper/db` on the root LV).
Schema lives under `db/migrations/*.sql` and is applied idempotently with
`uv run recordkeeper migrate` (runner records versions in `schema_migrations`).

Schema principles (see `0001_init.sql`, `0002_multi_account.sql`):
- Canonical entities (`artists`/`albums`/`tracks`) hold provider-agnostic
  identity; `external_identifiers` maps Spotify/MusicBrainz/Plex IDs onto them
  and is the backbone of cross-source matching.
- `scrobbles` is append-only, preserves repeat listens, and is deduped by the
  unique key `(account_id, source, artist_name, track_name, played_at)` — never
  by title.
- Provider payloads are kept as JSONB alongside typed columns.
- `sync_ledger` records idempotent cross-service writes (likes→loves,
  vinyl→scrobble); `recommendations` holds the support-artists shortlist state.

Never put a migration's transaction in the SQL file itself — the runner wraps
each file in `with conn.transaction()`. Do not add `BEGIN;`/`COMMIT;` to a
migration. The runner owns `schema_migrations`; do not recreate it in a
migration file.

## Accounts (multi-account per platform)

Accounts live in a gitignored `accounts.json` (mode 0600) — one entry per
connected account, keyed by `platform` + `username`, with platform-specific
credentials inline (e.g. Last.fm `api_key`/`api_secret`). The `accounts` table
mirrors only metadata (platform, username, display name, enabled); secrets never
enter the database. `ensure_accounts()` upserts metadata and returns ids.

Account-scoped tables carry an `account_id` FK, and uniqueness is per account
(`saved_tracks` URI, `playlists` id, `loved_tracks` artist+track) so two family
members' data never collide. Do not introduce a global unique key on a field
that is naturally per-account.

## Scrobble ingestion (incremental sync)

`recordkeeper sync` pulls new Last.fm scrobbles into `scrobbles` incrementally:
it pages newest-first and stops at the account's high-water mark (latest stored
`played_at`). A full-history pull happens automatically for a brand-new account
(no watermark). It is idempotent (unique key + `ON CONFLICT DO NOTHING`) and
runs every 30 minutes via `recordkeeper-sync.timer` (user `err`). Backfilling a
pre-existing account's unlinked rows is a one-time `scripts/backfill_accounts.py`
step (guarded: only when exactly one enabled Last.fm account exists).

## Backups

teletraan-1 pulls a read-only `backup-src` rrsync export of `data/` (`.snapshot/`
+ `exports/`) daily at 03:00 UTC. The toolbox `recordkeeper-snapshot.timer`
(02:30 UTC) stages a consistent SQLite `.backup` and a `pg_dump -Fc` dump into
`data/.snapshot/` so the puller never reads live files. Verify dumps with
`pg_restore` into a scratch DB before trusting them.

## Spotify snapshots

`recordkeeper spotify-auth --user <u>` runs the one-time OAuth flow (prints an
authorization URL, exchanges the pasted redirect for a refresh token cached at
`data/spotify-cache-<u>.json`). `recordkeeper spotify-backup` snapshots every
visible playlist plus saved tracks. Playlists are versioned: the current
`snapshot_id` is stored on `playlists`, and a new `playlist_snapshots` capture
(ordered items) is written only when that id changes, so repeat runs are cheap
and history is preserved. Scopes: `playlist-read-private`,
`playlist-read-collaborative`, `user-library-read`. Spotify account credentials
are `client_id`/`client_secret`/`redirect_uri` in `accounts.json`.

