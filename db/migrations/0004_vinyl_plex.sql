-- Adapt vinyl import tracking to Plex-based detection. The vinyls directory is
-- the Plex "Music" library, so imports are keyed on Plex's ratingKey instead of
-- a filesystem path (which we still capture when Plex exposes it).
ALTER TABLE vinyl_imports ADD COLUMN plex_rating_key TEXT;
CREATE UNIQUE INDEX vinyl_imports_plex_key ON vinyl_imports (plex_rating_key);
ALTER TABLE vinyl_imports ALTER COLUMN directory_path DROP NOT NULL;
