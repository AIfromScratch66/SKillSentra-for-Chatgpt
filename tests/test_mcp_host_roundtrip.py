"""Real local HTTP + stdio integration, not evidence of a live ChatGPT model call."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from app.config import AppConfig
from app.server import create_server


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "plugins" / "skillsentra" / "scripts" / "mcp_server.py"


class MCPHostRoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.server = create_server(AppConfig(
            root_dir=ROOT, static_dir=ROOT / "demo-apple",
            database_path=Path(self.tempdir.name) / "mcp-roundtrip.db",
            artifact_dir=Path(self.tempdir.name) / "artifacts",
            allowed_skill_roots=(ROOT / "sample-skills",),
            port=0, automation_enabled=False,
        ))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def call(self, name: str, arguments: dict) -> dict:
        environment = os.environ.copy()
        environment.update({"SKILLSENTRA_BASE_URL": self.base_url, "SKILLSENTRA_TIMEOUT_SECONDS": "5", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        environment.pop("SKILLSENTRA_API_TOKEN", None)
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        process = subprocess.run([sys.executable, "-B", "-u", str(BRIDGE)], input=json.dumps(message) + "\n", text=True, encoding="utf-8", capture_output=True, timeout=15, env=environment)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout.strip())["result"]

    def success(self, name: str, arguments: dict) -> object:
        result = self.call(name, arguments)
        self.assertFalse(result["isError"], result)
        return result["structuredContent"]["data"]

    def browser_read(self, path: str) -> object:
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))["data"]

    def test_all_sixteen_steps_host_proposals_roundtrip_without_completing_gates(self) -> None:
        for route, count in (("template", 9), ("existing", 7)):
            project = self.success("create_project", {"name": f"MCP transport fixture {route}", "route": route, "confirmed": True})
            project_id = project["id"]
            baseline = self.success("get_project_context", {"project_id": project_id})
            for step_index in range(count):
                with self.subTest(route=route, step_index=step_index):
                    created = self.success("create_host_request", {"project_id": project_id, "route": route, "step_index": step_index, "instruction": "Protocol fixture only: return a proposed change; do not claim a live model call", "confirmed": True})
                    request_id = created["id"]
                    selected = self.success("get_host_request", {"project_id": project_id, "request_id": request_id})
                    self.assertEqual(selected["context_hash"], created["context_hash"])
                    self.assertEqual(selected["context"]["step_index"], step_index)
                    output = {"summary": f"Fixture proposal {route}/{step_index}; no ChatGPT invocation", "patch": {"fixture_only": True}}
                    submitted = self.success("submit_host_result", {"project_id": project_id, "request_id": request_id, "context_hash": selected["context_hash"], "output": output, "host_label": "unittest-stdio-fixture", "confirmed": True})
                    from_browser = self.browser_read(f"/api/v1/projects/{project_id}/host-requests/{request_id}")
                    self.assertEqual(from_browser, submitted)
                    self.assertEqual(from_browser["output"], output)
                    self.assertEqual(from_browser["status"], "proposed")
                    self.assertFalse(from_browser["provider_verified"])
                    self.assertFalse(from_browser["applied"])
                    self.assertFalse(from_browser["automatically_invokes_host"])
                    self.assertEqual(from_browser["usage_status"], "unknown")
                    self.assertIsNone(from_browser["total_tokens"])
            after = self.success("get_project_context", {"project_id": project_id})
            for key in ("steps", "versions", "validations", "expert_reviews", "active_version_id", "status"):
                self.assertEqual(after.get(key), baseline.get(key), key)
            self.assertEqual(len(self.success("list_host_requests", {"project_id": project_id})), count)
            self.assertEqual(self.success("get_ai_runs", {"project_id": project_id}), [])
            contribution = self.success("get_ai_contribution", {"project_id": project_id})
            self.assertFalse(contribution["real_chatgpt_confirmed"])

    def test_real_ai_tool_rejects_mock_before_any_run(self) -> None:
        project = self.success("create_project", {"name": "Real-only fixture", "route": "template", "confirmed": True})
        result = self.call("run_ai_step", {"project_id": project["id"], "route": "template", "step_index": 0, "role": "creator", "task": "Do not use mock", "confirmed": True})
        self.assertTrue(result["isError"])
        self.assertIn(result["structuredContent"]["error"]["details"]["upstream_code"], {"real_ai_required", "ai_connection_required"})
        self.assertEqual(self.success("get_ai_runs", {"project_id": project["id"]}), [])


if __name__ == "__main__":
    unittest.main()
