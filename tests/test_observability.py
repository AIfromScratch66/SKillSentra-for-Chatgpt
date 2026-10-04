from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app.config import AppConfig
from app.database import Database
from app.errors import AppError
from app.server import RequestMetrics, create_server, health_snapshot


class ObservabilityAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        config = AppConfig(
            root_dir=root,
            static_dir=root / "demo-apple",
            database_path=Path(cls.tempdir.name) / "observability.db",
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

    def request(self, path: str) -> tuple[int, dict]:
        request = urllib.request.Request(f"{self.base_url}{path}", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_liveness_readiness_and_health_contract(self) -> None:
        live_status, live = self.request("/livez")
        ready_status, ready = self.request("/readyz")
        health_status, health = self.request("/api/health")

        self.assertEqual(live_status, 200)
        self.assertEqual(live["data"], {"status": "ok", "service": "skillsentra", "version": "0.8.0"})
        self.assertEqual(ready_status, 200)
        self.assertEqual(health_status, 200)
        self.assertEqual(ready["data"], health["data"])
        self.assertEqual(
            set(health["data"]),
            {"status", "service", "version", "database", "schema_version"},
        )

    def test_readiness_fails_but_liveness_survives_database_failure(self) -> None:
        with patch.object(Database, "session", side_effect=OSError("fixture database unavailable")):
            ready_status, ready = self.request("/readyz")
            live_status, live = self.request("/livez")
            metrics_status, metrics = self.request("/metrics")

        self.assertEqual(ready_status, 503)
        self.assertEqual(ready["error"]["code"], "database_unavailable")
        self.assertEqual(live_status, 200)
        self.assertEqual(live["data"]["status"], "ok")
        self.assertEqual(metrics_status, 200)
        self.assertEqual(metrics["data"]["status"], "ok")

    def test_metrics_are_bounded_and_do_not_retain_request_values(self) -> None:
        secret_fixture = "sensitive-query-fixture"
        with self.assertLogs("skillsentra", level="INFO") as captured_logs:
            missing_status, _missing = self.request(f"/api/v1/not-present?token={secret_fixture}")
            metrics_status, metrics = self.request(f"/metrics?ignored={secret_fixture}")
        snapshot = metrics["data"]

        self.assertEqual(missing_status, 404)
        self.assertEqual(metrics_status, 200)
        self.assertGreaterEqual(snapshot["requests"]["total"], 1)
        self.assertGreaterEqual(snapshot["requests"]["by_method"]["GET"], 1)
        self.assertGreaterEqual(snapshot["requests"]["by_status"]["4xx"], 1)
        self.assertGreaterEqual(snapshot["requests"]["latency_ms"]["average"], 0)
        self.assertGreaterEqual(snapshot["requests"]["latency_ms"]["maximum"], 0)
        self.assertNotIn(secret_fixture, json.dumps(snapshot, ensure_ascii=False))
        self.assertNotIn(secret_fixture, "\n".join(captured_logs.output))
        self.assertNotIn("path", snapshot["requests"])

    def test_malformed_request_neither_leaks_request_line_nor_crashes_handler(self) -> None:
        secret_fixture = "malformed-secret-query-fixture"
        captured_stderr = io.StringIO()
        with self.assertLogs("skillsentra", level="WARNING") as captured_logs:
            with contextlib.redirect_stderr(captured_stderr):
                with socket.create_connection(self.server.server_address, timeout=5) as client:
                    client.sendall(
                        f"GET /?token={secret_fixture} EXTRA HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii")
                    )
                    client.shutdown(socket.SHUT_WR)
                    response = bytearray()
                    while True:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        response.extend(chunk)

        combined_logs = "\n".join(captured_logs.output)
        self.assertIn(b" 400 ", bytes(response).split(b"\r\n", 1)[0])
        self.assertNotIn(secret_fixture, combined_logs)
        self.assertNotIn(secret_fixture, captured_stderr.getvalue())
        self.assertEqual(captured_stderr.getvalue(), "")
        live_status, live = self.request("/livez")
        self.assertEqual(live_status, 200)
        self.assertEqual(live["data"]["status"], "ok")

    def test_container_healthcheck_uses_readiness_not_liveness(self) -> None:
        compose = (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("http://127.0.0.1:8766/readyz", compose)
        self.assertIn("d['data']['database']=='ready'", compose)
        self.assertNotIn("http://127.0.0.1:8766/api/health", compose)

    def test_readiness_rejects_a_missing_expected_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            database = Database(Path(temp_name) / "migration-gap.db")
            database.migrate()
            expected = database.expected_migration_versions()
            with database.transaction() as connection:
                connection.execute("DELETE FROM schema_migrations WHERE version=?", (expected[-1],))

            with self.assertRaises(AppError) as context:
                health_snapshot(database)

        self.assertEqual(context.exception.code, "database_migration_mismatch")
        self.assertEqual(context.exception.status, 503)
        self.assertEqual(context.exception.details["missing_versions"], [expected[-1]])

    def test_unknown_http_method_is_not_retained_in_request_logs(self) -> None:
        secret_method = "SECRET_METHOD_ABC"
        with self.assertLogs("skillsentra", level="INFO") as captured_logs:
            with socket.create_connection(self.server.server_address, timeout=5) as client:
                client.sendall(f"{secret_method} / HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))
                client.shutdown(socket.SHUT_WR)
                while client.recv(4096):
                    pass

        combined_logs = "\n".join(captured_logs.output)
        self.assertNotIn(secret_method, combined_logs)
        self.assertIn("method=OTHER", combined_logs)


class RequestMetricsTests(unittest.TestCase):
    def test_unknown_methods_collapse_to_one_bounded_dimension(self) -> None:
        metrics = RequestMetrics()
        metrics.begin()
        metrics.finish("CUSTOM-sensitive-value", 204, 0.012)

        snapshot = metrics.snapshot()["requests"]
        self.assertEqual(snapshot["by_method"], {"OTHER": 1})
        self.assertEqual(snapshot["by_status"]["2xx"], 1)
        self.assertEqual(snapshot["in_flight"], 0)
        self.assertEqual(RequestMetrics.normalize_method("CUSTOM-sensitive-value"), "OTHER")


if __name__ == "__main__":
    unittest.main()
