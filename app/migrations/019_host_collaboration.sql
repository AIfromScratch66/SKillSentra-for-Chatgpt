CREATE TABLE IF NOT EXISTS host_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    route TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    context_hash TEXT NOT NULL,
    context_json TEXT NOT NULL,
    instruction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'proposed')),
    result_json TEXT,
    result_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_host_requests_project ON host_requests(project_id, created_at DESC);
