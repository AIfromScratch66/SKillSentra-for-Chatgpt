ALTER TABLE expert_reviews
ADD COLUMN artifact_version_id TEXT REFERENCES skill_versions(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_expert_reviews_artifact_version
ON expert_reviews(artifact_version_id);

CREATE TRIGGER IF NOT EXISTS trg_expert_review_artifact_binding_insert
BEFORE INSERT ON expert_reviews
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1
    FROM skill_versions
    WHERE id = NEW.artifact_version_id
      AND project_id = NEW.project_id
      AND artifact_digest = NEW.artifact_digest
)
BEGIN
    SELECT RAISE(ABORT, 'expert_review_artifact_binding_invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_expert_review_artifact_binding_update
BEFORE UPDATE OF project_id, artifact_version_id, artifact_digest ON expert_reviews
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1
    FROM skill_versions
    WHERE id = NEW.artifact_version_id
      AND project_id = NEW.project_id
      AND artifact_digest = NEW.artifact_digest
)
BEGIN
    SELECT RAISE(ABORT, 'expert_review_artifact_binding_invalid');
END;
