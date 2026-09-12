-- Recordkeeper schema — version 1 (PostgreSQL 16)
--
-- Design principles:
--   * Canonical entities (artists/albums/tracks) hold provider-agnostic identity.
--   * external_identifiers maps provider IDs (Spotify, MusicBrainz, Plex) onto
--     canonical entities and is the backbone of cross-source matching.
--   * scrobbles is append-only and preserves repeat listens (never deduped by
--     title alone); a unique key on (source, artist, track, played_at) prevents
--     re-importing the same play.
--   * Provider payloads are kept as JSONB alongside typed columns so the raw
--     record survives while common fields stay queryable.

-- =====================================================================
-- Identity & catalog
-- =====================================================================

CREATE TABLE artists (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT NOT NULL,
    sort_name  TEXT,
    mbid       UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX artists_name_key ON artists (lower(name));

CREATE TABLE albums (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title        TEXT NOT NULL,
    mbid         UUID,
    album_type   TEXT,                 -- album | single | compilation | ep
    release_date DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE album_artists (
    album_id  BIGINT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    artist_id BIGINT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    position  INT NOT NULL DEFAULT 0,
    PRIMARY KEY (album_id, artist_id)
);

CREATE TABLE tracks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title        TEXT NOT NULL,
    album_id     BIGINT REFERENCES albums(id) ON DELETE SET NULL,
    duration_ms  INT,
    track_number INT,
    disc_number  INT,
    isrc         TEXT,
    mbid         UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE track_artists (
    track_id  BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    artist_id BIGINT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    position  INT NOT NULL DEFAULT 0,
    role      TEXT NOT NULL DEFAULT 'primary',  -- primary | featured | remixer
    PRIMARY KEY (track_id, artist_id)
);

-- One provider's identifier mapped to a canonical entity (matching backbone).
CREATE TABLE external_identifiers (
    provider    TEXT NOT NULL,        -- spotify | musicbrainz | plex | isrc
    external_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,        -- artist | album | track
    entity_id   BIGINT NOT NULL,
    PRIMARY KEY (provider, external_id)
);
CREATE INDEX external_identifiers_entity_idx
    ON external_identifiers (entity_type, entity_id);

CREATE TABLE genres (
    id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE artist_genres (
    artist_id BIGINT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    genre_id  BIGINT NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    weight    NUMERIC NOT NULL DEFAULT 1.0,
    PRIMARY KEY (artist_id, genre_id)
);

CREATE TABLE track_genres (
    track_id BIGINT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    genre_id BIGINT NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    weight   NUMERIC NOT NULL DEFAULT 1.0,
    PRIMARY KEY (track_id, genre_id)
);

-- =====================================================================
-- Listening history (scrobbles) & loved tracks
-- =====================================================================

CREATE TABLE scrobbles (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artist_id   BIGINT REFERENCES artists(id) ON DELETE SET NULL,
    track_id    BIGINT REFERENCES tracks(id) ON DELETE SET NULL,
    album_id    BIGINT REFERENCES albums(id) ON DELETE SET NULL,
    played_at   TIMESTAMPTZ NOT NULL,
    source      TEXT NOT NULL,        -- lastfm | spotify | vinyl | manual
    artist_name TEXT NOT NULL,        -- as reported at scrobble time
    track_name  TEXT NOT NULL,
    album_name  TEXT,
    mbid        UUID,
    loved       BOOLEAN NOT NULL DEFAULT false,
    raw         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, artist_name, track_name, played_at)
);
CREATE INDEX scrobbles_played_at_idx ON scrobbles (played_at);
CREATE INDEX scrobbles_track_idx ON scrobbles (track_id);
CREATE INDEX scrobbles_artist_idx ON scrobbles (artist_id);
CREATE INDEX scrobbles_source_idx ON scrobbles (source);

-- Last.fm "loved tracks" (distinct from scrobbles: a track can be loved
-- without a recent play).
CREATE TABLE loved_tracks (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artist_name TEXT NOT NULL,
    track_name  TEXT NOT NULL,
    artist_id   BIGINT REFERENCES artists(id) ON DELETE SET NULL,
    track_id    BIGINT REFERENCES tracks(id) ON DELETE SET NULL,
    mbid        UUID,
    loved_at    TIMESTAMPTZ,
    raw         JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (artist_name, track_name)
);

-- =====================================================================
-- Spotify playlists, snapshots, and saved (liked) tracks
-- =====================================================================

CREATE TABLE playlists (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider             TEXT NOT NULL DEFAULT 'spotify',
    provider_playlist_id TEXT NOT NULL,
    name                 TEXT NOT NULL,
    description          TEXT,
    owner_id             TEXT,
    owner_name           TEXT,
    is_public            BOOLEAN,
    is_collaborative     BOOLEAN,
    snapshot_id          TEXT,
    raw                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_playlist_id)
);

-- Versioned backup captures (one row per capture, items queryable below).
CREATE TABLE playlist_snapshots (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    playlist_id BIGINT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    snapshot_id TEXT,
    track_count INT NOT NULL DEFAULT 0,
    raw         JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX playlist_snapshots_playlist_idx
    ON playlist_snapshots (playlist_id, captured_at DESC);

CREATE TABLE playlist_snapshot_items (
    snapshot_id  BIGINT NOT NULL REFERENCES playlist_snapshots(id) ON DELETE CASCADE,
    position     INT NOT NULL,
    track_id     BIGINT REFERENCES tracks(id) ON DELETE SET NULL,
    provider_uri TEXT,                -- spotify:track:...
    track_name   TEXT NOT NULL,
    artist_names TEXT[] NOT NULL DEFAULT '{}',
    album_name   TEXT,
    added_at     TIMESTAMPTZ,
    raw          JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, position)
);

-- Spotify saved ("liked") tracks — the source for the likes→loves sync.
CREATE TABLE saved_tracks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    track_id     BIGINT REFERENCES tracks(id) ON DELETE SET NULL,
    provider_uri TEXT NOT NULL UNIQUE,
    track_name   TEXT NOT NULL,
    artist_names TEXT[] NOT NULL DEFAULT '{}',
    album_name   TEXT,
    saved_at     TIMESTAMPTZ,
    raw          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- Plex inventory (what is already owned)
-- =====================================================================

CREATE TABLE plex_items (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type     TEXT NOT NULL,     -- artist | album | track
    title           TEXT NOT NULL,
    artist_name     TEXT,
    album_name      TEXT,
    plex_rating_key TEXT NOT NULL UNIQUE,
    plex_guid       TEXT,
    mbid            UUID,
    library_section TEXT,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw             JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX plex_items_entity_idx ON plex_items (entity_type, lower(title));

-- =====================================================================
-- Vinyl imports (source for the vinyl→scrobble automation)
-- =====================================================================

CREATE TABLE vinyl_imports (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    album_id       BIGINT REFERENCES albums(id) ON DELETE SET NULL,
    directory_path TEXT NOT NULL,
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    status         TEXT NOT NULL DEFAULT 'new',  -- new | reviewing | ready | scrobbled | skipped | error
    track_count    INT NOT NULL DEFAULT 0,
    raw            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (directory_path)
);

CREATE TABLE vinyl_import_tracks (
    import_id           BIGINT NOT NULL REFERENCES vinyl_imports(id) ON DELETE CASCADE,
    position            INT NOT NULL,
    track_id            BIGINT REFERENCES tracks(id) ON DELETE SET NULL,
    title               TEXT NOT NULL,
    duration_ms         INT,
    estimated_played_at TIMESTAMPTZ,
    scrobbled           BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (import_id, position)
);

-- =====================================================================
-- Sync ledger (idempotent, auditable cross-service writes)
-- =====================================================================

CREATE TABLE sync_ledger (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_type       TEXT NOT NULL,     -- likes_to_loves | vinyl_scrobble
    source_key      TEXT NOT NULL,     -- e.g. spotify track URI
    target_key      TEXT NOT NULL,     -- e.g. last.fm artist + track
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | synced | failed | skipped
    attempts        INT NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    last_error      TEXT,
    synced_at       TIMESTAMPTZ,
    UNIQUE (task_type, source_key)
);

-- =====================================================================
-- Recommendations (support-these-artists shortlist)
-- =====================================================================

CREATE TABLE recommendations (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    artist_id     BIGINT REFERENCES artists(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL,       -- human-readable justification
    score         NUMERIC NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'suggested',  -- suggested | wishlist | purchased | snoozed | dismissed
    snoozed_until TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX recommendations_status_idx ON recommendations (status);

-- =====================================================================
-- Jobs & migration bookkeeping
-- =====================================================================

CREATE TABLE jobs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_type    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',  -- running | complete | failed | abandoned
    params      JSONB NOT NULL DEFAULT '{}'::jsonb,
    result      JSONB,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
