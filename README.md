# Recordkeeper
Your music, accounted for.

## Current release
Read-only Last.fm raw-history backup CLI, SQLite checkpoints, JSONL and CSV exports.
No UI, scheduled jobs, Spotify/Plex integration, or Last.fm writes yet.

## Run
Requires Python 3.11+, uv, and keepassxc-cli on Linux.

```sh
uv sync --locked
uv run recordkeeper backup --user treyhyphen
uv run recordkeeper status
uv run recordkeeper export 3
uv run pytest -q
uv run ruff check .
```
The API key is read from the local KeePassXC entry
`Recordkeeper/Last.fm API key`. No secret is stored in this repository.
Data defaults to `./data`; use `recordkeeper --data /absolute/local/path ...`.
Do not put the SQLite working database on an SMB/NFS share.

A backup resumes the most recent running snapshot for the specified username.
Each run freezes its upper timestamp boundary. Every page and checkpoint commit
atomically. Identical track occurrences are preserved, not deduplicated by title.
Currently-playing entries are excluded. A provider count change stops the run;
`recordkeeper abandon` marks running snapshots abandoned without deleting pages.
Use `backup --user USER --cutoff UNIX_SECONDS` for an older stable upper boundary.
The cutoff applies only to a NEW snapshot. Final count mismatch is incomplete,
never success. Fixed timestamps and matching totals cannot detect every possible
same-count history edit: periodic reconciliation remains necessary.

Exports: `data/exports/run-ID/pages.jsonl` preserves original API page payloads;
`plays.csv` holds one row per completed play. Treat CSV fields as untrusted when
opening in spreadsheet software (import text fields without formula evaluation).
Exports are written to temporary files then renamed. These contain private history.
The database retains the username, cutoff, expected count and completion status.
Make an off-host copy of exports and a SQLite online backup before relying on this
as disaster recovery. Off-host replication and retention are NOT configured yet.

## Architecture and library decision
Use one modular application and thin provider adapters. pylast is installed and
locked for future Last.fm account operations. Its public history iterator does not
expose raw page responses or resumable page control. The archival adapter therefore
uses the documented JSON read endpoint through Python urllib rather than private
pylast APIs. OAuth/signature implementation remains out of scope.

## Delivery order
1. History archive CLI (current); harden retries and timestamp-window reconciliation.
2. Authenticated web backup center, durable worker/scheduler, health monitoring,
   portable container deployment and off-host recovery verification.
3. Spotify snapshots and Plex inventory/matching.
4. New Spotify likes to Last.fm loves (preview first).
5. Playlist overlap and support-artists shortlist.
6. Vinyl-only import scrobbling (baseline existing files; preview first).

Never automatically merge playlists, purchase music, or scrobble digital downloads.
