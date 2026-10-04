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


class AuthenticatedAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        cls.token_a = "tenant-a-access-token-1234567890"
        cls.token_b = "tenant-b-access-token-1234567890"
        cls.token_demo = "tenant-demo-access-token-1234567890"
        cls.token_connections = "tenant-connections-token-1234567890"
        cls.token_sources = "tenant-sources-token-1234567890"
        cls.token_viewer = "tenant-a-viewer-token-1234567890"
        registry = json.dumps(
            {
                cls.token_a: {"tenant_id": "ten_alpha", "actor_id": "alpha-admin", "roles": ["admin"]},
                cls.token_b: {"tenant_id": "ten_beta", "actor_id": "beta-admin", "roles": ["admin"]},
                cls.token_demo: {"tenant_id": "ten_demo", "actor_id": "demo-admin", "roles": ["admin"]},
                cls.token_connections: {"tenant_id": "ten_connections", "actor_id": "connections-admin", "roles": ["admin"]},
                cls.token_sources: {"tenant_id": "ten_sources", "actor_id": "sources-admin", "roles": ["admin"]},
                cls.token_viewer: {"tenant_id": "ten_alpha", "actor_id": "alpha-viewer", "roles": ["viewer"]},
            }
        )
        config = AppConfig(
            root_dir=root,
            static_dir=root / "demo-apple",
            database_path=Path(cls.tempdir.name) / "auth.db",
            host="127.0.0.1",
            port=0,
            artifact_dir=Path(cls.tempdir.name) / "artifacts",
            allowed_skill_roots=(root / "sample-skills",),
            auth_mode="token",
            auth_tokens_json=registry,
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

    def request(self, method: str, path: str, token: str = "", payload: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(f"{self.base_url}{path}", method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_token_required_and_projects_are_tenant_scoped(self) -> None:
        unauthenticated_status, unauthenticated = self.request("GET", "/api/v1/projects")
        created_status, created = self.request(
            "POST", "/api/v1/projects", self.token_a, {"name": "Alpha Skill", "route": "template"}
        )
        _, alpha_projects = self.request("GET", "/api/v1/projects", self.token_a)
        _, beta_projects = self.request("GET", "/api/v1/projects", self.token_b)
        cross_status, cross = self.request("GET", f"/api/v1/projects/{created['data']['id']}", self.token_b)

        self.assertEqual(unauthenticated_status, 401)
        self.assertEqual(unauthenticated["error"]["code"], "authentication_required")
        self.assertEqual(created_status, 200)
        self.assertEqual(len(alpha_projects["data"]), 1)
        self.assertEqual(beta_projects["data"], [])
        self.assertEqual(cross_status, 404)
        self.assertEqual(cross["error"]["code"], "project_not_found")

    def test_health_remains_available_to_probes(self) -> None:
        status, body = self.request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["status"], "ok")

    def test_provider_diagnostics_require_admin_and_public_status_is_aggregate(self) -> None:
        public_status, public_body = self.request("GET", "/api/v1/auth/providers")
        denied_status, denied_body = self.request(
            "GET", "/api/v1/admin/auth/providers", self.token_viewer
        )
        admin_status, admin_body = self.request(
            "GET", "/api/v1/admin/auth/providers", self.token_a
        )

        public_rendered = json.dumps(public_body, ensure_ascii=False)
        self.assertEqual(public_status, 200)
        self.assertNotIn("SKILLSENTRA_", public_rendered)
        self.assertNotIn("missing_configuration", public_rendered)
        self.assertEqual(denied_status, 403)
        self.assertEqual(denied_body["error"]["code"], "forbidden")
        self.assertEqual(admin_status, 200)
        google = admin_body["data"]["providers"]["google"]
        self.assertIn("SKILLSENTRA_GOOGLE_CLIENT_ID", google["missing_configuration"])
        self.assertTrue(google["required_configuration"])

    def test_production_readiness_is_admin_only_and_fail_closed(self) -> None:
        denied_status, denied_body = self.request(
            "GET", "/api/v1/control/production-readiness", self.token_viewer
        )
        status, body = self.request("GET", "/api/v1/control/production-readiness", self.token_a)
        self.assertEqual(denied_status, 403)
        self.assertEqual(denied_body["error"]["code"], "forbidden")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["status"], "no_go")
        self.assertFalse(body["data"]["release_authorized"])
        self.assertIn("environment", body["data"]["blocking_checks"])
        self.assertNotIn("auth_tokens_json", json.dumps(body))

    def test_read_only_role_cannot_mutate_projects(self) -> None:
        status, body = self.request(
            "POST", "/api/v1/projects", self.token_viewer, {"name": "Forbidden Skill", "route": "template"}
        )

        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden")

    def test_ai_connections_and_policy_binding_do_not_cross_tenants(self) -> None:
        _, alpha_bootstrap = self.request("GET", "/api/v1/bootstrap", self.token_connections)
        _, beta_bootstrap = self.request("GET", "/api/v1/bootstrap", self.token_b)
        alpha_connection = alpha_bootstrap["data"]["default_connection"]
        beta_connection = beta_bootstrap["data"]["default_connection"]
        _, alpha_project = self.request(
            "POST", "/api/v1/projects", self.token_connections, {"name": "Connection isolation", "route": "template"}
        )

        _, beta_connections = self.request("GET", "/api/v1/ai/connections", self.token_b)
        models_status, models = self.request(
            "GET", f"/api/v1/ai/connections/{alpha_connection['id']}/models", self.token_b
        )
        test_status, tested = self.request(
            "POST", f"/api/v1/ai/connections/{alpha_connection['id']}/test", self.token_b, {}
        )
        bind_status, bound = self.request(
            "PUT",
            f"/api/v1/projects/{alpha_project['data']['id']}/ai-policy",
            self.token_connections,
            {"connection_id": beta_connection["id"]},
        )
        delete_status, deleted = self.request(
            "DELETE", f"/api/v1/ai/connections/{alpha_connection['id']}", self.token_b
        )

        self.assertNotEqual(alpha_connection["id"], beta_connection["id"])
        self.assertEqual([item["id"] for item in beta_connections["data"]], [beta_connection["id"]])
        for status, body in ((models_status, models), (test_status, tested), (bind_status, bound), (delete_status, deleted)):
            self.assertEqual(status, 404)
            self.assertEqual(body["error"]["code"], "connection_not_found")

    def test_server_local_skill_roots_are_not_implicitly_shared(self) -> None:
        _, demo_bootstrap = self.request("GET", "/api/v1/bootstrap", self.token_demo)
        _, beta_bootstrap = self.request("GET", "/api/v1/bootstrap", self.token_sources)
        _, beta_project = self.request(
            "POST", "/api/v1/projects", self.token_sources, {"name": "Source isolation", "route": "existing"}
        )
        source = demo_bootstrap["data"]["skill_sources"][0]
        import_status, imported = self.request(
            "POST",
            f"/api/v1/projects/{beta_project['data']['id']}/import",
            self.token_sources,
            {"source_id": source["id"]},
        )

        self.assertEqual(beta_bootstrap["data"]["skill_sources"], [])
        self.assertEqual(import_status, 404)
        self.assertEqual(imported["error"]["code"], "skill_source_not_found")


if __name__ == "__main__":
    unittest.main()
