-- Re-scope sync_ledger uniqueness per account so two family members syncing the
-- same Spotify URI (likes→loves) never collide. The global (task_type, source_key)
-- key is replaced by an account-scoped unique index.
ALTER TABLE sync_ledger DROP CONSTRAINT IF EXISTS sync_ledger_task_type_source_key_key;
CREATE UNIQUE INDEX sync_ledger_account_task_source_key
    ON sync_ledger (account_id, task_type, source_key);
