from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "skillsentra"
BRIDGE = PLUGIN / "scripts" / "mcp_server.py"
LAUNCHER = PLUGIN / "scripts" / "start_mcp.ps1"

READ_TOOLS = {
    "health",
    "search_skills",
    "discover_skills",
    "get_skill",
    "leaderboard",
    "list_projects",
    "get_expert_framework",
    "get_project_context",
    "get_ai_runs",
    "get_ai_contribution",
    "list_host_requests",
    "get_host_request",
}
WRITE_TOOLS = {
    "create_project",
    "evaluate_project",
    "record_expert_review",
    "check_updates",
    "run_ai_step",
    "create_host_request",
    "submit_host_result",
}


def expert_review_payload() -> dict[str, Any]:
    dimension_ids = (
        "product_strategy",
        "instruction_architecture",
        "evaluation_science",
        "safety_privacy",
        "plugin_interoperability",
        "experience_visualization",
        "reliability_lifecycle",
        "accessibility_localization",
    )
    gate_ids = (
        "critical_safety",
        "provenance_license",
        "real_host_connection",
        "real_task_evidence",
        "manual_accessibility",
        "human_release_authorization",
    )
    return {
        "framework_version": "skillsentra-expert-matrix-v1",
        "review_kind": "internal_simulation",
        "reviewer_label": "Codex internal expert matrix",
        "artifact_digest": "sha256:" + "a" * 64,
        "evaluation_world": {
            "skill_version": "0.6.0-rc.1",
            "host_version": "fixture-host",
            "adapter_version": "skillsentra-mcp/0.6.0",
            "model": "fixture-model",
            "installed_skill_set": ["skillsentra@fixture"],
            "tool_permissions": ["local-http-read", "project-review-write"],
            "dataset_id": "mcp-fixture",
            "dataset_version": "v1",
            "grader_version": "expert-matrix-v1",
            "executed_at": "2026-08-29T00:00:00Z",
            "external_dependencies": [],
        },
        "dimensions": {
            item: {
                "score": 9.0,
                "evidence_level": "E2",
                "evidence_refs": [f"tests::{item}"],
                "note": "Local fixture evidence.",
            }
            for item in dimension_ids
        },
        "gates": {
            item: {
                "status": "unknown",
                "evidence_level": "E0",
                "evidence_refs": [],
                "note": "Not established by this local MCP test; real-host passage requires E4.",
            }
            for item in gate_ids
        },
        "findings": ["Real-host evidence remains unknown."],
        "proposed_changes": ["Run an authorized real-host connection test."],
    }


class FakeSkillSentraHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, method: str, path: str, body: Any = None) -> None:
        self.__class__.requests.append({"method": method, "path": path, "body": body})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        self._record("GET", self.path)

        if parsed.path == "/api/health":
            return self._respond(200, {"data": {"status": "ok", "service": "fake-skillsentra"}})
        if parsed.path == "/api/v1/marketplace/publications":
            term = query.get("q", [""])[0]
            if term == "empty-response":
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if term == "explode":
                return self._respond(
                    503,
                    {"error": {"code": "temporary_unavailable", "message": "maintenance window"}},
                )
            return self._respond(
                200,
                {
                    "data": {
                        "items": [{"id": "skill-123", "name": "Fixture Skill", "version": "1.2.3"}],
                        "query": term,
                    }
                },
            )
        if parsed.path == "/api/v1/marketplace/publications/skill-123":
            return self._respond(200, {"data": {"id": "skill-123", "digest": "sha256:" + "b" * 64}})
        if parsed.path == "/api/v1/marketplace/leaderboard":
            return self._respond(200, {"data": {"items": [{"id": "skill-123", "rank": 1}]}})
        if parsed.path == "/api/v1/projects":
            return self._respond(200, {"data": [{"id": "project-1", "name": "Fixture Project"}]})
        if parsed.path == "/api/v1/projects/project-1":
            return self._respond(200, {"data": {"id": "project-1", "route": "template", "steps": [], "versions": [{"id": "version-1", "artifact_digest": "sha256:" + "a" * 64}]}})
        if parsed.path == "/api/v1/projects/project-1/ai-runs":
            return self._respond(200, {"data": [{"id": "run-1", "usage_status": "unknown", "input_tokens": None, "output_tokens": None, "total_tokens": None}]})
        if parsed.path == "/api/v1/projects/project-1/ai-contribution":
            return self._respond(200, {"data": {"real_chatgpt_confirmed": False, "chatgpt_contribution_status": "not_confirmed"}})
        if parsed.path == "/api/v1/projects/project-1/host-requests":
            return self._respond(200, {"data": [{"id": "host-1", "context_hash": "sha256:" + "c" * 64, "status": "pending"}]})
        if parsed.path == "/api/v1/projects/project-1/host-requests/host-1":
            return self._respond(200, {"data": {"id": "host-1", "context_hash": "sha256:" + "c" * 64, "context": {"fixture": True}, "status": "pending"}})
        if parsed.path == "/api/v1/expert-framework":
            return self._respond(
                200,
                {
                    "data": {
                        "version": "skillsentra-expert-matrix-v1",
                        "review_kind": "internal_simulation",
                        "disclaimer": "Internal simulation; not release authorization.",
                    }
                },
            )
        return self._respond(404, {"error": {"code": "not_found", "message": "fixture route not found"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        parsed = urlsplit(self.path)
        self._record("POST", self.path, body)

        if parsed.path == "/api/v1/projects":
            return self._respond(201, {"data": {"id": "project-2", **body}})
        if parsed.path == "/api/v1/discovery/search":
            return self._respond(
                200,
                {"data": {"count": 1, "items": [{"name": "External \ue857 Fixture", "repository": "https://github.com/example/fixture", "verification_status": "unverified_candidate"}], **body}},
            )
        if parsed.path == "/api/v1/projects/project-1/validations":
            return self._respond(201, {"data": {"id": "validation-1", **body}})
        if parsed.path == "/api/v1/projects/project-1/ai-runs":
            return self._respond(201, {"data": {"id": "run-1", "status": "completed", "usage_status": "unknown", "input_tokens": None, "output_tokens": None, "total_tokens": None, **body}})
        if parsed.path == "/api/v1/projects/project-1/host-requests":
            return self._respond(201, {"data": {"id": "host-1", "context_hash": "sha256:" + "c" * 64, "status": "pending", **body}})
        if parsed.path == "/api/v1/projects/project-1/host-requests/host-1/result":
            if body.get("context_hash") != "sha256:" + "c" * 64:
                return self._respond(409, {"error": {"code": "host_context_stale", "message": "Fixture stale context"}})
            return self._respond(201, {"data": {"id": "host-1", "status": "proposed", "usage_status": "host_reported" if body.get("usage") else "unknown", **body}})
        if parsed.path == "/api/v1/projects/project-1/expert-reviews":
            return self._respond(
                201,
                {"data": {"id": "expert-review-1", "decision": "NO_GO", **body}},
            )
        if parsed.path == "/api/v1/projects/project-1/update-checks":
            return self._respond(201, {"data": {"id": "update-check-1", "status": "recorded"}})
        return self._respond(404, {"error": {"code": "not_found", "message": "fixture route not found"}})


class RedirectTargetHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self.__class__.requests.append(
            {"path": self.path, "authorization": self.headers.get("Authorization")}
        )
        raw = b'{"data":{"status":"unexpected-redirect-target"}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class RedirectSourceHandler(BaseHTTPRequestHandler):
    target_url = ""
    authorizations: list[str | None] = []

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self.__class__.authorizations.append(self.headers.get("Authorization"))
        self.send_response(302)
        self.send_header("Location", f"{self.__class__.target_url}/redirect-target")
        self.send_header("Content-Length", "0")
        self.end_headers()


class MCPPluginTests(unittest.TestCase):
    def test_release_bundle_includes_the_plugin_package(self) -> None:
        from scripts.build_release import release_files

        paths = {path.relative_to(ROOT).as_posix() for path in release_files("0.6.3")}
        self.assertIn("bridge/skillsentra_bridge.py", paths)
        self.assertIn("plugins/skillsentra/.codex-plugin/plugin.json", paths)
        self.assertIn("plugins/skillsentra/assets/skillsentra-icon.svg", paths)

    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeSkillSentraHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.httpd.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        FakeSkillSentraHandler.requests = []

    def run_bridge(
        self,
        messages: list[dict[str, Any]],
        *,
        base_url: str | None = None,
        token: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["SKILLSENTRA_BASE_URL"] = base_url or self.base_url
        environment["SKILLSENTRA_TIMEOUT_SECONDS"] = "3"
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        if token is None:
            environment.pop("SKILLSENTRA_API_TOKEN", None)
        else:
            environment["SKILLSENTRA_API_TOKEN"] = token
        payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in messages) + "\n"
        return subprocess.run(
            [sys.executable, "-B", "-u", str(BRIDGE)],
            input=payload,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            check=False,
            env=environment,
            cwd=PLUGIN,
        )

    def run_mcp(self, messages: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
        completed = self.run_bridge(messages)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        return {item.get("id"): item for item in responses}

    @staticmethod
    def call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

    def test_manifest_initialize_ping_and_tool_schemas(self) -> None:
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        mcp_config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "skillsentra-for-chatgpt")
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
        self.assertRegex(manifest["version"], rf"^{re.escape(version)}\+chatgpt\.[a-z0-9-]+$")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        server_config = mcp_config["mcpServers"]["skillsentra-for-chatgpt"]
        self.assertEqual(server_config["args"][-1], "./scripts/start_mcp.ps1")
        self.assertEqual(
            server_config["env"]["SKILLSENTRA_BASE_URL"],
            "http://127.0.0.1:8866",
        )
        self.assertNotIn("SKILLSENTRA_BASE_URL", server_config["env_vars"])
        self.assertIn(
            'DEFAULT_BASE_URL = "http://127.0.0.1:8866"',
            BRIDGE.read_text(encoding="utf-8"),
        )
        for icon_key in ("composerIcon", "logo"):
            self.assertTrue((PLUGIN / manifest["interface"][icon_key]).is_file())

        responses = self.run_mcp(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "unittest", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "ping"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            ]
        )

        initialized = responses[1]["result"]
        self.assertEqual(initialized["protocolVersion"], "2025-06-18")
        self.assertEqual(initialized["serverInfo"], {"name": "skillsentra-for-chatgpt", "version": manifest["version"]})
        self.assertEqual(responses[2]["result"], {})

        tools = {item["name"]: item for item in responses[3]["result"]["tools"]}
        self.assertEqual(set(tools), READ_TOOLS | WRITE_TOOLS)
        for name, tool in tools.items():
            self.assertEqual(tool["inputSchema"]["type"], "object", name)
            self.assertFalse(tool["inputSchema"]["additionalProperties"], name)
            self.assertIn("ok", tool["outputSchema"]["required"], name)
            self.assertEqual(tool["annotations"]["readOnlyHint"], name in READ_TOOLS, name)
            self.assertFalse(tool["annotations"]["destructiveHint"], name)
        for name in WRITE_TOOLS:
            self.assertIn("confirmed", tools[name]["inputSchema"]["required"])
            self.assertIs(tools[name]["inputSchema"]["properties"]["confirmed"]["const"], True)
        review_schema = tools["record_expert_review"]["inputSchema"]["properties"]["review"]
        self.assertIn("artifact_digest", review_schema["required"])
        self.assertIn("evaluation_world", review_schema["required"])
        self.assertIn("evidence_level", review_schema["properties"]["gates"]["additionalProperties"]["required"])

    @unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
    def test_windows_launcher_forces_utf8_for_mcp_output(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("$env:PYTHONUTF8 = '1'", launcher)
        self.assertIn("$env:PYTHONIOENCODING = 'utf-8'", launcher)

    def test_read_tools_call_expected_http_routes(self) -> None:
        responses = self.run_mcp(
            [
                self.call(10, "health", {}),
                self.call(11, "search_skills", {"query": "report", "category": "writing", "pricing": "free"}),
                self.call(12, "get_skill", {"skill_id": "skill-123"}),
                self.call(13, "leaderboard", {"limit": 7}),
                self.call(14, "list_projects", {}),
                self.call(15, "get_expert_framework", {}),
                self.call(16, "discover_skills", {"provider": "github", "query": "report skill", "limit": 5}),
            ]
        )

        for request_id in range(10, 17):
            self.assertFalse(responses[request_id]["result"]["isError"])
            self.assertTrue(responses[request_id]["result"]["structuredContent"]["ok"])
        self.assertEqual(responses[11]["result"]["structuredContent"]["data"]["query"], "report")
        self.assertEqual(responses[12]["result"]["structuredContent"]["data"]["id"], "skill-123")
        self.assertEqual(
            responses[15]["result"]["structuredContent"]["data"]["version"],
            "skillsentra-expert-matrix-v1",
        )
        self.assertEqual(
            responses[16]["result"]["structuredContent"]["data"]["items"][0]["verification_status"],
            "unverified_candidate",
        )
        requested_paths = [item["path"] for item in FakeSkillSentraHandler.requests]
        self.assertIn("/api/v1/marketplace/publications?q=report&category=writing&pricing=free", requested_paths)
        self.assertIn("/api/v1/marketplace/leaderboard?limit=7", requested_paths)

    def test_missing_argument_http_error_and_confirmation_error_are_enveloped(self) -> None:
        responses = self.run_mcp(
            [
                self.call(20, "get_skill", {}),
                self.call(21, "search_skills", {"query": "explode"}),
                self.call(22, "create_project", {"name": "No confirmation", "route": "template"}),
            ]
        )

        missing = responses[20]["result"]
        self.assertTrue(missing["isError"])
        self.assertEqual(missing["structuredContent"]["error"]["code"], "missing_argument")
        upstream = responses[21]["result"]
        self.assertTrue(upstream["isError"])
        self.assertEqual(upstream["structuredContent"]["error"]["code"], "http_error")
        self.assertEqual(upstream["structuredContent"]["error"]["details"]["status"], 503)
        self.assertEqual(
            upstream["structuredContent"]["error"]["details"]["upstream_code"],
            "temporary_unavailable",
        )
        confirmation = responses[22]["result"]
        self.assertTrue(confirmation["isError"])
        self.assertEqual(confirmation["structuredContent"]["error"]["code"], "confirmation_required")
        self.assertFalse(any(item["method"] == "POST" for item in FakeSkillSentraHandler.requests))

    def test_empty_http_success_does_not_fabricate_a_completed_result(self) -> None:
        responses = self.run_mcp([self.call(26, "search_skills", {"query": "empty-response"})])
        result = responses[26]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "invalid_response")

    def test_redirect_is_not_followed_and_authorization_never_reaches_target(self) -> None:
        target = ThreadingHTTPServer(("127.0.0.1", 0), RedirectTargetHandler)
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectSourceHandler)
        source_thread = threading.Thread(target=source.serve_forever, daemon=True)
        target_host, target_port = target.server_address
        source_host, source_port = source.server_address
        RedirectTargetHandler.requests = []
        RedirectSourceHandler.authorizations = []
        RedirectSourceHandler.target_url = f"http://{target_host}:{target_port}"
        target_thread.start()
        source_thread.start()
        secret = "redirect-regression-secret-token"
        try:
            completed = self.run_bridge(
                [self.call(24, "health", {})],
                base_url=f"http://{source_host}:{source_port}",
                token=secret,
            )
        finally:
            source.shutdown()
            source.server_close()
            source_thread.join(timeout=5)
            target.shutdown()
            target.server_close()
            target_thread.join(timeout=5)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        result = responses[0]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "http_error")
        self.assertEqual(result["structuredContent"]["error"]["details"]["status"], 302)
        self.assertEqual(RedirectSourceHandler.authorizations, [f"Bearer {secret}"])
        self.assertEqual(RedirectTargetHandler.requests, [])
        self.assertNotIn(secret, completed.stdout)
        self.assertNotIn(secret, completed.stderr)

    def test_non_loopback_plain_http_base_url_is_rejected_without_token_leakage(self) -> None:
        secret = "startup-regression-secret-token"
        completed = self.run_bridge(
            [],
            base_url="http://example.com:8766",
            token=secret,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertIn("requires HTTPS", completed.stderr)
        self.assertNotIn(secret, completed.stderr)

    def test_base_url_path_and_query_secrets_never_reach_error_output(self) -> None:
        secret = "base-url-regression-secret-token"
        unused = ThreadingHTTPServer(("127.0.0.1", 0), RedirectTargetHandler)
        unused_host, unused_port = unused.server_address
        unused.server_close()

        unreachable = self.run_bridge(
            [self.call(25, "health", {})],
            base_url=f"http://{unused_host}:{unused_port}/private/{secret}",
            token=secret,
        )
        self.assertEqual(unreachable.returncode, 0, unreachable.stderr)
        response = json.loads(unreachable.stdout.strip())
        error_payload = response["result"]["structuredContent"]["error"]
        self.assertEqual(error_payload["code"], "service_unreachable")
        self.assertNotIn("details", error_payload)
        self.assertNotIn(secret, unreachable.stdout)
        self.assertNotIn(secret, unreachable.stderr)

        rejected_query = self.run_bridge(
            [],
            base_url=f"http://127.0.0.1:8766/api?credential={secret}",
            token=secret,
        )
        self.assertEqual(rejected_query.returncode, 1)
        self.assertEqual(rejected_query.stdout, "")
        self.assertIn("must not contain a query or fragment", rejected_query.stderr)
        self.assertNotIn(secret, rejected_query.stderr)

    def test_real_host_gate_cannot_pass_below_e4(self) -> None:
        review = expert_review_payload()
        review["gates"]["real_host_connection"] = {
            "status": "pass",
            "evidence_level": "E2",
            "evidence_refs": ["tests::not-real-host-evidence"],
            "note": "Insufficient evidence level.",
        }
        responses = self.run_mcp(
            [
                self.call(
                    23,
                    "record_expert_review",
                    {"project_id": "project-1", "review": review, "confirmed": True},
                )
            ]
        )

        result = responses[23]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "invalid_argument")
        self.assertFalse(any(item["method"] == "POST" for item in FakeSkillSentraHandler.requests))

    def test_confirmed_write_tools_send_minimal_expected_payloads(self) -> None:
        review = expert_review_payload()
        responses = self.run_mcp(
            [
                self.call(30, "create_project", {"name": "MCP Project", "route": "template", "confirmed": True}),
                self.call(
                    31,
                    "evaluate_project",
                    {"project_id": "project-1", "stage": "static", "confirmed": True},
                ),
                self.call(
                    32,
                    "record_expert_review",
                    {"project_id": "project-1", "review": review, "confirmed": True},
                ),
                self.call(33, "check_updates", {"project_id": "project-1", "confirmed": True}),
            ]
        )

        for request_id in range(30, 34):
            self.assertFalse(responses[request_id]["result"]["isError"])
        posts = {
            item["path"]: item["body"]
            for item in FakeSkillSentraHandler.requests
            if item["method"] == "POST"
        }
        self.assertEqual(posts["/api/v1/projects"], {"name": "MCP Project", "route": "template"})
        self.assertEqual(posts["/api/v1/projects/project-1/validations"], {"stage": "static"})
        self.assertEqual(posts["/api/v1/projects/project-1/expert-reviews"], review)
        self.assertEqual(posts["/api/v1/projects/project-1/update-checks"], {})

    def test_ai_context_reads_preserve_unknown_usage_and_host_boundaries(self) -> None:
        responses = self.run_mcp([
            self.call(40, "get_project_context", {"project_id": "project-1"}),
            self.call(41, "get_ai_runs", {"project_id": "project-1", "limit": 20}),
            self.call(42, "get_ai_contribution", {"project_id": "project-1"}),
            self.call(43, "list_host_requests", {"project_id": "project-1"}),
            self.call(44, "get_host_request", {"project_id": "project-1", "request_id": "host-1"}),
        ])
        for result in responses.values():
            self.assertFalse(result["result"]["isError"])
        self.assertEqual(responses[40]["result"]["structuredContent"]["data"]["versions"][0]["id"], "version-1")
        run = responses[41]["result"]["structuredContent"]["data"][0]
        self.assertEqual(run["usage_status"], "unknown")
        self.assertIsNone(run["total_tokens"])
        self.assertFalse(responses[42]["result"]["structuredContent"]["data"]["real_chatgpt_confirmed"])
        self.assertIn("/api/v1/projects/project-1/ai-runs?limit=20", [item["path"] for item in FakeSkillSentraHandler.requests])

    def test_real_ai_step_never_allows_mock_and_does_not_complete_step(self) -> None:
        arguments = {"project_id": "project-1", "route": "existing", "step_index": 6, "role": "evaluator", "task": "Review the authorized fixture", "context": {"fixture": True}, "confirmed": True}
        responses = self.run_mcp([self.call(45, "run_ai_step", arguments)])
        self.assertFalse(responses[45]["result"]["isError"])
        self.assertEqual(FakeSkillSentraHandler.requests, [{"method": "POST", "path": "/api/v1/projects/project-1/ai-runs", "body": {"route": "existing", "step_index": 6, "role": "evaluator", "task": arguments["task"], "context": {"fixture": True}, "require_real": True}}])
        self.assertIsNone(responses[45]["result"]["structuredContent"]["data"]["total_tokens"])

    def test_ai_and_host_writes_reject_unconfirmed_or_invalid_scope_before_http(self) -> None:
        valid = {"project_id": "project-1", "route": "template", "step_index": 0, "role": "creator", "task": "Fixture", "confirmed": True}
        messages = [self.call(50, "run_ai_step", {**valid, "confirmed": False}), self.call(51, "run_ai_step", {**valid, "step_index": True}), self.call(52, "run_ai_step", {**valid, "route": "existing", "step_index": 7}), self.call(53, "create_host_request", {"project_id": "project-1", "route": "template", "step_index": 0, "instruction": "Fixture"}), self.call(54, "submit_host_result", {"project_id": "project-1", "request_id": "host-1"})]
        responses = self.run_mcp(messages)
        self.assertTrue(all(item["result"]["isError"] for item in responses.values()))
        self.assertEqual(FakeSkillSentraHandler.requests, [])

    def test_host_roundtrip_is_context_bound_proposal_with_unknown_usage(self) -> None:
        responses = self.run_mcp([
            self.call(60, "create_host_request", {"project_id": "project-1", "route": "template", "step_index": 0, "instruction": "Review this fixture", "confirmed": True}),
            self.call(61, "get_host_request", {"project_id": "project-1", "request_id": "host-1"}),
            self.call(62, "submit_host_result", {"project_id": "project-1", "request_id": "host-1", "context_hash": "sha256:" + "c" * 64, "output": {"summary": "Fixture proposal", "patch": {"goal": "Proposed only"}}, "host_label": "unittest-fixture-not-live-host", "confirmed": True}),
        ])
        self.assertTrue(all(not item["result"]["isError"] for item in responses.values()))
        result = responses[62]["result"]["structuredContent"]["data"]
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["usage_status"], "unknown")
        self.assertNotIn("usage", result)
        self.assertNotIn("model", result)
        posts = [item for item in FakeSkillSentraHandler.requests if item["method"] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertTrue(all("/steps/" not in item["path"] for item in posts))

    def test_host_usage_rejects_forged_provenance_and_invalid_counts(self) -> None:
        base = {"project_id": "project-1", "request_id": "host-1", "context_hash": "sha256:" + "c" * 64, "output": {"summary": "Fixture"}, "confirmed": True}
        invalid_usage = [False, {"usage_source": "provider_response", "total_tokens": 10}, {"input_tokens": True}, {"output_tokens": -1}, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 99}, {"total_tokens": 10**13}]
        responses = self.run_mcp([self.call(70 + index, "submit_host_result", {**base, "usage": usage}) for index, usage in enumerate(invalid_usage)])
        self.assertTrue(all(item["result"]["isError"] for item in responses.values()))
        self.assertEqual(FakeSkillSentraHandler.requests, [])
        valid = self.run_mcp([self.call(76, "submit_host_result", {**base, "usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}})])
        self.assertEqual(valid[76]["result"]["structuredContent"]["data"]["usage_status"], "host_reported")

    def test_stale_host_result_preserves_backend_rejection(self) -> None:
        responses = self.run_mcp([self.call(80, "submit_host_result", {"project_id": "project-1", "request_id": "host-1", "context_hash": "sha256:" + "d" * 64, "output": {"summary": "Stale fixture"}, "confirmed": True})])
        failure = responses[80]["result"]["structuredContent"]["error"]
        self.assertEqual(failure["details"], {"status": 409, "upstream_code": "host_context_stale"})

    def test_validation_binds_the_exact_selected_version(self) -> None:
        responses = self.run_mcp([self.call(81, "evaluate_project", {"project_id": "project-1", "stage": "evaluation", "version_id": "version-1", "confirmed": True})])
        self.assertFalse(responses[81]["result"]["isError"])
        self.assertEqual(FakeSkillSentraHandler.requests[0]["body"], {"stage": "evaluation", "version_id": "version-1"})


if __name__ == "__main__":
    unittest.main()
