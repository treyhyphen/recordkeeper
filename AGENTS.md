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

`recordkeeper-spotify-backup.timer` runs the snapshot daily at 03:30 UTC.
Requests are throttled (0.2 s between pages) and a 429 rate-limit exits 0
gracefully so the next scheduled run resumes — do not hammer Spotify with
repeated manual full runs or you will trip a ~24 h extended rate limit. Followed
playlists owned by others are recorded as metadata only (Spotify returns 403 for
their contents).

## Likes → loves sync

`recordkeeper lastfm-auth --user <u>` runs the one-time Last.fm web-auth flow
(grant `track.love`), caching a session key at `data/lastfm-session-<u>.json`.
`recordkeeper likes-sync` resolves each Spotify saved track to its Last.fm
canonical title and loves it there, one-way and additive (an unlike on Spotify
never unloves on Last.fm). Preview (default) reports the resolution without
writing; `--apply` performs the loves. Idempotency comes from `sync_ledger`
(`task_type='likes_to_loves'`, `source_key` = Spotify URI, per-account unique):
only tracks not already `synced`/`skipped` are touched. Writes commit per track
so a long backfill is durable and resumable. Last.fm error 6 (track not found)
is recorded `skipped`, not retried. Throttled at 0.5 s/track (≈2 requests:
`getCorrection` + `love`) to stay under Last.fm's 5 req/s. `recordkeeper
likes-sync.timer` runs `--apply` every 30 min for forward sync.

## Plex inventory

`recordkeeper plex-inventory` enumerates the Plex `artist`-type library sections
(artists → albums → tracks) and upserts them into `plex_items`, keyed on Plex's
`ratingKey`. This is read-only and is the "what is already owned" backbone for
the vinyl-scrobble and support-these-artists features. The music library
("Music", `/mnt/Media/Music/Vinyl Rips`) is the vinyl-rips section; its
`added_at` timestamps are the anchor for estimating vinyl scrobble times.
`recordkeeper-plex-inventory.timer` refreshes daily at 03:45 UTC.

The Plex server is `wopr` (`172.16.1.5:32400`); account credentials (`base_url`,
`token`) live in `accounts.json`. Deployment note: toolbox UFW is deny-outgoing,
so `172.16.1.5:32400/tcp` must be allowed outbound for this job (same as the
`172.16.1.5:22` allow already on teletraan-1).

## Throwback Thursday

`recordkeeper throwback` builds a weekly Spotify playlist of tracks the account
hasn't played in `--since-months` (default 6) and has played at least 3 times
(drawn from the Last.fm scrobble history; candidates come from the *Last.fm*
account, the playlist is written to the *Spotify* account). Each candidate is
resolved to a Spotify URI via search (best-effort, top result; tracks not on
Spotify are skipped). Preview (default) lists the candidates; `--apply` creates
the playlist on first run and replaces its contents each week. The playlist id
is persisted in `data/throwback-playlist-<u>.json` (gitignored) — do NOT re-derive
it via `current_user_playlists`, which the snapshot job exhausts.

Post-Feb-2026 Spotify endpoints: `POST /me/playlists` (create) and
`PUT /playlists/{id}/items` (replace) — Spotipy's `user_playlist_create` /
`playlist_replace_items` still call the retired `/users/{id}/playlists` and
`/playlists/{id}/tracks` and return 403, so throwback calls the new paths
directly via `client._post` / `client._put`. The Spotify client is constructed
with `status_retries=0` so a 429 raises immediately instead of retrying/sleeping
through Spotify's extended rate limit. `recordkeeper-throwback.timer` runs
`--apply` every Thursday at 12:00 UTC. Scopes include `playlist-modify-private`
and `playlist-modify-public`.

## Vinyl scrobble

`recordkeeper vinyl-sync` detects albums newly added to the Plex "Music" section
(the vinyls directory), estimates a listening session ending at the album's
`added_at` (track *i* starts at `added_at - (total - cumulative_before_i)`), and
scrobbles each track via `network.scrobble`. Timestamps are explicitly
estimates. Albums older than Last.fm's 14-day scrobble window are baselined
(`status='skipped'`), never scrobbled with a misleading time. Preview (default)
reports the detection without writing; `--apply` scrobbles `new` imports, marking
per-track progress in `vinyl_import_tracks.scrobbled` and the write in
`sync_ledger` (`task_type='vinyl_scrobble'`, per-account unique). The first run
baselined all 250 existing albums (historical). `recordkeeper-vinyl-sync.timer`
runs `--apply` daily at 04:00 UTC.

## Support these artists

`recordkeeper support-artists` ranks artists by total Last.fm plays and excludes
those already owned on vinyl (case-insensitive match against `plex_items` rows of
`entity_type='artist'`). The top un-owned artists are stored in `recommendations`
as `status='suggested'` (score = play count, `reason` = "N plays, last YYYY-MM-DD"),
resolving each artist name into the canonical `artists` table. Preview (default)
lists the shortlist; `--apply` upserts (idempotent per `(account_id, artist_id)`).
Plex presence is an exclusion signal only — the `status` field carries manual
overrides (`purchased`, `snoozed`, `dismissed`, etc.).
`recordkeeper support-artists.timer` refreshes daily at 04:30 UTC. Note: `plex_items.artist_name`
must be populated for `entity_type='artist'` rows (the ownership join keys on it).

## Web UI

`recordkeeper serve` runs a read-only FastAPI + Jinja2 web UI (dense UniFi-style:
dark sidebar, compact tables, status dots, monospace values). Pages: dashboard
(counts + top artists + recent scrobbles), scrobbles, playlists, support-artists,
vinyl. Each route opens a short-lived Postgres connection (reads `DATABASE_URL`).
Deployment: `recordkeeper-web.service` binds `0.0.0.0:8000`; toolbox UFW needs an
inbound `172.16.0.0/16 → 8000/tcp` allow for LAN access. A `Dockerfile` + the
`compose.yaml` `web` service containerize the same app (DATABASE_URL points at the
`db` service hostname on the compose network).

