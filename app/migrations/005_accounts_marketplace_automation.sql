PRAGMA foreign_keys = ON;

ALTER TABLE projects ADD COLUMN owner_user_id TEXT;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    normalized_email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'restricted', 'disabled')),
    email_verified INTEGER NOT NULL DEFAULT 0 CHECK (email_verified IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    roles_json TEXT NOT NULL DEFAULT '["creator","buyer"]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'revoked')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry
ON user_sessions(token_hash, expires_at);

CREATE TABLE IF NOT EXISTS scan_targets (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('github', 'website')),
    url TEXT NOT NULL,
    requested_ref TEXT NOT NULL DEFAULT 'main',
    interval_minutes INTEGER NOT NULL DEFAULT 1440 CHECK (interval_minutes BETWEEN 15 AND 43200),
    auto_enabled INTEGER NOT NULL DEFAULT 1 CHECK (auto_enabled IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'running', 'warning', 'error', 'paused')),
    last_run_at TEXT,
    next_run_at TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, url)
);

CREATE INDEX IF NOT EXISTS idx_scan_targets_due
ON scan_targets(auto_enabled, next_run_at);

CREATE TABLE IF NOT EXISTS scan_runs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES scan_targets(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    discovered_count INTEGER NOT NULL DEFAULT 0 CHECK (discovered_count >= 0),
    scanned_count INTEGER NOT NULL DEFAULT 0 CHECK (scanned_count >= 0),
    blocked_count INTEGER NOT NULL DEFAULT 0 CHECK (blocked_count >= 0),
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_target_started
ON scan_runs(target_id, started_at DESC);

CREATE TABLE IF NOT EXISTS marketplace_publications (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    publisher_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    skill_version_id TEXT REFERENCES skill_versions(id) ON DELETE SET NULL,
    snapshot_id TEXT REFERENCES repository_snapshots(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    summary TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'productivity',
    canonicalization_version TEXT NOT NULL DEFAULT 'artifact-canon-v1',
    digest_algorithm TEXT NOT NULL DEFAULT 'sha256',
    artifact_digest TEXT NOT NULL,
    pricing_type TEXT NOT NULL DEFAULT 'free' CHECK (pricing_type IN ('free', 'paid')),
    price_minor INTEGER NOT NULL DEFAULT 0 CHECK (price_minor >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    platform_fee_bps INTEGER NOT NULL DEFAULT 1500 CHECK (platform_fee_bps BETWEEN 0 AND 10000),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'unlisted', 'blocked')),
    average_rating REAL NOT NULL DEFAULT 0 CHECK (average_rating BETWEEN 0 AND 5),
    review_count INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
    purchase_count INTEGER NOT NULL DEFAULT 0 CHECK (purchase_count >= 0),
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, slug),
    CHECK ((pricing_type = 'free' AND price_minor = 0) OR (pricing_type = 'paid' AND price_minor > 0))
);

CREATE INDEX IF NOT EXISTS idx_publications_ranking
ON marketplace_publications(status, average_rating DESC, review_count DESC, purchase_count DESC);

CREATE TABLE IF NOT EXISTS marketplace_reviews (
    id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES marketplace_publications(id) ON DELETE CASCADE,
    reviewer_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'hidden', 'flagged')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(publication_id, reviewer_user_id)
);

CREATE TABLE IF NOT EXISTS marketplace_orders (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    buyer_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    publication_id TEXT NOT NULL REFERENCES marketplace_publications(id) ON DELETE RESTRICT,
    publisher_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    platform_fee_minor INTEGER NOT NULL CHECK (platform_fee_minor >= 0),
    publisher_amount_minor INTEGER NOT NULL CHECK (publisher_amount_minor >= 0),
    currency TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'sandbox' CHECK (mode = 'sandbox'),
    status TEXT NOT NULL DEFAULT 'paid' CHECK (status IN ('paid', 'refunded', 'cancelled')),
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_orders_publisher
ON marketplace_orders(publisher_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_skill_library (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    publication_id TEXT NOT NULL REFERENCES marketplace_publications(id) ON DELETE CASCADE,
    order_id TEXT REFERENCES marketplace_orders(id) ON DELETE SET NULL,
    artifact_digest TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    UNIQUE(user_id, publication_id)
);

CREATE INDEX IF NOT EXISTS idx_projects_owner_updated
ON projects(owner_user_id, updated_at DESC);
