PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'team',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'restricted', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO tenants(id, name, slug, plan, status, created_at, updated_at)
VALUES ('ten_demo', 'SkillSentra Workspace', 'skillsentra-workspace', 'team', 'active',
        '2026-01-01T00:00:00.000+00:00', '2026-01-01T00:00:00.000+00:00');

ALTER TABLE projects ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'ten_demo';

CREATE INDEX IF NOT EXISTS idx_projects_tenant_updated
ON projects(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'github' CHECK (provider IN ('github', 'local-fixture')),
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private', 'internal')),
    default_branch TEXT NOT NULL DEFAULT 'main',
    installation_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'scanning', 'stale', 'blocked', 'error')),
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, provider, owner, name)
);

CREATE INDEX IF NOT EXISTS idx_repositories_tenant_updated
ON repositories(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    requested_ref TEXT NOT NULL,
    resolved_commit TEXT NOT NULL,
    canonicalization_version TEXT NOT NULL DEFAULT 'artifact-canon-v1',
    digest_algorithm TEXT NOT NULL DEFAULT 'sha256',
    artifact_digest TEXT NOT NULL,
    artifact_version_id TEXT REFERENCES skill_versions(id) ON DELETE SET NULL,
    source_id TEXT REFERENCES skill_sources(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('scanned', 'review_required', 'blocked', 'error')),
    file_count INTEGER NOT NULL DEFAULT 0 CHECK (file_count >= 0),
    total_bytes INTEGER NOT NULL DEFAULT 0 CHECK (total_bytes >= 0),
    scan_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, repository_id, resolved_commit, artifact_digest)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_tenant_created
ON repository_snapshots(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS evidence_attestations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL REFERENCES repository_snapshots(id) ON DELETE CASCADE,
    subject_digest TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass', 'warning', 'fail', 'unknown', 'revoked')),
    severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    engine TEXT NOT NULL,
    ruleset_version TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    valid_until TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_tenant_digest
ON evidence_attestations(tenant_id, subject_digest, created_at DESC);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('development', 'staging', 'production')),
    host_name TEXT NOT NULL,
    adapter TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'online' CHECK (status IN ('online', 'offline', 'restricted', 'disabled')),
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, name, environment)
);

CREATE INDEX IF NOT EXISTS idx_agents_tenant_updated
ON agents(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    snapshot_id TEXT REFERENCES repository_snapshots(id) ON DELETE SET NULL,
    artifact_version_id TEXT REFERENCES skill_versions(id) ON DELETE SET NULL,
    canonicalization_version TEXT NOT NULL DEFAULT 'artifact-canon-v1',
    digest_algorithm TEXT NOT NULL DEFAULT 'sha256',
    desired_digest TEXT NOT NULL,
    observed_digest TEXT NOT NULL DEFAULT '',
    policy_version TEXT NOT NULL,
    control_level TEXT NOT NULL DEFAULT 'observed' CHECK (control_level IN ('enforced', 'observed', 'declared', 'unsupported')),
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'queued', 'applied', 'observed', 'drifted', 'suspended', 'revoked', 'rolled_back')),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deployments_tenant_updated
ON deployments(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS revocations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    artifact_digest TEXT NOT NULL,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'high' CHECK (severity IN ('medium', 'high', 'critical')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_revocation
ON revocations(tenant_id, artifact_digest) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS entitlements (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    publisher_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    snapshot_id TEXT REFERENCES repository_snapshots(id) ON DELETE SET NULL,
    canonicalization_version TEXT NOT NULL DEFAULT 'artifact-canon-v1',
    digest_algorithm TEXT NOT NULL DEFAULT 'sha256',
    artifact_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'suspended', 'revoked')),
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entitlements_tenant_digest
ON entitlements(tenant_id, artifact_digest, status);

CREATE TABLE IF NOT EXISTS price_rules (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    metric TEXT NOT NULL,
    unit TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, version, metric, unit, currency)
);

CREATE TABLE IF NOT EXISTS share_rules (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    publisher_bps INTEGER NOT NULL CHECK (publisher_bps BETWEEN 0 AND 10000),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, version)
);

CREATE TABLE IF NOT EXISTS usage_receipts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_receipt_id TEXT NOT NULL,
    publisher_id TEXT NOT NULL,
    entitlement_id TEXT REFERENCES entitlements(id) ON DELETE SET NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL,
    canonicalization_version TEXT NOT NULL,
    digest_algorithm TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    metric TEXT NOT NULL,
    unit TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    policy_version TEXT NOT NULL,
    evidence_level TEXT NOT NULL CHECK (evidence_level IN ('E0', 'E1', 'E2', 'E3')),
    occurred_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('qualified', 'excluded')),
    exclusion_reason TEXT NOT NULL DEFAULT '',
    price_rule_id TEXT REFERENCES price_rules(id) ON DELETE SET NULL,
    share_rule_id TEXT REFERENCES share_rules(id) ON DELETE SET NULL,
    gross_amount_minor INTEGER NOT NULL DEFAULT 0,
    publisher_amount_minor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, external_receipt_id)
);

CREATE INDEX IF NOT EXISTS idx_receipts_tenant_created
ON usage_receipts(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS journal_transactions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    description TEXT NOT NULL,
    reversal_of_id TEXT REFERENCES journal_transactions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, source_type, source_id)
);

CREATE TABLE IF NOT EXISTS journal_lines (
    id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES journal_transactions(id) ON DELETE RESTRICT,
    account_code TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('debit', 'credit')),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    party_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_lines_transaction
ON journal_lines(transaction_id);

CREATE TABLE IF NOT EXISTS settlement_statements (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    publisher_id TEXT NOT NULL,
    cycle_start TEXT NOT NULL,
    cycle_end TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'issued' CHECK (status IN ('draft', 'issued', 'closed', 'adjusted')),
    total_gross_minor INTEGER NOT NULL,
    total_payable_minor INTEGER NOT NULL,
    line_items_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, publisher_id, cycle_start, cycle_end, version)
);

CREATE TABLE IF NOT EXISTS payout_intents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    statement_id TEXT NOT NULL REFERENCES settlement_statements(id) ON DELETE RESTRICT,
    idempotency_key TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'skillsentra-sandbox',
    provider_reference TEXT NOT NULL DEFAULT '',
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    currency TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'sandbox' CHECK (mode = 'sandbox'),
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'paid', 'failed_retryable', 'failed_final', 'reversed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS platform_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    previous_hash TEXT NOT NULL DEFAULT '',
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_platform_audit_tenant_id
ON platform_audit_events(tenant_id, id DESC);
