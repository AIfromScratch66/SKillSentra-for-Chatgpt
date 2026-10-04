ALTER TABLE ai_runs ADD COLUMN usage_receipt_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ai_runs ADD COLUMN provider_snapshot TEXT NOT NULL DEFAULT '';
ALTER TABLE ai_runs ADD COLUMN reserved_cost REAL NOT NULL DEFAULT 0;
ALTER TABLE ai_runs ADD COLUMN fallback_used INTEGER NOT NULL DEFAULT 0;

UPDATE ai_runs SET provider_snapshot = COALESCE(
    (SELECT provider FROM ai_connections WHERE ai_connections.id = ai_runs.connection_id), ''
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_orchestration
ON ai_runs(project_id, orchestration_id, created_at);
