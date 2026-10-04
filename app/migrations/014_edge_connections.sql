PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS host_connections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    host_type TEXT NOT NULL CHECK (host_type IN ('chatgpt', 'codex', 'other-agent')),
    transport TEXT NOT NULL CHECK (transport IN ('mcp', 'host-app-api')),
    external_ref TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered', 'online', 'offline', 'restricted', 'disabled')),
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, host_type, external_ref)
);

CREATE INDEX IF NOT EXISTS idx_host_connections_tenant_updated
ON host_connections(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS connector_instances (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    host_connection_id TEXT NOT NULL REFERENCES host_connections(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered', 'online', 'offline', 'restricted', 'disabled')),
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    observed_state_json TEXT NOT NULL DEFAULT '{}',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, host_connection_id, display_name)
);

CREATE INDEX IF NOT EXISTS idx_connector_instances_tenant_updated
ON connector_instances(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS bridge_devices (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    display_name TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('windows', 'macos', 'linux', 'other')),
    connection_mode TEXT NOT NULL DEFAULT 'outbound-only' CHECK (connection_mode = 'outbound-only'),
    execution_scope TEXT NOT NULL DEFAULT 'metadata-only'
        CHECK (execution_scope IN ('metadata-only', 'local-files', 'sandbox')),
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered', 'online', 'offline', 'restricted', 'disabled')),
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    observed_state_json TEXT NOT NULL DEFAULT '{}',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, display_name)
);

CREATE INDEX IF NOT EXISTS idx_bridge_devices_tenant_updated
ON bridge_devices(tenant_id, updated_at DESC);
