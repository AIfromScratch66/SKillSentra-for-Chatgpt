PRAGMA foreign_keys = ON;

-- One-time, hashed account action tokens.  Raw tokens are never persisted;
-- delivery adapters receive them only in memory after the transaction commits.
CREATE TABLE IF NOT EXISTS account_action_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_type TEXT NOT NULL CHECK (token_type IN ('email_verification', 'password_reset')),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_account_action_tokens_lookup
ON account_action_tokens(token_type, token_hash, expires_at, consumed_at);

CREATE INDEX IF NOT EXISTS idx_account_action_tokens_user
ON account_action_tokens(user_id, token_type, created_at DESC);
