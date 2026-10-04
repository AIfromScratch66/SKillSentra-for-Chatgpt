from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.config import AppConfig
from app.server import create_server
from bridge.skillsentra_bridge import BridgeClient, BridgeClientConfig


class EdgeAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        config = AppConfig(
            root_dir=root,
            static_dir=root / "demo-apple",
            database_path=Path(cls.tempdir.name) / "edge-api.db",
            host="127.0.0.1",
            port=0,
            artifact_dir=Path(cls.tempdir.name) / "artifacts",
            allowed_skill_roots=(root / "sample-skills",),
            automation_enabled=False,
        )
        cls.server = create_server(config)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.tempdir.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_edge_registration_heartbeat_and_overview_routes(self) -> None:
        status, host_body = self.request(
            "POST",
            "/api/v1/control/host-connections",
            {
                "display_name": "ChatGPT Workspace",
                "host_type": "chatgpt",
                "transport": "mcp",
                "external_ref": "chatgpt:workspace:test",
                "capabilities": {"skill.search": "enforced"},
            },
        )
        host = host_body["data"]
        connector_status, connector_body = self.request(
            "POST",
            f"/api/v1/control/host-connections/{host['id']}/connectors",
            {
                "display_name": "SkillSentra Connector",
                "connector_version": "0.7.0-dev",
                "capabilities": {"skill.search": "enforced", "skill.invoke": "observed"},
            },
        )
        connector = connector_body["data"]
        heartbeat_status, heartbeat_body = self.request(
            "POST",
            f"/api/v1/control/connectors/{connector['id']}/heartbeat",
            {"observed_state": {"protocol": "mcp", "healthy": True}},
        )
        bridge_client = BridgeClient(BridgeClientConfig(self.base_url))
        bridge = bridge_client.register(
            "Local Windows Bridge",
            "windows",
            capabilities={"local.files": "unsupported"},
        )
        bridge = bridge_client.heartbeat(
            bridge["id"], observed_state={"mode": "metadata-only", "pending_jobs": 0}
        )
        overview_status, overview_body = self.request("GET", "/api/v1/control/edge/overview")

        self.assertEqual(status, 200)
        self.assertEqual(connector_status, 200)
        self.assertEqual(heartbeat_status, 200)
        self.assertEqual(heartbeat_body["data"]["status"], "online")
        self.assertEqual(bridge["connection_mode"], "outbound-only")
        self.assertEqual(bridge["status"], "online")
        self.assertEqual(overview_status, 200)
        self.assertEqual(overview_body["data"]["host_connections"]["total"], 1)
        self.assertEqual(overview_body["data"]["connectors"]["total"], 1)
        self.assertEqual(overview_body["data"]["bridges"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
