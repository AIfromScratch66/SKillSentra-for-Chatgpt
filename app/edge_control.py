from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .control_plane import ControlPlane, Principal
from .database import Database, json_dumps, utc_now
from .errors import AppError
from .repository import make_id


CAPABILITY_LEVELS = frozenset({"enforced", "observed", "declared", "unsupported"})
CAPABILITY_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
EDGE_STATUSES = frozenset({"registered", "online", "offline", "restricted", "disabled"})


class EdgeControl:
    """Tenant-scoped registry for Agent connectors and outbound-only local Bridges.

    This service records identity, declared capability and observed liveness only.
    It deliberately does not execute local commands, install Skills or authorize a
    release. Those actions require a separate deployment policy and evidence gate.
    """

    def __init__(self, database: Database):
        self.database = database

    def create_host_connection(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        display_name = self._required_text(payload, "display_name", 120)
        host_type = str(payload.get("host_type") or "").strip().lower()
        if host_type not in {"chatgpt", "codex", "other-agent"}:
            raise AppError("invalid_host_type", "宿主类型必须是 chatgpt、codex 或 other-agent。")
        transport = str(payload.get("transport") or "mcp").strip().lower()
        if transport not in {"mcp", "host-app-api"}:
            raise AppError("invalid_host_transport", "连接方式必须是 mcp 或 host-app-api。")
        external_ref = self._required_text(payload, "external_ref", 200)
        capabilities = self._capabilities(payload.get("capabilities"))
        connection_id = make_id("host")
        now = utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO host_connections(
                        id, tenant_id, display_name, host_type, transport, external_ref,
                        capabilities_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        connection_id,
                        principal.tenant_id,
                        display_name,
                        host_type,
                        transport,
                        external_ref,
                        json_dumps(capabilities),
                        now,
                        now,
                    ),
                )
                ControlPlane._audit(
                    connection,
                    principal,
                    "host_connection.registered",
                    "host_connection",
                    connection_id,
                    {"host_type": host_type, "transport": transport},
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise AppError("host_connection_exists", "该宿主连接已经登记。", 409) from exc
            raise
        return self.get_host_connection(principal, connection_id)

    def get_host_connection(self, principal: Principal, connection_id: str) -> dict[str, Any]:
        return self._decode_capabilities(
            self._tenant_record("host_connections", connection_id, principal.tenant_id)
        )

    def list_host_connections(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM host_connections WHERE tenant_id=? ORDER BY updated_at DESC",
                (principal.tenant_id,),
            ).fetchall()
        return [self._decode_capabilities(dict(row)) for row in rows]

    def create_connector(
        self, principal: Principal, host_connection_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        principal.require("operator")
        self._tenant_record("host_connections", host_connection_id, principal.tenant_id)
        display_name = self._required_text(payload, "display_name", 120)
        connector_version = self._required_text(payload, "connector_version", 60)
        capabilities = self._capabilities(payload.get("capabilities"))
        connector_id = make_id("con")
        now = utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO connector_instances(
                        id, tenant_id, host_connection_id, display_name, connector_version,
                        capabilities_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        connector_id,
                        principal.tenant_id,
                        host_connection_id,
                        display_name,
                        connector_version,
                        json_dumps(capabilities),
                        now,
                        now,
                    ),
                )
                ControlPlane._audit(
                    connection,
                    principal,
                    "connector.registered",
                    "connector",
                    connector_id,
                    {"host_connection_id": host_connection_id, "connector_version": connector_version},
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise AppError("connector_exists", "该连接器实例已经登记。", 409) from exc
            raise
        return self.get_connector(principal, connector_id)

    def get_connector(self, principal: Principal, connector_id: str) -> dict[str, Any]:
        record = self._tenant_record("connector_instances", connector_id, principal.tenant_id)
        return self._decode_edge(record)

    def list_connectors(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT c.*, h.display_name AS host_display_name, h.host_type, h.transport
                FROM connector_instances c
                JOIN host_connections h ON h.id=c.host_connection_id
                WHERE c.tenant_id=? ORDER BY c.updated_at DESC
                """,
                (principal.tenant_id,),
            ).fetchall()
        return [self._decode_edge(dict(row)) for row in rows]

    def heartbeat_connector(
        self, principal: Principal, connector_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        principal.require("operator")
        connector = self._tenant_record("connector_instances", connector_id, principal.tenant_id)
        status = self._heartbeat_status(payload)
        observed_state = self._observed_state(payload.get("observed_state"))
        capabilities = (
            self._capabilities(payload.get("capabilities"))
            if "capabilities" in payload
            else json.loads(connector["capabilities_json"] or "{}")
        )
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE connector_instances
                SET status=?, capabilities_json=?, observed_state_json=?, last_seen_at=?, updated_at=?
                WHERE id=? AND tenant_id=?
                """,
                (
                    status,
                    json_dumps(capabilities),
                    json_dumps(observed_state),
                    now,
                    now,
                    connector_id,
                    principal.tenant_id,
                ),
            )
            connection.execute(
                """
                UPDATE host_connections SET status=?, last_seen_at=?, updated_at=?
                WHERE id=? AND tenant_id=? AND status!='disabled'
                """,
                (status, now, now, connector["host_connection_id"], principal.tenant_id),
            )
            ControlPlane._audit(
                connection,
                principal,
                "connector.heartbeat",
                "connector",
                connector_id,
                {"status": status},
            )
        return self.get_connector(principal, connector_id)

    def create_bridge(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        display_name = self._required_text(payload, "display_name", 120)
        platform = str(payload.get("platform") or "").strip().lower()
        if platform not in {"windows", "macos", "linux", "other"}:
            raise AppError("invalid_bridge_platform", "Bridge 平台必须是 windows、macos、linux 或 other。")
        execution_scope = str(payload.get("execution_scope") or "metadata-only").strip().lower()
        if execution_scope not in {"metadata-only", "local-files", "sandbox"}:
            raise AppError("invalid_execution_scope", "Bridge 执行范围不正确。")
        agent_id = str(payload.get("agent_id") or "").strip() or None
        if agent_id:
            self._tenant_record("agents", agent_id, principal.tenant_id)
        capabilities = self._capabilities(payload.get("capabilities"))
        bridge_id = make_id("brg")
        now = utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO bridge_devices(
                        id, tenant_id, agent_id, display_name, platform, execution_scope,
                        capabilities_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bridge_id,
                        principal.tenant_id,
                        agent_id,
                        display_name,
                        platform,
                        execution_scope,
                        json_dumps(capabilities),
                        now,
                        now,
                    ),
                )
                ControlPlane._audit(
                    connection,
                    principal,
                    "bridge.registered",
                    "bridge",
                    bridge_id,
                    {"platform": platform, "execution_scope": execution_scope, "connection_mode": "outbound-only"},
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise AppError("bridge_exists", "该本地 Bridge 已经登记。", 409) from exc
            raise
        return self.get_bridge(principal, bridge_id)

    def get_bridge(self, principal: Principal, bridge_id: str) -> dict[str, Any]:
        return self._decode_edge(self._tenant_record("bridge_devices", bridge_id, principal.tenant_id))

    def list_bridges(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM bridge_devices WHERE tenant_id=? ORDER BY updated_at DESC",
                (principal.tenant_id,),
            ).fetchall()
        return [self._decode_edge(dict(row)) for row in rows]

    def heartbeat_bridge(self, principal: Principal, bridge_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("operator")
        bridge = self._tenant_record("bridge_devices", bridge_id, principal.tenant_id)
        status = self._heartbeat_status(payload)
        observed_state = self._observed_state(payload.get("observed_state"))
        capabilities = (
            self._capabilities(payload.get("capabilities"))
            if "capabilities" in payload
            else json.loads(bridge["capabilities_json"] or "{}")
        )
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE bridge_devices
                SET status=?, capabilities_json=?, observed_state_json=?, last_seen_at=?, updated_at=?
                WHERE id=? AND tenant_id=?
                """,
                (
                    status,
                    json_dumps(capabilities),
                    json_dumps(observed_state),
                    now,
                    now,
                    bridge_id,
                    principal.tenant_id,
                ),
            )
            ControlPlane._audit(
                connection,
                principal,
                "bridge.heartbeat",
                "bridge",
                bridge_id,
                {"status": status, "connection_mode": "outbound-only"},
            )
        return self.get_bridge(principal, bridge_id)

    def overview(self, principal: Principal) -> dict[str, Any]:
        with self.database.session() as connection:
            host_rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM host_connections WHERE tenant_id=? GROUP BY status",
                (principal.tenant_id,),
            ).fetchall()
            connector_rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM connector_instances WHERE tenant_id=? GROUP BY status",
                (principal.tenant_id,),
            ).fetchall()
            bridge_rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM bridge_devices WHERE tenant_id=? GROUP BY status",
                (principal.tenant_id,),
            ).fetchall()
        return {
            "host_connections": self._status_counts(host_rows),
            "connectors": self._status_counts(connector_rows),
            "bridges": self._status_counts(bridge_rows),
            "bridge_policy": {
                "connection_mode": "outbound-only",
                "remote_execution": False,
                "automatic_install": False,
            },
        }

    @staticmethod
    def _status_counts(rows: Any) -> dict[str, int]:
        counts = {status: 0 for status in sorted(EDGE_STATUSES)}
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
        counts["total"] = sum(counts.values())
        return counts

    @staticmethod
    def _heartbeat_status(payload: dict[str, Any]) -> str:
        status = str(payload.get("status") or "online").strip().lower()
        if status not in {"online", "restricted"}:
            raise AppError("invalid_heartbeat_status", "心跳状态必须是 online 或 restricted。")
        return status

    @staticmethod
    def _capabilities(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > 64:
            raise AppError("invalid_edge_capabilities", "能力声明必须是最多 64 项的对象。")
        normalized: dict[str, str] = {}
        for raw_key, raw_level in value.items():
            key = str(raw_key).strip().lower()
            level = str(raw_level).strip().lower()
            if not CAPABILITY_KEY_RE.fullmatch(key) or level not in CAPABILITY_LEVELS:
                raise AppError(
                    "invalid_edge_capabilities",
                    "能力名称或等级不正确；等级必须是 enforced、observed、declared 或 unsupported。",
                )
            normalized[key] = level
        if len(json_dumps(normalized).encode("utf-8")) > 16_384:
            raise AppError("edge_capabilities_too_large", "能力声明超过 16 KB。", 413)
        return normalized

    @staticmethod
    def _observed_state(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AppError("invalid_observed_state", "观测状态必须是对象。")
        if EdgeControl._contains_sensitive_key(value):
            raise AppError(
                "sensitive_observed_state",
                "观测状态不能包含 token、secret、password、credential、api_key 或 authorization 字段。",
            )
        try:
            encoded = json_dumps(value).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_observed_state", "观测状态必须是可序列化数据。") from exc
        if len(encoded) > 32_768:
            raise AppError("observed_state_too_large", "观测状态超过 32 KB。", 413)
        return value

    @staticmethod
    def _contains_sensitive_key(value: Any) -> bool:
        sensitive = {"token", "secret", "password", "credential", "api_key", "apikey", "authorization"}
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key).strip().lower().replace("-", "_")
                if key in sensitive or any(part in sensitive for part in key.split(".")):
                    return True
                if EdgeControl._contains_sensitive_key(child):
                    return True
        elif isinstance(value, list):
            return any(EdgeControl._contains_sensitive_key(item) for item in value)
        return False

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str, limit: int) -> str:
        value = str(payload.get(field) or "").strip()
        if not value:
            raise AppError("field_required", f"{field} 为必填项。", 400, {"field": field})
        if len(value) > limit:
            raise AppError("field_too_large", f"{field} 超过长度限制。", 413, {"field": field, "limit": limit})
        return value

    def _tenant_record(self, table: str, record_id: str, tenant_id: str) -> dict[str, Any]:
        if table not in {"host_connections", "connector_instances", "bridge_devices", "agents"}:
            raise ValueError("Unsupported edge table")
        with self.database.session() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id=? AND tenant_id=?", (record_id, tenant_id)
            ).fetchone()
        if row is None:
            raise AppError("edge_resource_not_found", "边缘资源不存在或当前租户无权访问。", 404)
        return dict(row)

    @staticmethod
    def _decode_capabilities(record: dict[str, Any]) -> dict[str, Any]:
        record["capabilities"] = json.loads(record.pop("capabilities_json") or "{}")
        return record

    @classmethod
    def _decode_edge(cls, record: dict[str, Any]) -> dict[str, Any]:
        record = cls._decode_capabilities(record)
        record["observed_state"] = json.loads(record.pop("observed_state_json") or "{}")
        return record
