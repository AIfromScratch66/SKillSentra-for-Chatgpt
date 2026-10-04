CREATE TRIGGER IF NOT EXISTS trg_expert_review_external_evidence_insert
BEFORE INSERT ON expert_reviews
FOR EACH ROW
WHEN NEW.decision = 'READY_FOR_HUMAN_DECISION'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.real_host_connection.status'), 'unknown')) = 'pass'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.real_task_evidence.status'), 'unknown')) = 'pass'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.manual_accessibility.status'), 'unknown')) = 'pass'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.human_release_authorization.status'), 'unknown')) = 'pass'
BEGIN
    SELECT RAISE(ABORT, 'expert_external_evidence_registry_unavailable');
END;

CREATE TRIGGER IF NOT EXISTS trg_expert_review_external_evidence_update
BEFORE UPDATE OF decision, gates_json ON expert_reviews
FOR EACH ROW
WHEN NEW.decision = 'READY_FOR_HUMAN_DECISION'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.real_host_connection.status'), 'unknown')) = 'pass'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.real_task_evidence.status'), 'unknown')) = 'pass'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.manual_accessibility.status'), 'unknown')) = 'pass'
  OR LOWER(COALESCE(json_extract(NEW.gates_json, '$.human_release_authorization.status'), 'unknown')) = 'pass'
BEGIN
    SELECT RAISE(ABORT, 'expert_external_evidence_registry_unavailable');
END;
