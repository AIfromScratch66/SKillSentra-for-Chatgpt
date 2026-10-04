INSERT OR IGNORE INTO update_policies(
    project_id, enabled, frequency, sources_json, strategy, base_digest,
    base_manifest_json, next_check_at, created_at, updated_at
)
SELECT
    id, 1, 'weekly', '["upstream","security"]', 'review', '',
    '{}', NULL, created_at, updated_at
FROM projects;
