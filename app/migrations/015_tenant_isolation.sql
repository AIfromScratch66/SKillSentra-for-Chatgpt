PRAGMA foreign_keys = OFF;

-- Rebuild instead of ALTER ADD COLUMN so existing installations receive a
-- real tenant foreign key and all dependent tables keep their original
-- ai_connections(id) reference.
CREATE TABLE ai_connections_v2 (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('mock', 'openai', 'openai-compatible')),
    display_name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    credential_env TEXT NOT NULL,
    credential_last4 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'configured',
    last_error TEXT NOT NULL DEFAULT '',
    model_catalog_json TEXT NOT NULL DEFAULT '[]',
    last_tested_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO ai_connections_v2(
    id, tenant_id, provider, display_name, base_url, credential_env,
    credential_last4, status, last_error, model_catalog_json,
    last_tested_at, created_at, updated_at
)
SELECT
    id, 'ten_demo', provider, display_name, base_url, credential_env,
    credential_last4, status, last_error, model_catalog_json,
    last_tested_at, created_at, updated_at
FROM ai_connections;

DROP TABLE ai_connections;
ALTER TABLE ai_connections_v2 RENAME TO ai_connections;

CREATE INDEX idx_ai_connections_tenant_updated
ON ai_connections(tenant_id, updated_at DESC);

-- The original table made root_path globally unique. Rebuilding changes that
-- boundary to tenant_id + root_path so two workspaces may independently own a
-- path without sharing a database record.
CREATE TABLE skill_sources_v2 (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'local',
    version TEXT NOT NULL DEFAULT 'unversioned',
    artifact_digest TEXT NOT NULL,
    canonicalization_version TEXT NOT NULL DEFAULT 'artifact-canon-v1',
    digest_algorithm TEXT NOT NULL DEFAULT 'sha256',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, root_path)
);

INSERT INTO skill_sources_v2(
    id, tenant_id, name, root_path, origin, version, artifact_digest,
    canonicalization_version, digest_algorithm, manifest_json,
    metadata_json, status, created_at, updated_at
)
SELECT
    id, 'ten_demo', name, root_path, origin, version, artifact_digest,
    canonicalization_version, digest_algorithm, manifest_json,
    metadata_json, status, created_at, updated_at
FROM skill_sources;

DROP TABLE skill_sources;
ALTER TABLE skill_sources_v2 RENAME TO skill_sources;

CREATE INDEX idx_skill_sources_tenant_status_name
ON skill_sources(tenant_id, status, name);

PRAGMA foreign_keys = ON;
