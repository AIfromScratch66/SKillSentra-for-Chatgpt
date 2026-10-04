CREATE TABLE IF NOT EXISTS oauth_identities (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('google', 'chatgpt')),
    provider_subject TEXT NOT NULL,
    email_at_link TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, provider_subject),
    UNIQUE(provider, user_id)
);

CREATE INDEX IF NOT EXISTS idx_oauth_identities_user
ON oauth_identities(user_id);

CREATE TABLE IF NOT EXISTS oauth_login_states (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('google', 'chatgpt')),
    state_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_login_states_expiry
ON oauth_login_states(expires_at, consumed_at);
