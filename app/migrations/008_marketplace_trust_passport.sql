PRAGMA foreign_keys = ON;

ALTER TABLE marketplace_publications ADD COLUMN license_id TEXT NOT NULL DEFAULT 'unspecified';
ALTER TABLE marketplace_publications ADD COLUMN compatibility_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE marketplace_publications ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE marketplace_publications ADD COLUMN data_policy TEXT NOT NULL DEFAULT 'not_declared';
ALTER TABLE marketplace_publications ADD COLUMN support_policy TEXT NOT NULL DEFAULT 'community';
ALTER TABLE marketplace_publications ADD COLUMN curation_status TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE marketplace_publications ADD COLUMN trust_evidence_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_publications_curation
ON marketplace_publications(status, curation_status, published_at DESC);
