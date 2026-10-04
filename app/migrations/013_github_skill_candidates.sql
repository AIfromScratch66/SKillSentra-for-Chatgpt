CREATE TABLE IF NOT EXISTS github_skill_candidates (
    id TEXT PRIMARY KEY,
    repository TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    ref TEXT,
    github_stars INTEGER NOT NULL CHECK (github_stars >= 0),
    source_rank INTEGER NOT NULL CHECK (source_rank BETWEEN 1 AND 100),
    source_query TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status = 'unverified_candidate'),
    requires_static_scan INTEGER NOT NULL DEFAULT 1 CHECK (requires_static_scan IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_github_skill_candidates_rank
ON github_skill_candidates(source_rank, github_stars DESC);
