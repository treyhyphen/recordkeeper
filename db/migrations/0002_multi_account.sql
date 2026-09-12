-- Multi-account support: connect multiple accounts per platform.
--
-- Each account-scoped table gains an `account_id` foreign key, and uniqueness
-- keys that were globally scoped (one URI per user, one playlist ID per user,
-- etc.) are re-scoped per account so two family members' data never collide.

CREATE TABLE accounts (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform     TEXT NOT NULL,          -- lastfm | spotify | plex
    username     TEXT NOT NULL,          -- provider username (label for plex)
    display_name TEXT,
    enabled      BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform, username)
);

-- scrobbles: link each play to an account; dedupe per account.
ALTER TABLE scrobbles ADD COLUMN account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;
ALTER TABLE scrobbles DROP CONSTRAINT IF EXISTS scrobbles_source_artist_name_track_name_played_at_key;
CREATE UNIQUE INDEX scrobbles_account_play_key
    ON scrobbles (account_id, source, artist_name, track_name, played_at);
CREATE INDEX scrobbles_account_idx ON scrobbles (account_id);

-- loved_tracks: a track can be loved by more than one account.
ALTER TABLE loved_tracks ADD COLUMN account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;
ALTER TABLE loved_tracks DROP CONSTRAINT IF EXISTS loved_tracks_artist_name_track_name_key;
CREATE UNIQUE INDEX loved_tracks_account_track_key
    ON loved_tracks (account_id, artist_name, track_name);

-- saved_tracks: a Spotify URI may be liked by multiple accounts.
ALTER TABLE saved_tracks ADD COLUMN account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;
ALTER TABLE saved_tracks DROP CONSTRAINT IF EXISTS saved_tracks_provider_uri_key;
CREATE UNIQUE INDEX saved_tracks_account_uri_key ON saved_tracks (account_id, provider_uri);

-- playlists: uniqueness is per account.
ALTER TABLE playlists ADD COLUMN account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;
ALTER TABLE playlists DROP CONSTRAINT IF EXISTS playlists_provider_provider_playlist_id_key;
CREATE UNIQUE INDEX playlists_account_provider_key
    ON playlists (account_id, provider, provider_playlist_id);

-- sync_ledger: record which account a cross-service write targets.
ALTER TABLE sync_ledger ADD COLUMN account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;
