from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database, json_dumps, row_to_dict, utc_now
from .errors import AppError


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def decode_json_fields(record: dict[str, Any] | None, *fields: str) -> dict[str, Any] | None:
    if record is None:
        return None
    for field in fields:
        if field in record:
            record[field.removesuffix("_json")] = json.loads(record.pop(field) or "{}")
    return record


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def create_project(
        self, name: str, route: str, tenant_id: str = "ten_demo", owner_user_id: str | None = None
    ) -> dict[str, Any]:
        project_id = make_id("prj")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO projects(id, name, route, tenant_id, owner_user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, name, route, tenant_id, owner_user_id, now, now),
            )
            connection.execute(
                "INSERT INTO ai_policies(project_id, created_at, updated_at) VALUES (?, ?, ?)",
                (project_id, now, now),
            )
            connection.execute(
                "INSERT INTO update_policies(project_id, next_check_at, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (project_id, self._next_check("weekly"), now, now),
            )
            self._audit(connection, project_id, "user", "project.created", "project", project_id, {"route": route})
        return self.get_project(project_id)

    def list_projects(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            if tenant_id is None:
                rows = connection.execute("SELECT * FROM projects ORDER BY updated_at DESC, rowid DESC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM projects WHERE tenant_id = ? ORDER BY updated_at DESC, rowid DESC",
                    (tenant_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        with self.database.session() as connection:
            if tenant_id is None:
                project = row_to_dict(connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
            else:
                project = row_to_dict(
                    connection.execute("SELECT * FROM projects WHERE id = ? AND tenant_id = ?", (project_id, tenant_id)).fetchone()
                )
            if project is None:
                return None
            steps = [
                decode_json_fields(dict(row), "payload_json")
                for row in connection.execute(
                    "SELECT * FROM step_states WHERE project_id = ? ORDER BY route, step_index",
                    (project_id,),
                )
            ]
            policy = row_to_dict(connection.execute("SELECT * FROM ai_policies WHERE project_id = ?", (project_id,)).fetchone())
        project["steps"] = steps
        project["ai_policy"] = self._normalize_policy(policy)
        return project

    def project_belongs_to(self, project_id: str, tenant_id: str) -> bool:
        with self.database.session() as connection:
            return connection.execute(
                "SELECT 1 FROM projects WHERE id = ? AND tenant_id = ?", (project_id, tenant_id)
            ).fetchone() is not None

    def update_project(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "status", "current_step", "source_id", "active_version_id", "route"}
        columns = [key for key in changes if key in allowed]
        if not columns:
            project = self.get_project(project_id)
            if project is None:
                raise KeyError("project_not_found")
            return project
        assignments = [f"{key} = ?" for key in columns]
        values = [changes[key] for key in columns]
        values.extend([utc_now(), project_id])
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE projects SET {', '.join(assignments)}, updated_at = ?, version = version + 1 WHERE id = ?",
                values,
            )
            if connection.total_changes == 0:
                raise KeyError("project_not_found")
            self._audit(connection, project_id, "user", "project.updated", "project", project_id, {"fields": columns})
        return self.get_project(project_id) or {}

    def save_step(
        self,
        project_id: str,
        route: str,
        step_index: int,
        payload: dict[str, Any],
        status: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        step_id = make_id("stp")
        now = utc_now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError("project_not_found")
            current = connection.execute(
                "SELECT revision FROM step_states WHERE project_id = ? AND route = ? AND step_index = ?",
                (project_id, route, step_index),
            ).fetchone()
            if expected_revision is not None:
                actual = int(current["revision"]) if current else 0
                if actual != expected_revision:
                    raise AppError(
                        "step_revision_conflict",
                        "此步骤已在其他页面更新，请刷新后重试。",
                        409,
                        {"expected_revision": expected_revision, "actual_revision": actual},
                    )
            connection.execute(
                """
                INSERT INTO step_states(id, project_id, route, step_index, payload_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, route, step_index) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    status = excluded.status,
                    revision = step_states.revision + 1,
                    updated_at = excluded.updated_at
                """,
                (step_id, project_id, route, step_index, json_dumps(payload), status, now, now),
            )
            connection.execute(
                "UPDATE projects SET route = ?, current_step = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (route, step_index, now, project_id),
            )
            row = connection.execute(
                "SELECT * FROM step_states WHERE project_id = ? AND route = ? AND step_index = ?",
                (project_id, route, step_index),
            ).fetchone()
            self._audit(connection, project_id, "user", "step.saved", "step", row["id"], {"route": route, "step_index": step_index, "status": status})
        return decode_json_fields(dict(row), "payload_json") or {}

    def create_connection(
        self,
        provider: str,
        display_name: str,
        base_url: str,
        credential_env: str,
        tenant_id: str = "ten_demo",
    ) -> dict[str, Any]:
        connection_id = make_id("conn")
        now = utc_now()
        secret = os.environ.get(credential_env, "") if credential_env else ""
        last4 = secret[-4:] if secret else ""
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ai_connections(
                    id, tenant_id, provider, display_name, base_url, credential_env, credential_last4,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'configured', ?, ?)
                """,
                (connection_id, tenant_id, provider, display_name, base_url, credential_env, last4, now, now),
            )
        return self.get_connection(connection_id, tenant_id) or {}

    def list_connections(self, tenant_id: str = "ten_demo") -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_connections WHERE tenant_id = ? ORDER BY updated_at DESC",
                (tenant_id,),
            ).fetchall()
        return [self._public_connection(dict(row)) for row in rows]

    def get_connection(self, connection_id: str, tenant_id: str = "ten_demo") -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM ai_connections WHERE id = ? AND tenant_id = ?",
                (connection_id, tenant_id),
            ).fetchone()
        return self._public_connection(dict(row)) if row else None

    def get_connection_internal(self, connection_id: str, tenant_id: str = "ten_demo") -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM ai_connections WHERE id = ? AND tenant_id = ?",
                (connection_id, tenant_id),
            ).fetchone()
        return decode_json_fields(dict(row), "model_catalog_json") if row else None

    def update_connection_test(
        self,
        connection_id: str,
        status: str,
        models: list[dict[str, Any]],
        error: str = "",
        tenant_id: str = "ten_demo",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT credential_env FROM ai_connections WHERE id = ? AND tenant_id = ?",
                (connection_id, tenant_id),
            ).fetchone()
            if current is None:
                raise KeyError("connection_not_found")
            secret = os.environ.get(str(current["credential_env"] or ""), "")
            last4 = secret[-4:] if secret else ""
            connection.execute(
                """
                UPDATE ai_connections
                SET status = ?, last_error = ?, model_catalog_json = ?, credential_last4 = ?, last_tested_at = ?, updated_at = ?
                WHERE id = ? AND tenant_id = ?
                """,
                (status, error, json_dumps(models), last4, now, now, connection_id, tenant_id),
            )
        return self.get_connection(connection_id, tenant_id) or {}

    def delete_connection(self, connection_id: str, tenant_id: str = "ten_demo") -> dict[str, Any]:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM ai_connections WHERE id = ? AND tenant_id = ?",
                (connection_id, tenant_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("connection_not_found")
        return {"id": connection_id, "deleted": True}

    def update_policy(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "enabled", "mode", "connection_id", "creator_model", "evaluator_model",
            "safety_model", "fallback_enabled", "redact_enabled", "external_files_enabled", "daily_budget",
        }
        columns = [key for key in changes if key in allowed]
        if not columns:
            return self.get_policy(project_id)
        values: list[Any] = []
        assignments: list[str] = []
        for column in columns:
            value = changes[column]
            if column in {"enabled", "fallback_enabled", "redact_enabled", "external_files_enabled"}:
                value = int(bool(value))
            assignments.append(f"{column} = ?")
            values.append(value)
        values.extend([utc_now(), project_id])
        with self.database.transaction() as connection:
            project = connection.execute(
                "SELECT tenant_id FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise KeyError("project_not_found")
            if "connection_id" in columns and changes.get("connection_id") is not None:
                connection_record = connection.execute(
                    "SELECT 1 FROM ai_connections WHERE id = ? AND tenant_id = ?",
                    (str(changes["connection_id"]), str(project["tenant_id"])),
                ).fetchone()
                if connection_record is None:
                    raise AppError("connection_not_found", "模型连接不存在。", 404)
            connection.execute(
                f"UPDATE ai_policies SET {', '.join(assignments)}, updated_at = ? WHERE project_id = ?",
                values,
            )
            if connection.total_changes == 0:
                raise KeyError("project_not_found")
            self._audit(connection, project_id, "user", "ai.policy.updated", "ai_policy", project_id, {"fields": columns})
        return self.get_policy(project_id)

    def get_policy(self, project_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM ai_policies WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError("project_not_found")
        return self._normalize_policy(dict(row))

    def daily_spend(self, project_id: str) -> float:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(MAX(estimated_cost, reserved_cost)), 0) AS total
                FROM ai_runs
                WHERE project_id = ? AND date(created_at) = date('now')
                """,
                (project_id,),
            ).fetchone()
        return float(row["total"])

    def start_ai_run(
        self,
        project_id: str,
        connection_id: str,
        route: str,
        step_index: int,
        role: str,
        model: str,
        manifest: dict[str, Any],
        orchestration_id: str = "",
        currency: str = "USD",
        pricebook_version: str = "local-pricebook-v1",
        attempt: int = 1,
        reserved_cost: float = 0,
        provider: str = "",
        budget_limit: float | None = None,
    ) -> dict[str, Any]:
        run_id = make_id("run")
        now = utc_now()
        with self.database.transaction() as connection:
            if budget_limit is not None:
                spent = float(connection.execute(
                    "SELECT COALESCE(SUM(MAX(estimated_cost, reserved_cost)), 0) FROM ai_runs "
                    "WHERE project_id = ? AND date(created_at) = date('now')", (project_id,),
                ).fetchone()[0])
                if spent + reserved_cost > budget_limit:
                    raise AppError("daily_budget_exceeded", "项目每日 AI 预算不足。", 409,
                                   {"spent": spent, "budget": budget_limit})
            connection.execute(
                """
                INSERT INTO ai_runs(
                    id, project_id, connection_id, route, step_index, role, model,
                    status, input_manifest_json, orchestration_id, currency,
                    pricebook_version, attempt, created_at, reserved_cost, provider_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, project_id, connection_id, route, step_index, role, model,
                    json_dumps(manifest), orchestration_id, currency, pricebook_version, attempt, now, reserved_cost, provider,
                ),
            )
            self._audit(
                connection,
                project_id,
                "system",
                "ai.run.started",
                "ai_run",
                run_id,
                {"model": model, "role": role, "orchestration_id": orchestration_id},
            )
        return self.get_ai_run(run_id) or {}

    def finish_ai_run(self, run_id: str, result: dict[str, Any] | None = None, error: str = "") -> dict[str, Any]:
        result = result or {}
        receipt = result.get("usage_receipt") or {
            "usage_status": "unknown", "usage_source": "unavailable", "cost_status": "unknown",
            "input_tokens": None, "output_tokens": None, "total_tokens": None, "estimated_cost": None,
        }
        status = "failed" if error else "completed"
        with self.database.transaction() as connection:
            row = connection.execute("SELECT project_id FROM ai_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("run_not_found")
            connection.execute(
                """
                UPDATE ai_runs SET status = ?, output_json = ?, input_tokens = ?, output_tokens = ?,
                    estimated_cost = ?, duration_ms = ?, provider_request_id = ?, error_message = ?, finished_at = ?,
                    usage_receipt_json = ?, reserved_cost = CASE WHEN ? THEN 0 ELSE reserved_cost END,
                    fallback_used = ?
                WHERE id = ?
                """,
                (
                    status,
                    json_dumps(result.get("output", {})),
                    int(receipt.get("input_tokens") or 0),
                    int(receipt.get("output_tokens") or 0),
                    float(result.get("estimated_cost") or receipt.get("estimated_cost") or 0),
                    int(result.get("duration_ms", 0)),
                    str(result.get("provider_request_id", "")),
                    error,
                    utc_now(),
                    json_dumps(receipt),
                    receipt.get("cost_status") in {"estimated_local_pricebook", "simulated", "not_sent"},
                    int(bool(result.get("fallback_used", False))),
                    run_id,
                ),
            )
            self._audit(connection, row["project_id"], "system", f"ai.run.{status}", "ai_run", run_id, {"error": error[:200]})
        return self.get_ai_run(run_id) or {}

    def get_ai_run(self, run_id: str) -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT r.*, c.provider AS connection_provider, c.display_name AS connection_name FROM ai_runs r "
                "LEFT JOIN ai_connections c ON c.id = r.connection_id WHERE r.id = ?", (run_id,)
            ).fetchone()
        return self._normalize_ai_run(dict(row)) if row else None

    def list_ai_runs(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT r.*, c.provider AS connection_provider, c.display_name AS connection_name
                FROM ai_runs r
                LEFT JOIN ai_connections c ON c.id = r.connection_id
                WHERE r.project_id = ?
                ORDER BY r.created_at DESC, r.rowid DESC
                LIMIT ?
                """,
                (project_id, max(1, min(limit, 200))),
            ).fetchall()
        return [self._normalize_ai_run(dict(row)) for row in rows]

    @staticmethod
    def _normalize_ai_run(record: dict[str, Any]) -> dict[str, Any]:
        decoded = decode_json_fields(record, "input_manifest_json", "output_json", "usage_receipt_json") or {}
        receipt = decoded.get("usage_receipt") or {}
        provider = decoded.get("provider_snapshot") or decoded.get("connection_provider") or "unknown"
        decoded["connection_provider"] = provider
        if not receipt:
            # Historical zeros have no supplier receipt and cannot prove zero use.
            mock = provider == "mock"
            receipt = {
                "usage_status": "simulated" if mock else ("unknown" if decoded.get("status") == "running" else "legacy_unknown"),
                "usage_source": "mock_fixture" if mock else "legacy_unverified",
                "input_tokens": decoded.get("input_tokens") if mock else None,
                "output_tokens": decoded.get("output_tokens") if mock else None,
                "total_tokens": int(decoded.get("input_tokens") or 0) + int(decoded.get("output_tokens") or 0) if mock else None,
                "cost_status": "simulated" if mock else "unknown",
                "estimated_cost": decoded.get("estimated_cost") if mock else None,
            }
            decoded["usage_receipt"] = receipt
        for name in ("usage_status", "usage_source", "input_tokens", "output_tokens", "total_tokens",
                     "cached_input_tokens", "reasoning_output_tokens", "cost_status", "provider_response_status",
                     "provider_model", "transport_request_id", "provider_error_code", "error_code", "output_sha256"):
            decoded[name] = receipt.get(name)
        if receipt.get("estimated_cost") is None and decoded.get("cost_status") not in {"simulated", "not_sent"}:
            decoded["estimated_cost"] = None
        decoded["fallback_used"] = bool(decoded.get("fallback_used"))
        return decoded

    def list_orchestration_runs(self, project_id: str, orchestration_id: str) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_runs WHERE project_id = ? AND orchestration_id = ? ORDER BY created_at, rowid",
                (project_id, orchestration_id),
            ).fetchall()
        return [self._normalize_ai_run(dict(row)) for row in rows]

    def ai_contribution_summary(self, project_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT r.*, c.provider AS connection_provider
                FROM ai_runs r
                LEFT JOIN ai_connections c ON c.id = r.connection_id
                WHERE r.project_id = ?
                """,
                (project_id,),
            ).fetchall()
        summary: dict[str, Any] = {
            "run_count": len(rows),
            "completed_run_count": 0,
            "failed_run_count": 0,
            "unknown_usage_run_count": 0,
            "reported_usage_run_count": 0,
            "usage_complete": True,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_by_currency": {},
            "by_provider": {},
            "mock_tokens": 0,
            "real_model_tokens": 0,
            "openai_api_tokens": 0,
            "openai_compatible_tokens": 0,
            "confirmed_chatgpt_tokens": 0,
            "unattributed_tokens": 0,
            "real_chatgpt_confirmed": False,
            "chatgpt_contribution_status": "not_confirmed",
            "evidence_level": "local_ai_run_ledger",
            "warnings": [],
        }
        for row in rows:
            row = self._normalize_ai_run(dict(row))
            provider = str(row["connection_provider"] or "unknown")
            input_tokens = int(row["input_tokens"] or 0)
            output_tokens = int(row["output_tokens"] or 0)
            total_tokens = int(row["total_tokens"]) if row.get("total_tokens") is not None else input_tokens + output_tokens
            if row.get("usage_status") == "reported":
                summary["reported_usage_run_count"] += 1
            elif row.get("usage_status") != "simulated":
                summary["unknown_usage_run_count"] += 1
                summary["usage_complete"] = False
            if row["status"] == "completed":
                summary["completed_run_count"] += 1
            elif row["status"] == "failed":
                summary["failed_run_count"] += 1
            summary["input_tokens"] += input_tokens
            summary["output_tokens"] += output_tokens
            summary["total_tokens"] += total_tokens
            provider_summary = summary["by_provider"].setdefault(
                provider,
                {"run_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )
            provider_summary["run_count"] += 1
            provider_summary["input_tokens"] += input_tokens
            provider_summary["output_tokens"] += output_tokens
            provider_summary["total_tokens"] += total_tokens
            currency = str(row["currency"] or "USD")
            costs = summary["estimated_cost_by_currency"]
            costs[currency] = round(float(costs.get(currency, 0)) + float(row["estimated_cost"] or 0), 8)
            if provider == "mock" or str(row["model"] or "").startswith("mock-"):
                summary["mock_tokens"] += total_tokens
            elif provider == "openai":
                summary["real_model_tokens"] += total_tokens
                summary["openai_api_tokens"] += total_tokens
            elif provider == "openai-compatible":
                summary["real_model_tokens"] += total_tokens
                summary["openai_compatible_tokens"] += total_tokens
            elif provider == "chatgpt":
                summary["real_model_tokens"] += total_tokens
                summary["confirmed_chatgpt_tokens"] += total_tokens
            else:
                summary["unattributed_tokens"] += total_tokens
        summary["real_chatgpt_confirmed"] = summary["confirmed_chatgpt_tokens"] > 0
        if summary["real_chatgpt_confirmed"]:
            summary["chatgpt_contribution_status"] = "confirmed_chatgpt_host"
        elif summary["openai_api_tokens"] or summary["openai_compatible_tokens"]:
            summary["chatgpt_contribution_status"] = "real_model_not_chatgpt_host"
            summary["warnings"].append("OpenAI API 或兼容接口调用属于真实模型用量，但不等同于 ChatGPT 宿主贡献。")
        else:
            summary["warnings"].append("未发现可确认的真实 ChatGPT 宿主调用。")
        if summary["mock_tokens"]:
            summary["warnings"].append("Mock token 仅代表本地开发联调用量，不能计为 ChatGPT 贡献。")
        if summary["unattributed_tokens"]:
            summary["warnings"].append("存在缺少连接提供商的历史运行记录，只能列为不可归因 token。")
        if summary["unknown_usage_run_count"]:
            summary["warnings"].append("存在未返回完整用量的运行；Token 合计只包含已知用量，未知用量不能视为零。")
        return summary

    def list_audit_events(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (project_id, max(1, min(limit, 500))),
            ).fetchall()
        return [decode_json_fields(dict(row), "detail_json") or {} for row in rows]

    def upsert_skill_source(
        self,
        name: str,
        root_path: str,
        artifact_digest: str,
        manifest: dict[str, Any],
        metadata: dict[str, Any],
        version: str = "unversioned",
        origin: str = "local",
        tenant_id: str = "ten_demo",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM skill_sources WHERE tenant_id = ? AND root_path = ?",
                (tenant_id, root_path),
            ).fetchone()
            source_id = existing["id"] if existing else make_id("src")
            connection.execute(
                """
                INSERT INTO skill_sources(
                    id, tenant_id, name, root_path, origin, version, artifact_digest,
                    manifest_json, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, root_path) DO UPDATE SET
                    name = excluded.name,
                    origin = excluded.origin,
                    version = excluded.version,
                    artifact_digest = excluded.artifact_digest,
                    manifest_json = excluded.manifest_json,
                    metadata_json = excluded.metadata_json,
                    status = 'ready',
                    updated_at = excluded.updated_at
                """,
                (
                    source_id, tenant_id, name, root_path, origin, version, artifact_digest,
                    json_dumps(manifest), json_dumps(metadata), now, now,
                ),
            )
        return self.get_skill_source(source_id, tenant_id) or {}

    def list_skill_sources(self, tenant_id: str = "ten_demo") -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM skill_sources WHERE tenant_id = ? AND status = 'ready' ORDER BY name",
                (tenant_id,),
            ).fetchall()
        return [decode_json_fields(dict(row), "manifest_json", "metadata_json") or {} for row in rows]

    def get_skill_source(self, source_id: str, tenant_id: str = "ten_demo") -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM skill_sources WHERE id = ? AND tenant_id = ?",
                (source_id, tenant_id),
            ).fetchone()
        return decode_json_fields(dict(row), "manifest_json", "metadata_json") if row else None

    def create_skill_version(
        self,
        project_id: str,
        version: str,
        artifact_digest: str,
        manifest: dict[str, Any],
        artifact_path: str,
        status: str = "candidate",
        package_path: str = "",
        base_version_id: str | None = None,
        change_summary: str = "",
    ) -> dict[str, Any]:
        version_id = make_id("ver")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO skill_versions(
                    id, project_id, version, artifact_digest, status, manifest_json,
                    artifact_path, package_path, base_version_id, change_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id, project_id, version, artifact_digest, status,
                    json_dumps(manifest), artifact_path, package_path, base_version_id,
                    change_summary, now,
                ),
            )
            connection.execute(
                "UPDATE projects SET active_version_id = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (version_id, now, project_id),
            )
            self._audit(
                connection,
                project_id,
                "system",
                "skill.version.created",
                "skill_version",
                version_id,
                {"version": version, "digest": artifact_digest, "status": status},
            )
        return self.get_skill_version(version_id) or {}

    def update_skill_version(self, version_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"status", "package_path", "change_summary"}
        columns = [key for key in changes if key in allowed]
        if not columns:
            version = self.get_skill_version(version_id)
            if version is None:
                raise KeyError("version_not_found")
            return version
        with self.database.transaction() as connection:
            row = connection.execute("SELECT project_id FROM skill_versions WHERE id = ?", (version_id,)).fetchone()
            if row is None:
                raise KeyError("version_not_found")
            connection.execute(
                f"UPDATE skill_versions SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
                [changes[column] for column in columns] + [version_id],
            )
            self._audit(connection, row["project_id"], "user", "skill.version.updated", "skill_version", version_id, {"fields": columns})
        return self.get_skill_version(version_id) or {}

    def get_skill_version(self, version_id: str) -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM skill_versions WHERE id = ?", (version_id,)).fetchone()
        return decode_json_fields(dict(row), "manifest_json") if row else None

    def list_skill_versions(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM skill_versions WHERE project_id = ? ORDER BY created_at DESC, rowid DESC",
                (project_id,),
            ).fetchall()
        return [decode_json_fields(dict(row), "manifest_json") or {} for row in rows]

    def create_validation(
        self,
        project_id: str,
        version_id: str | None,
        stage: str,
        status: str,
        score: float,
        findings: list[dict[str, Any]],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        validation_id = make_id("val")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO validation_runs(
                    id, project_id, version_id, stage, status, score,
                    findings_json, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation_id, project_id, version_id, stage, status, score,
                    json_dumps(findings), json_dumps(evidence), now,
                ),
            )
            self._audit(connection, project_id, "system", "validation.completed", "validation", validation_id, {"stage": stage, "status": status, "score": score})
        return self.get_validation(validation_id) or {}

    def get_validation(self, validation_id: str) -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM validation_runs WHERE id = ?", (validation_id,)).fetchone()
        return decode_json_fields(dict(row), "findings_json", "evidence_json") if row else None

    def list_validations(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM validation_runs WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (project_id, max(1, min(limit, 200))),
            ).fetchall()
        return [decode_json_fields(dict(row), "findings_json", "evidence_json") or {} for row in rows]

    def latest_validation(self, project_id: str, stage: str) -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM validation_runs WHERE project_id = ? AND stage = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id, stage),
            ).fetchone()
        return decode_json_fields(dict(row), "findings_json", "evidence_json") if row else None

    def create_expert_review(self, project_id: str, review: dict[str, Any]) -> dict[str, Any]:
        review_id = make_id("xrv")
        now = utc_now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError("project_not_found")
            versions = connection.execute(
                "SELECT id, artifact_digest FROM skill_versions WHERE project_id = ? ORDER BY created_at DESC, rowid DESC",
                (project_id,),
            ).fetchall()
            if not versions:
                raise AppError(
                    "expert_artifact_version_required",
                    "项目尚无可绑定的 Skill 版本摘要，不能记录专家评价。",
                    409,
                )
            artifact_version = next(
                (item for item in versions if item["artifact_digest"] == review.get("artifact_digest")),
                None,
            )
            if artifact_version is None:
                raise AppError(
                    "expert_artifact_digest_mismatch",
                    "artifact_digest 不属于该项目的任何 Skill 版本。",
                    409,
                )
            external_gates = {
                "real_host_connection",
                "real_task_evidence",
                "manual_accessibility",
                "human_release_authorization",
            }
            gates = review.get("gates") or {}
            if any((gates.get(gate_id) or {}).get("status") == "pass" for gate_id in external_gates):
                raise AppError(
                    "external_evidence_unverified",
                    "可信独立 E4/E5 证据注册表尚未启用，外部硬门不能由客户端标记为通过。",
                    409,
                )
            if review.get("decision") == "READY_FOR_HUMAN_DECISION":
                raise AppError(
                    "external_evidence_unverified",
                    "外部硬门尚未获得可信独立证据，不能进入人工发布决定。",
                    409,
                )
            connection.execute(
                """
                INSERT INTO expert_reviews(
                    id, project_id, framework_version, review_kind, reviewer_label,
                    artifact_digest, artifact_version_id, raw_score, capped_score, decision, evidence_ceiling,
                    evaluation_world_json, dimensions_json, gates_json, findings_json,
                    proposed_changes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    project_id,
                    review["framework_version"],
                    review["review_kind"],
                    review["reviewer_label"],
                    review["artifact_digest"],
                    artifact_version["id"],
                    review["raw_score"],
                    review["capped_score"],
                    review["decision"],
                    review["evidence_ceiling"],
                    json_dumps(review["evaluation_world"]),
                    json_dumps(review["dimensions"]),
                    json_dumps(review["gates"]),
                    json_dumps(review["findings"]),
                    json_dumps(review["proposed_changes"]),
                    now,
                ),
            )
            self._audit(
                connection,
                project_id,
                "agent",
                "expert_review.recorded",
                "expert_review",
                review_id,
                {
                    "framework_version": review["framework_version"],
                    "artifact_digest": review["artifact_digest"],
                    "decision": review["decision"],
                    "capped_score": review["capped_score"],
                },
            )
        return self.get_expert_review(review_id) or {}

    def get_expert_review(self, review_id: str) -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM expert_reviews WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            return None
        return decode_json_fields(
            dict(row),
            "evaluation_world_json",
            "dimensions_json",
            "gates_json",
            "findings_json",
            "proposed_changes_json",
        )

    def list_expert_reviews(self, project_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM expert_reviews WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (project_id, max(1, min(limit, 100))),
            ).fetchall()
        return [
            decode_json_fields(
                dict(row),
                "evaluation_world_json",
                "dimensions_json",
                "gates_json",
                "findings_json",
                "proposed_changes_json",
            )
            or {}
            for row in rows
        ]

    def get_update_policy(self, project_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM update_policies WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError("project_not_found")
        record = decode_json_fields(dict(row), "sources_json", "base_manifest_json") or {}
        record["enabled"] = bool(record.get("enabled"))
        return record

    def update_update_policy(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"enabled", "frequency", "sources", "strategy", "base_digest", "base_manifest", "last_checked_at", "next_check_at"}
        normalized = {key: value for key, value in changes.items() if key in allowed}
        if "sources" in normalized:
            normalized["sources_json"] = json_dumps(normalized.pop("sources"))
        if "base_manifest" in normalized:
            normalized["base_manifest_json"] = json_dumps(normalized.pop("base_manifest"))
        if "enabled" in normalized:
            normalized["enabled"] = int(bool(normalized["enabled"]))
        if "frequency" in normalized and "next_check_at" not in normalized:
            normalized["next_check_at"] = self._next_check(str(normalized["frequency"]))
        if not normalized:
            return self.get_update_policy(project_id)
        columns = list(normalized)
        values = [normalized[column] for column in columns] + [utc_now(), project_id]
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE update_policies SET {', '.join(f'{column} = ?' for column in columns)}, updated_at = ? WHERE project_id = ?",
                values,
            )
            if connection.total_changes == 0:
                raise KeyError("project_not_found")
            self._audit(connection, project_id, "user", "update.policy.updated", "update_policy", project_id, {"fields": columns})
        return self.get_update_policy(project_id)

    def create_update_check(
        self,
        project_id: str,
        base_digest: str,
        local_digest: str,
        upstream_digest: str,
        status: str,
        diff: dict[str, Any],
    ) -> dict[str, Any]:
        check_id = make_id("upd")
        now = utc_now()
        frequency = self.get_update_policy(project_id)["frequency"]
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO update_checks(
                    id, project_id, base_digest, local_digest, upstream_digest,
                    status, diff_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (check_id, project_id, base_digest, local_digest, upstream_digest, status, json_dumps(diff), now),
            )
            connection.execute(
                "UPDATE update_policies SET last_checked_at = ?, next_check_at = ?, updated_at = ? WHERE project_id = ?",
                (now, self._next_check(frequency), now, project_id),
            )
            self._audit(connection, project_id, "system", "update.check.completed", "update_check", check_id, {"status": status})
        return self.get_update_check(check_id) or {}

    def get_update_check(self, check_id: str) -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM update_checks WHERE id = ?", (check_id,)).fetchone()
        return decode_json_fields(dict(row), "diff_json") if row else None

    def list_update_checks(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM update_checks WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (project_id, max(1, min(limit, 200))),
            ).fetchall()
        return [decode_json_fields(dict(row), "diff_json") or {} for row in rows]

    def list_due_update_projects(self, limit: int = 20) -> list[str]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT project_id FROM update_policies
                WHERE enabled = 1 AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY COALESCE(next_check_at, created_at) LIMIT ?
                """,
                (utc_now(), max(1, min(limit, 100))),
            ).fetchall()
        return [str(row["project_id"]) for row in rows]

    @staticmethod
    def _next_check(frequency: str) -> str:
        days = {"daily": 1, "weekly": 7, "monthly": 30, "每天": 1, "每周": 7, "每月": 30}.get(frequency, 7)
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="milliseconds")

    @staticmethod
    def _audit(connection: Any, project_id: str | None, actor_type: str, action: str, entity_type: str, entity_id: str, detail: dict[str, Any]) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(project_id, actor_type, action, entity_type, entity_id, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, actor_type, action, entity_type, entity_id, json_dumps(detail), utc_now()),
        )

    @staticmethod
    def _public_connection(record: dict[str, Any]) -> dict[str, Any]:
        record = decode_json_fields(record, "model_catalog_json") or {}
        record.pop("credential_env", None)
        return record

    @staticmethod
    def _normalize_policy(record: dict[str, Any] | None) -> dict[str, Any] | None:
        if record is None:
            return None
        for field in ("enabled", "fallback_enabled", "redact_enabled", "external_files_enabled"):
            record[field] = bool(record[field])
        return record
