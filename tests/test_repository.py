from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.ai_gateway import AIGateway, ensure_safe_destination, validate_base_url, validate_credential_env
from app.database import Database
from app.errors import AppError
from app.repository import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tempdir.name)
        self.database = Database(self.root_path / "test.db")
        self.database.migrate()
        self.repository = Repository(self.database)
        self.gateway = AIGateway(self.repository)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_tenant(self, tenant_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO tenants(id, name, slug, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (tenant_id, tenant_id, tenant_id.replace("_", "-"), "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )

    def test_project_step_and_audit_round_trip(self) -> None:
        project = self.repository.create_project("研究简报", "template")
        step = self.repository.save_step(
            project["id"],
            "template",
            0,
            {"goal": "生成有来源的研究简报"},
            "complete",
        )
        loaded = self.repository.get_project(project["id"])
        events = self.repository.list_audit_events(project["id"])

        self.assertEqual(step["payload"]["goal"], "生成有来源的研究简报")
        self.assertEqual(loaded["steps"][0]["revision"], 1)
        self.assertEqual({event["action"] for event in events}, {"project.created", "step.saved"})

    def test_legacy_projects_receive_update_policies_during_migration(self) -> None:
        legacy_path = self.root_path / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        try:
            migration = Path(__file__).resolve().parents[1] / "app" / "migrations" / "001_initial.sql"
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES ('001_initial', '2026-01-01T00:00:00Z')")
            connection.execute(
                "INSERT INTO projects(id, name, route, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("prj_legacy", "历史项目", "template", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO ai_policies(project_id, created_at, updated_at) VALUES (?, ?, ?)",
                ("prj_legacy", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.commit()
        finally:
            connection.close()

        upgraded = Database(legacy_path)
        upgraded.migrate()
        repository = Repository(upgraded)

        self.assertEqual(repository.get_update_policy("prj_legacy")["frequency"], "weekly")

    def test_mock_ai_run_is_persisted_without_raw_context(self) -> None:
        project = self.repository.create_project("AI 测试", "template")
        connection = self.gateway.ensure_mock_connection()
        self.repository.update_policy(project["id"], {"connection_id": connection["id"]})

        run = self.gateway.run(
            project["id"],
            {
                "route": "template",
                "step_index": 0,
                "role": "creator",
                "task": "分析目标",
                "context": {"goal": "联系 test@example.com", "api_key": "fixture-secret-value"},
            },
        )

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["output"]["decision"], "pending")
        self.assertFalse(run["input_manifest"]["raw_persisted"])
        self.assertNotIn("test@example.com", str(run["input_manifest"]))
        self.assertNotIn("fixture-secret", str(run["input_manifest"]))

    def test_manual_mode_blocks_ai(self) -> None:
        project = self.repository.create_project("人工模式", "template")
        self.repository.update_policy(project["id"], {"mode": "manual"})

        with self.assertRaises(AppError) as context:
            self.gateway.run(project["id"], {"route": "template", "step_index": 0, "context": {}})

        self.assertEqual(context.exception.code, "ai_disabled")

    def test_ai_run_rejects_model_outside_synced_catalog(self) -> None:
        project = self.repository.create_project("模型目录", "template")
        connection = self.gateway.ensure_mock_connection()
        self.repository.update_policy(project["id"], {"connection_id": connection["id"]})

        with self.assertRaises(AppError) as context:
            self.gateway.run(
                project["id"],
                {"route": "template", "step_index": 0, "role": "creator", "model": "unknown-model", "context": {}},
            )

        self.assertEqual(context.exception.code, "model_not_available")

    def test_step_revision_conflict_is_not_silently_overwritten(self) -> None:
        project = self.repository.create_project("并发编辑", "template")
        first = self.repository.save_step(project["id"], "template", 0, {"goal": "v1"}, "draft", expected_revision=0)

        with self.assertRaises(AppError) as context:
            self.repository.save_step(project["id"], "template", 0, {"goal": "stale"}, "draft", expected_revision=0)

        self.assertEqual(first["revision"], 1)
        self.assertEqual(context.exception.code, "step_revision_conflict")
        self.assertEqual(context.exception.details["actual_revision"], 1)

    def test_three_role_orchestration_uses_versioned_server_pricebook(self) -> None:
        project = self.repository.create_project("三角色", "template")
        connection = self.gateway.ensure_mock_connection()
        self.repository.update_policy(project["id"], {"connection_id": connection["id"]})

        result = self.gateway.orchestrate(
            project["id"],
            {
                "route": "template",
                "step_index": 0,
                "roles": ["creator", "evaluator", "safety"],
                "context": {"goal": "构建 Skill"},
            },
        )

        self.assertEqual(len(result["runs"]), 3)
        self.assertEqual(result["total_cost"], sum(item["estimated_cost"] for item in result["runs"]))
        self.assertEqual(result["pricebook_version"], self.gateway.pricebook.version)
        self.assertTrue(all(item["currency"] == self.gateway.pricebook.currency for item in result["runs"]))

    def test_ai_contribution_summary_does_not_count_mock_as_chatgpt(self) -> None:
        project = self.repository.create_project("专利写作大师", "template")
        connection = self.gateway.ensure_mock_connection()
        self.repository.update_policy(project["id"], {"connection_id": connection["id"]})

        self.gateway.orchestrate(
            project["id"],
            {
                "route": "template",
                "step_index": 0,
                "roles": ["creator", "evaluator", "safety"],
                "context": {"goal": "生成中国专利申请文件"},
            },
        )
        runs = self.repository.list_ai_runs(project["id"])
        summary = self.repository.ai_contribution_summary(project["id"])

        self.assertEqual({item["connection_provider"] for item in runs}, {"mock"})
        self.assertEqual(summary["total_tokens"], 576)
        self.assertEqual(summary["mock_tokens"], 576)
        self.assertEqual(summary["confirmed_chatgpt_tokens"], 0)
        self.assertFalse(summary["real_chatgpt_confirmed"])
        self.assertEqual(summary["chatgpt_contribution_status"], "not_confirmed")
        self.assertTrue(any("Mock token" in item for item in summary["warnings"]))

    def test_connection_validation_rejects_unsafe_configuration(self) -> None:
        with self.assertRaises(AppError):
            validate_base_url("http://example.com/v1")
        with self.assertRaises(AppError):
            validate_base_url("https://user:pass@example.com/v1")
        with self.assertRaises(AppError):
            validate_base_url("https://169.254.169.254/v1")
        self.assertEqual(validate_credential_env("OPENAI_API_KEY"), "OPENAI_API_KEY")
        self.assertEqual(validate_credential_env("SKILLSENTRA_OPENAI_API_KEY"), "SKILLSENTRA_OPENAI_API_KEY")
        with self.assertRaises(AppError):
            validate_credential_env("OPENAI_API_KEY_EXTRA")
        self.assertEqual(validate_base_url("http://127.0.0.1:9000/v1"), "http://127.0.0.1:9000/v1")

    def test_official_openai_endpoint_allows_enterprise_dns_proxy(self) -> None:
        ensure_safe_destination("https://api.openai.com/v1")

    def test_newly_configured_connection_can_be_tested_and_become_healthy(self) -> None:
        connection = self.gateway.create_connection({"provider": "mock", "display_name": "首次联通"})

        self.assertEqual(connection["status"], "configured")
        tested = self.gateway.test_connection(connection["id"])

        self.assertEqual(tested["status"], "healthy")
        self.assertTrue(tested["model_catalog"])

    def test_connections_and_policy_binding_are_tenant_scoped(self) -> None:
        self.create_tenant("ten_alpha")
        self.create_tenant("ten_beta")
        alpha = self.repository.create_connection(
            "mock", "Alpha", "http://127.0.0.1/mock", "SKILLSENTRA_MOCK_KEY", "ten_alpha"
        )
        beta = self.repository.create_connection(
            "mock", "Beta", "http://127.0.0.1/mock", "SKILLSENTRA_MOCK_KEY", "ten_beta"
        )
        project = self.repository.create_project("Alpha project", "template", "ten_alpha")

        self.assertEqual([item["id"] for item in self.repository.list_connections("ten_alpha")], [alpha["id"]])
        self.assertEqual([item["id"] for item in self.repository.list_connections("ten_beta")], [beta["id"]])
        self.assertIsNone(self.repository.get_connection(alpha["id"], "ten_beta"))
        self.assertIsNone(self.repository.get_connection_internal(beta["id"], "ten_alpha"))
        with self.assertRaises(KeyError):
            self.repository.update_connection_test(alpha["id"], "healthy", [], tenant_id="ten_beta")
        with self.assertRaises(KeyError):
            self.repository.delete_connection(alpha["id"], "ten_beta")
        with self.assertRaises(AppError) as context:
            self.repository.update_policy(project["id"], {"connection_id": beta["id"]})
        self.assertEqual(context.exception.code, "connection_not_found")

        bound = self.repository.update_policy(project["id"], {"connection_id": alpha["id"]})
        self.assertEqual(bound["connection_id"], alpha["id"])
        self.assertEqual(self.repository.delete_connection(alpha["id"], "ten_alpha"), {"id": alpha["id"], "deleted": True})
        self.assertIsNone(self.repository.get_policy(project["id"])["connection_id"])

    def test_skill_source_identity_and_reads_are_tenant_scoped(self) -> None:
        self.create_tenant("ten_alpha")
        self.create_tenant("ten_beta")
        common = {
            "name": "Shared path",
            "root_path": str(self.root_path / "same-source"),
            "artifact_digest": "sha256:" + "a" * 64,
            "manifest": {"files": []},
            "metadata": {"name": "shared-path"},
        }
        alpha = self.repository.upsert_skill_source(**common, tenant_id="ten_alpha")
        beta = self.repository.upsert_skill_source(**common, tenant_id="ten_beta")

        self.assertNotEqual(alpha["id"], beta["id"])
        self.assertEqual([item["id"] for item in self.repository.list_skill_sources("ten_alpha")], [alpha["id"]])
        self.assertEqual([item["id"] for item in self.repository.list_skill_sources("ten_beta")], [beta["id"]])
        self.assertIsNone(self.repository.get_skill_source(alpha["id"], "ten_beta"))

    def test_tenant_migration_backfills_existing_connections_and_sources(self) -> None:
        legacy_path = self.root_path / "tenant-legacy.db"
        migration_dir = Path(__file__).resolve().parents[1] / "app" / "migrations"
        connection = sqlite3.connect(legacy_path)
        try:
            for version in ("001_initial", "002_lifecycle"):
                connection.executescript((migration_dir / f"{version}.sql").read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, "2026-01-01T00:00:00Z"),
                )
            connection.execute(
                "INSERT INTO ai_connections(id, provider, display_name, base_url, credential_env, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("conn_legacy", "mock", "Legacy", "http://127.0.0.1/mock", "SKILLSENTRA_MOCK_KEY", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO skill_sources(id, name, root_path, artifact_digest, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("src_legacy", "Legacy", "C:/legacy/skill", "sha256:" + "b" * 64, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.commit()
        finally:
            connection.close()

        upgraded = Database(legacy_path)
        upgraded.migrate()
        with upgraded.session() as connection:
            self.assertEqual(connection.execute("SELECT tenant_id FROM ai_connections WHERE id='conn_legacy'").fetchone()["tenant_id"], "ten_demo")
            self.assertEqual(connection.execute("SELECT tenant_id FROM skill_sources WHERE id='src_legacy'").fetchone()["tenant_id"], "ten_demo")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
