from __future__ import annotations

import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.config import AppConfig
from app.server import create_server


class AccountAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        config = AppConfig(
            root_dir=root,
            static_dir=root / "demo-apple",
            database_path=Path(cls.tempdir.name) / "accounts-api.db",
            host="127.0.0.1",
            port=0,
            artifact_dir=Path(cls.tempdir.name) / "artifacts",
            allowed_skill_roots=(root / "sample-skills",),
            auth_mode="accounts",
            automation_enabled=False,
        )
        cls.server = create_server(config)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.cookies = http.cookiejar.CookieJar()
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cls.cookies))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.tempdir.cleanup()

    def request(
        self, method: str, path: str, payload: dict | None = None, csrf: bool = False, anonymous: bool = False,
    ) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if csrf:
            token = next((cookie.value for cookie in self.cookies if cookie.name == "skillsentra_csrf"), "")
            headers["X-CSRF-Token"] = token
        request = urllib.request.Request(f"{self.base_url}{path}", method=method, data=data, headers=headers)
        opener = urllib.request.build_opener() if anonymous else self.opener
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_account_session_csrf_personal_project_and_logout(self) -> None:
        status, registration = self.request("POST", "/api/v1/auth/register", {
            "email": "api-user@example.com", "display_name": "API User", "password": "StrongApiPass123",
        })
        self.assertEqual(status, 200)
        self.assertEqual(registration["data"]["user"]["roles"], ["buyer", "creator", "finance"])

        no_csrf_status, no_csrf = self.request("POST", "/api/v1/projects", {"name": "Blocked", "route": "template"})
        self.assertEqual(no_csrf_status, 403)
        self.assertEqual(no_csrf["error"]["code"], "csrf_required")

        created_status, created = self.request(
            "POST", "/api/v1/projects", {"name": "Personal Skill", "route": "template"}, csrf=True,
        )
        session_status, session = self.request("GET", "/api/v1/session")
        self.assertEqual(created_status, 200)
        self.assertEqual(session_status, 200)
        self.assertEqual(created["data"]["owner_user_id"], session["data"]["actor_id"])

        public_status, leaderboard = self.request("GET", "/api/v1/marketplace/leaderboard", anonymous=True)
        self.assertEqual(public_status, 200)
        self.assertEqual(leaderboard["data"], [])

        logout_status, _ = self.request("POST", "/api/v1/auth/logout", {}, csrf=True)
        after_status, after = self.request("GET", "/api/v1/session")
        self.assertEqual(logout_status, 200)
        self.assertEqual(after_status, 401)
        self.assertEqual(after["error"]["code"], "authentication_required")

    def test_provider_status_is_public_aggregate_and_unconfigured_google_fails_closed(self) -> None:
        status, providers = self.request("GET", "/api/v1/auth/providers", anonymous=True)
        self.assertEqual(status, 200)
        google = providers["data"]["providers"]["google"]
        chatgpt = providers["data"]["providers"]["chatgpt"]
        self.assertFalse(google["available"])
        self.assertEqual(google["reason_code"], "provider_unavailable")
        self.assertFalse(chatgpt["available"])
        self.assertEqual(chatgpt["reason_code"], "external_access_required")
        rendered = json.dumps(providers, ensure_ascii=False)
        self.assertNotIn("SKILLSENTRA_", rendered)
        self.assertNotIn("required_configuration", rendered)
        self.assertNotIn("missing_configuration", rendered)
        self.assertNotIn("next_steps", rendered)
        start_status, start = self.request("GET", "/api/v1/auth/google/start", anonymous=True)
        self.assertEqual(start_status, 503)
        self.assertEqual(start["error"]["code"], "provider_not_configured")


if __name__ == "__main__":
    unittest.main()
