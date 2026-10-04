CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_skill_version_unique
ON marketplace_publications(skill_version_id)
WHERE skill_version_id IS NOT NULL;
