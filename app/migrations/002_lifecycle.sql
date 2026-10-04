ALTER TABLE projects ADD COLUMN source_id TEXT;
ALTER TABLE projects ADD COLUMN active_version_id TEXT;

ALTER TABLE skill_versions ADD COLUMN canonicalization_version TEXT NOT NULL DEFAULT 'artifact-canon-v1';
ALTER TABLE skill_versions ADD COLUMN digest_algorithm TEXT NOT NULL DEFAULT 'sha256';
ALTER TABLE skill_versions ADD COLUMN artifact_path TEXT NOT NULL DEFAULT '';
ALTER TABLE skill_versions ADD COLUMN package_path TEXT NOT NULL DEFAULT '';
ALTER TABLE skill_versions ADD COLUMN base_version_id TEXT;
ALTER TABLE skill_versions ADD COLUMN change_summary TEXT NOT NULL DEFAULT '';

ALTER TABLE ai_runs ADD COLUMN orchestration_id TEXT NOT NULL DEFAULT '';
ALTER TABLE ai_runs ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD';
ALTER TABLE ai_runs ADD COLUMN pricebook_version TEXT NOT NULL DEFAULT 'local-pricebook-v1';
ALTER TABLE ai_runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS skill_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL DEFAULT 'local',
    version TEXT NOT NULL DEFAULT 'unversioned',
    artifact_digest TEXT NOT NULL,
    canonicalization_version TEXT NOT NULL DEFAULT 'artifact-canon-v1',
    digest_algorithm TEXT NOT NULL DEFAULT 'sha256',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version_id TEXT REFERENCES skill_versions(id) ON DELETE SET NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    findings_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_validation_project_created
ON validation_runs(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS update_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 1,
    frequency TEXT NOT NULL DEFAULT 'weekly',
    sources_json TEXT NOT NULL DEFAULT '["upstream","security"]',
    strategy TEXT NOT NULL DEFAULT 'review',
    base_digest TEXT NOT NULL DEFAULT '',
    base_manifest_json TEXT NOT NULL DEFAULT '{}',
    last_checked_at TEXT,
    next_check_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS update_checks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    base_digest TEXT NOT NULL DEFAULT '',
    local_digest TEXT NOT NULL DEFAULT '',
    upstream_digest TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    diff_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_update_checks_project_created
ON update_checks(project_id, created_at DESC);
