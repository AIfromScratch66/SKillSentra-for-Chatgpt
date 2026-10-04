CREATE TABLE IF NOT EXISTS expert_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    framework_version TEXT NOT NULL,
    review_kind TEXT NOT NULL CHECK (review_kind = 'internal_simulation'),
    reviewer_label TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    raw_score REAL NOT NULL,
    capped_score REAL NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('NO_GO', 'ITERATE', 'READY_FOR_HUMAN_DECISION')),
    evidence_ceiling TEXT NOT NULL,
    evaluation_world_json TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    gates_json TEXT NOT NULL,
    findings_json TEXT NOT NULL DEFAULT '[]',
    proposed_changes_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expert_reviews_project_created
ON expert_reviews(project_id, created_at DESC);
