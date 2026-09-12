-- Scope the support-artists shortlist per account and make it idempotent.
ALTER TABLE recommendations ADD COLUMN account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX recommendations_account_artist_key
    ON recommendations (account_id, artist_id);
