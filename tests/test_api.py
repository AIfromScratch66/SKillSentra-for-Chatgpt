from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.config import AppConfig
from app.errors import AppError
from app.server import create_server, health_snapshot
from tests.test_expert_matrix import review_payload


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        config = AppConfig(
            root_dir=root,
            static_dir=root / "demo-apple",
            database_path=Path(cls.tempdir.name) / "api.db",
            host="127.0.0.1",
            port=0,
            artifact_dir=Path(cls.tempdir.name) / "artifacts",
            allowed_skill_roots=(root / "sample-skills",),
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

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_health_and_static_app(self) -> None:
        status, body = self.request("GET", "/api/health")
        with urllib.request.urlopen(f"{self.base_url}/", timeout=5) as response:
            html = response.read().decode("utf-8")
            csp = response.headers.get("Content-Security-Policy", "")
        with urllib.request.urlopen(f"{self.base_url}/studio.html", timeout=5) as response:
            studio_html = response.read().decode("utf-8")

        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["database"], "ready")
        self.assertEqual(body["data"]["version"], "0.8.0")
        self.assertTrue(body["data"]["schema_version"])
        self.assertIn('id="ranking"', html)
        self.assertIn('src="marketplace.js?v=', html)
        self.assertIn("SkillSentra Studio", studio_html)
        self.assertIn('src="i18n.js"', studio_html)
        self.assertIn("default-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_static_entrypoint_aliases_preserve_behavior_with_query_strings(self) -> None:
        for path, marker in (("/studio.html?refresh=v064", "SkillSentra Studio"), ("/?refresh=v064", 'id="ranking"')):
            with self.subTest(path=path):
                with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(marker, response.read().decode("utf-8"))

    def test_health_fails_closed_when_database_probe_fails(self) -> None:
        class BrokenDatabase:
            @staticmethod
            def session() -> object:
                raise OSError("fixture database unavailable")

        with self.assertRaises(AppError) as context:
            health_snapshot(BrokenDatabase())  # type: ignore[arg-type]

        self.assertEqual(context.exception.code, "database_unavailable")
        self.assertEqual(context.exception.status, 503)

    def test_full_project_ai_flow(self) -> None:
        _, bootstrap = self.request("GET", "/api/v1/bootstrap")
        connection = bootstrap["data"]["default_connection"]
        status, created = self.request("POST", "/api/v1/projects", {"name": "API 项目", "route": "template"})
        project = created["data"]
        _, step = self.request(
            "PUT",
            f"/api/v1/projects/{project['id']}/steps/0",
            {
                "route": "template",
                "status": "complete",
                "expected_revision": 0,
                "payload": {
                    "skillName": "api-skill",
                    "audience": "开发者",
                    "goal": "构建可验证 Skill",
                    "success": "形成可验证候选版本",
                },
            },
        )
        _, tested = self.request("POST", f"/api/v1/ai/connections/{connection['id']}/test", {})
        _, run = self.request(
            "POST",
            f"/api/v1/projects/{project['id']}/ai-runs",
            {
                "connection_id": connection["id"],
                "route": "template",
                "step_index": 0,
                "role": "creator",
                "task": "提取目标",
                "context": {"goal": "构建可验证 Skill"},
            },
        )
        _, audit = self.request("GET", f"/api/v1/projects/{project['id']}/audit")

        self.assertEqual(status, 200)
        self.assertEqual(step["data"]["status"], "complete")
        self.assertEqual(tested["data"]["status"], "healthy")
        self.assertEqual(run["data"]["status"], "completed")
        self.assertGreaterEqual(len(audit["data"]), 5)

    def test_expert_framework_and_version_bound_review_api(self) -> None:
        _, bootstrap = self.request("GET", "/api/v1/bootstrap")
        _, created = self.request("POST", "/api/v1/projects", {"name": "专家 API", "route": "template"})
        project_id = created["data"]["id"]
        no_version_status, no_version = self.request(
            "POST",
            f"/api/v1/projects/{project_id}/expert-reviews",
            review_payload(),
        )
        candidate_status, candidate = self.request(
            "POST",
            f"/api/v1/projects/{project_id}/candidate",
            {"version": "0.6.0-review-fixture"},
        )
        self.assertEqual(candidate_status, 200)
        payload = review_payload()
        payload["artifact_digest"] = candidate["data"]["version"]["artifact_digest"]
        arbitrary_payload = dict(payload)
        arbitrary_payload["artifact_digest"] = "sha256:" + "f" * 64
        arbitrary_status, arbitrary = self.request(
            "POST",
            f"/api/v1/projects/{project_id}/expert-reviews",
            arbitrary_payload,
        )
        fabricated_payload = review_payload()
        fabricated_payload["artifact_digest"] = payload["artifact_digest"]
        fabricated_payload["gates"]["real_host_connection"] = {
            "status": "pass",
            "evidence_level": "E4",
            "evidence_refs": ["claim://fabricated-host-connection"],
        }
        fabricated_status, fabricated = self.request(
            "POST",
            f"/api/v1/projects/{project_id}/expert-reviews",
            fabricated_payload,
        )
        status, recorded = self.request(
            "POST",
            f"/api/v1/projects/{project_id}/expert-reviews",
            payload,
        )
        _, restored = self.request("GET", f"/api/v1/projects/{project_id}")

        self.assertEqual(bootstrap["data"]["expert_framework"]["review_kind"], "internal_simulation")
        self.assertEqual(bootstrap["data"]["expert_framework"]["external_evidence_registry"]["status"], "unavailable")
        self.assertEqual(no_version_status, 409)
        self.assertEqual(no_version["error"]["code"], "expert_artifact_version_required")
        self.assertEqual(arbitrary_status, 409)
        self.assertEqual(arbitrary["error"]["code"], "expert_artifact_digest_mismatch")
        self.assertEqual(fabricated_status, 409)
        self.assertEqual(fabricated["error"]["code"], "external_evidence_unverified")
        self.assertEqual(status, 200)
        self.assertEqual(recorded["data"]["decision"], "NO_GO")
        self.assertEqual(recorded["data"]["capped_score"], 7.9)
        self.assertEqual(recorded["data"]["artifact_version_id"], candidate["data"]["version"]["id"])
        self.assertEqual(restored["data"]["expert_reviews"][0]["id"], recorded["data"]["id"])

    def test_external_discovery_api_fails_closed_before_network(self) -> None:
        provider_status, provider_error = self.request(
            "POST",
            "/api/v1/discovery/search",
            {"provider": "unknown", "query": "report skill", "limit": 5},
        )
        query_status, query_error = self.request(
            "POST",
            "/api/v1/discovery/search",
            {"provider": "github", "query": "x", "limit": 5},
        )

        self.assertEqual(provider_status, 400)
        self.assertEqual(provider_error["error"]["code"], "invalid_discovery_provider")
        self.assertEqual(query_status, 400)
        self.assertEqual(query_error["error"]["code"], "discovery_invalid_query")

    def test_three_role_orchestration_and_project_restore(self) -> None:
        _, bootstrap = self.request("GET", "/api/v1/bootstrap")
        connection = bootstrap["data"]["default_connection"]
        _, created = self.request("POST", "/api/v1/projects", {"name": "编排项目", "route": "template"})
        project_id = created["data"]["id"]

        status, result = self.request(
            "POST",
            f"/api/v1/projects/{project_id}/ai-orchestrations",
            {
                "connection_id": connection["id"],
                "route": "template",
                "step_index": 0,
                "roles": ["creator", "evaluator", "safety"],
                "task": "形成目标契约",
                "context": {"email": "owner@example.com", "token": "sk-secretfixture000000"},
            },
        )
        _, restored = self.request("GET", f"/api/v1/projects/{project_id}")
        _, runs = self.request("GET", f"/api/v1/projects/{project_id}/ai-runs?limit=10")
        _, contribution = self.request("GET", f"/api/v1/projects/{project_id}/ai-contribution")

        self.assertEqual(status, 200)
        self.assertEqual([item["role"] for item in result["data"]["runs"]], ["creator", "evaluator", "safety"])
        self.assertEqual(len({item["orchestration_id"] for item in result["data"]["runs"]}), 1)
        self.assertTrue(result["data"]["pricebook_version"])
        self.assertEqual(restored["data"]["id"], project_id)
        self.assertEqual(len(runs["data"]), 3)
        self.assertEqual({item["connection_provider"] for item in runs["data"]}, {"mock"})
        self.assertEqual(contribution["data"]["total_tokens"], 576)
        self.assertEqual(contribution["data"]["mock_tokens"], 576)
        self.assertEqual(contribution["data"]["confirmed_chatgpt_tokens"], 0)
        self.assertFalse(contribution["data"]["real_chatgpt_confirmed"])
        self.assertEqual(contribution["data"]["chatgpt_contribution_status"], "not_confirmed")
        self.assertNotIn("owner@example.com", json.dumps(runs, ensure_ascii=False))
        self.assertNotIn("secretfixture", json.dumps(runs, ensure_ascii=False))

    def test_connection_response_does_not_expose_credential_reference(self) -> None:
        _, body = self.request("GET", "/api/v1/bootstrap")
        serialized = json.dumps(body, ensure_ascii=False)

        self.assertNotIn("credential_env", body["data"]["default_connection"])
        self.assertTrue(all("credential_env" not in item for item in body["data"]["connections"]))
        self.assertNotIn("sk-secret", serialized)
        self.assertFalse(body["data"]["security"]["browser_secrets"])

    def test_import_response_hides_server_paths(self) -> None:
        _, bootstrap = self.request("GET", "/api/v1/bootstrap")
        source = bootstrap["data"]["skill_sources"][0]
        _, created = self.request("POST", "/api/v1/projects", {"name": "安全导入", "route": "existing"})

        status, imported = self.request(
            "POST",
            f"/api/v1/projects/{created['data']['id']}/import",
            {"source_id": source["id"]},
        )
        serialized = json.dumps(imported, ensure_ascii=False)

        self.assertEqual(status, 200)
        self.assertNotIn("root_path", serialized)
        self.assertNotIn("artifact_path", serialized)
        self.assertNotIn("package_path", serialized)
        self.assertEqual(imported["data"]["baseline"]["status"], "baseline")

    def test_error_envelope(self) -> None:
        status, body = self.request("POST", "/api/v1/projects", {"name": "", "route": "wrong"})

        self.assertEqual(status, 400)
        self.assertIn("code", body["error"])
        self.assertIn("request_id", body["meta"])

    def test_client_disconnect_during_response_is_a_transport_outcome(self) -> None:
        class DisconnectedWriter:
            @staticmethod
            def write(_: bytes) -> None:
                raise ConnectionAbortedError("client closed the socket")

        handler = object.__new__(self.server.RequestHandlerClass)
        handler.command = "GET"
        handler.path = "/api/health"
        handler.close_connection = False
        handler.wfile = DisconnectedWriter()
        handler.send_response = lambda _status: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None

        handler._send_json(200, {"data": {"status": "ok"}})

        self.assertTrue(handler.close_connection)

    def test_policy_and_step_bounds_return_clear_errors(self) -> None:
        _, created = self.request("POST", "/api/v1/projects", {"name": "边界测试", "route": "existing"})
        project_id = created["data"]["id"]

        policy_status, policy = self.request(
            "PUT",
            f"/api/v1/projects/{project_id}/ai-policy",
            {"mode": "unattended", "daily_budget": -1},
        )
        step_status, step = self.request(
            "PUT",
            f"/api/v1/projects/{project_id}/steps/7",
            {"route": "existing", "status": "draft", "payload": {}},
        )
        route_status, route_error = self.request(
            "PUT",
            f"/api/v1/projects/{project_id}/steps/0",
            {"route": "template", "status": "draft", "payload": {}},
        )

        self.assertEqual(policy_status, 400)
        self.assertEqual(policy["error"]["code"], "invalid_ai_mode")
        self.assertEqual(step_status, 400)
        self.assertEqual(step["error"]["code"], "invalid_step_index")
        self.assertEqual(route_status, 409)
        self.assertEqual(route_error["error"]["code"], "project_route_conflict")

    def test_invalid_limits_are_client_errors(self) -> None:
        _, created = self.request("POST", "/api/v1/projects", {"name": "Limit", "route": "template"})
        project_id = created["data"]["id"]

        audit_status, audit = self.request("GET", f"/api/v1/projects/{project_id}/audit?limit=NaN")
        job_status, jobs = self.request("POST", "/api/v1/update-jobs/run-due", {"limit": 0})

        self.assertEqual(audit_status, 400)
        self.assertEqual(audit["error"]["code"], "invalid_limit")
        self.assertEqual(job_status, 400)
        self.assertEqual(jobs["error"]["code"], "invalid_limit")

    def test_user_update_policy_cannot_replace_frozen_baseline(self) -> None:
        _, created = self.request("POST", "/api/v1/projects", {"name": "策略保护", "route": "template"})
        project_id = created["data"]["id"]

        status, policy = self.request(
            "PUT",
            f"/api/v1/projects/{project_id}/update-policy",
            {"frequency": "daily", "base_digest": "attacker-value", "base_manifest": {"files": [{"path": "fake"}]}},
        )

        self.assertEqual(status, 200)
        self.assertEqual(policy["data"]["frequency"], "daily")
        self.assertEqual(policy["data"]["base_digest"], "")
        self.assertEqual(policy["data"]["base_manifest"], {})

    def test_control_plane_golden_path_and_financial_invariants(self) -> None:
        status, demo = self.request("POST", "/api/v1/control/demo", {})
        status_overview, overview = self.request("GET", "/api/v1/control/overview")
        statement_status, statement = self.request(
            "POST",
            "/api/v1/control/statements",
            {
                "publisher_id": "publisher-demo",
                "cycle_start": "2000-01-01T00:00:00+00:00",
                "cycle_end": "2100-01-01T00:00:00+00:00",
            },
        )
        payout_status, payout = self.request(
            "POST",
            f"/api/v1/control/statements/{statement['data']['id']}/payouts",
            {},
            {"Idempotency-Key": "api-payout-idempotency-001"},
        )
        _, replay = self.request(
            "POST",
            f"/api/v1/control/statements/{statement['data']['id']}/payouts",
            {},
            {"Idempotency-Key": "api-payout-idempotency-001"},
        )
        _, ledger = self.request("GET", "/api/v1/control/ledger")
        _, audit = self.request("GET", "/api/v1/control/audit")

        self.assertEqual(status, 200)
        self.assertEqual(status_overview, 200)
        self.assertEqual(statement_status, 200)
        self.assertEqual(payout_status, 200)
        self.assertEqual(demo["data"]["receipt"]["status"], "qualified")
        self.assertEqual(overview["data"]["ledger_balance_minor"], 0)
        self.assertEqual(overview["data"]["readiness"]["status"], "review")
        self.assertEqual(overview["data"]["readiness"]["score"], 75)
        self.assertEqual(overview["data"]["readiness"]["next_step"]["section"], "security")
        self.assertEqual(payout["data"]["mode"], "sandbox")
        self.assertEqual(payout["data"]["id"], replay["data"]["id"])
        self.assertTrue(ledger["data"]["balanced"])
        self.assertTrue(audit["data"]["integrity"]["valid"])


if __name__ == "__main__":
    unittest.main()
