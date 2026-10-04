from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.control_plane import ControlPlane, Principal
from app.database import Database
from app.edge_control import EdgeControl
from app.errors import AppError
from app.repository import Repository
from app.skill_engine import SkillEngine


class EdgeControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        database = Database(root / "edge.db")
        database.migrate()
        repository = Repository(database)
        engine = SkillEngine(repository, root / "artifacts", ())
        control_plane = ControlPlane(database, repository, engine)
        self.principal = Principal(
            "ten_edge", "edge-admin", frozenset({"admin", "operator"}), "token"
        )
        control_plane.ensure_tenant(self.principal)
        self.database = database
        self.service = EdgeControl(database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_connector_and_bridge_are_separate_observed_edges(self) -> None:
        host = self.service.create_host_connection(
            self.principal,
            {
                "display_name": "Codex Desktop",
                "host_type": "codex",
                "transport": "mcp",
                "external_ref": "workspace:edge-test",
                "capabilities": {"skill.search": "enforced", "skill.invoke": "observed"},
            },
        )
        connector = self.service.create_connector(
            self.principal,
            host["id"],
            {
                "display_name": "SkillSentra MCP",
                "connector_version": "0.7.0-dev",
                "capabilities": {"skill.search": "enforced"},
            },
        )
        connector = self.service.heartbeat_connector(
            self.principal,
            connector["id"],
            {"observed_state": {"protocol": "2025-06-18", "tool_count": 11}},
        )
        bridge = self.service.create_bridge(
            self.principal,
            {
                "display_name": "Windows Development Bridge",
                "platform": "windows",
                "execution_scope": "sandbox",
                "capabilities": {
                    "local.files": "observed",
                    "sandbox.run": "declared",
                },
            },
        )
        bridge = self.service.heartbeat_bridge(
            self.principal,
            bridge["id"],
            {"status": "restricted", "observed_state": {"sandbox": "available", "pending_jobs": 0}},
        )
        overview = self.service.overview(self.principal)

        self.assertEqual(connector["status"], "online")
        self.assertEqual(connector["observed_state"]["tool_count"], 11)
        self.assertEqual(self.service.get_host_connection(self.principal, host["id"])["status"], "online")
        self.assertEqual(bridge["connection_mode"], "outbound-only")
        self.assertEqual(bridge["status"], "restricted")
        self.assertFalse(overview["bridge_policy"]["remote_execution"])
        self.assertFalse(overview["bridge_policy"]["automatic_install"])
        self.assertEqual(overview["connectors"]["total"], 1)
        self.assertEqual(overview["bridges"]["total"], 1)

    def test_capabilities_are_fail_closed_and_tenant_scoped(self) -> None:
        with self.assertRaises(AppError) as invalid:
            self.service.create_bridge(
                self.principal,
                {
                    "display_name": "Unsafe Bridge",
                    "platform": "windows",
                    "execution_scope": "unrestricted",
                },
            )
        self.assertEqual(invalid.exception.code, "invalid_execution_scope")

        bridge = self.service.create_bridge(
            self.principal,
            {
                "display_name": "Scoped Bridge",
                "platform": "linux",
                "capabilities": {"local.files": "unsupported"},
            },
        )
        other = Principal("ten_other", "other", self.principal.roles, "token")
        with self.assertRaises(AppError) as missing:
            self.service.get_bridge(other, bridge["id"])
        self.assertEqual(missing.exception.status, 404)

        with self.assertRaises(AppError) as sensitive:
            self.service.heartbeat_bridge(
                self.principal,
                bridge["id"],
                {"observed_state": {"runtime": {"api_key": "must-not-be-recorded"}}},
            )
        self.assertEqual(sensitive.exception.code, "sensitive_observed_state")

        viewer = Principal("ten_edge", "viewer", frozenset({"viewer"}), "token")
        with self.assertRaises(AppError) as forbidden:
            self.service.create_host_connection(
                viewer,
                {
                    "display_name": "Viewer Host",
                    "host_type": "chatgpt",
                    "transport": "mcp",
                    "external_ref": "viewer-host",
                },
            )
        self.assertEqual(forbidden.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
