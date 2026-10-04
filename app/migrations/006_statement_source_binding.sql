ALTER TABLE usage_receipts ADD COLUMN statement_id TEXT REFERENCES settlement_statements(id) ON DELETE SET NULL;

ALTER TABLE marketplace_orders ADD COLUMN statement_id TEXT REFERENCES settlement_statements(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_usage_receipts_unsettled
ON usage_receipts(tenant_id, publisher_id, status, statement_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_marketplace_orders_unsettled
ON marketplace_orders(publisher_user_id, status, statement_id, created_at);
