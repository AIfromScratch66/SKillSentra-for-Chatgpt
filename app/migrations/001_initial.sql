PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    route TEXT NOT NULL CHECK (route IN ('template', 'existing')),
    status TEXT NOT NULL DEFAULT 'draft',
    current_step INTEGER NOT NULL DEFAULT 0 CHECK (current_step >= 0),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS step_states (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    route TEXT NOT NULL CHECK (route IN ('template', 'existing')),
    step_index INTEGER NOT NULL CHECK (step_index >= 0),
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, route, step_index)
);

CREATE TABLE IF NOT EXISTS ai_connections (
    id TEXT PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS ai_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 1,
    mode TEXT NOT NULL DEFAULT 'suggest' CHECK (mode IN ('suggest', 'auto', 'manual')),
    connection_id TEXT REFERENCES ai_connections(id) ON DELETE SET NULL,
    creator_model TEXT NOT NULL DEFAULT 'mock-balanced',
    evaluator_model TEXT NOT NULL DEFAULT 'mock-evaluator',
    safety_model TEXT NOT NULL DEFAULT 'mock-safety',
    fallback_enabled INTEGER NOT NULL DEFAULT 1,
    redact_enabled INTEGER NOT NULL DEFAULT 1,
    external_files_enabled INTEGER NOT NULL DEFAULT 0,
    daily_budget REAL NOT NULL DEFAULT 20 CHECK (daily_budget >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    connection_id TEXT REFERENCES ai_connections(id) ON DELETE SET NULL,
    route TEXT NOT NULL CHECK (route IN ('template', 'existing')),
    step_index INTEGER NOT NULL CHECK (step_index >= 0),
    role TEXT NOT NULL CHECK (role IN ('creator', 'evaluator', 'safety')),
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    input_manifest_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    provider_request_id TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_project_created
ON ai_runs(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS skill_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_project_created
ON audit_events(project_id, created_at DESC);

