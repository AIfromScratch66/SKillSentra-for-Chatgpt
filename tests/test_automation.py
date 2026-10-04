from __future__ import annotations

import json
import os
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.automation import ScanAutomationService, WebsiteSkillDiscovery, _PinnedAddress, _PinnedHTTPSConnection
from app.control_plane import ControlPlane, Principal
from app.database import Database
from app.errors import AppError
from app.repository import Repository
from app.skill_engine import SkillEngine


PUBLIC_DNS_RESULT = [
    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
]


class FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []
        self.connected_to: tuple[object, ...] | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def connect(self, sockaddr: tuple[object, ...]) -> None:
        self.connected_to = sockaddr

    def close(self) -> None:
        self.closed = True


class FakeTLSContext:
    post_handshake_auth = False

    def __init__(self) -> None:
        self.calls: list[tuple[FakeSocket, str]] = []
        self.secure_socket = object()

    def wrap_socket(self, raw_socket: FakeSocket, *, server_hostname: str) -> object:
        self.calls.append((raw_socket, server_hostname))
        return self.secure_socket


class FakeWebsiteResponse:
    def __init__(
        self,
        payload: object | None = None,
        *,
        body: bytes | None = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.body = body if body is not None else json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.offset = 0

    def read1(self, amount: int) -> bytes:
        chunk = self.body[self.offset:self.offset + amount]
        self.offset += len(chunk)
        return chunk


class FakeWebsiteConnection:
    def __init__(
        self,
        hostname: str,
        address: _PinnedAddress,
        *,
        timeout: float,
        context: ssl.SSLContext,
        response: FakeWebsiteResponse,
    ):
        self.hostname = hostname
        self.address = address
        self.timeout = timeout
        self.context = context
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.sock = FakeSocket()
        self.closed = False

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        self.requests.append((method, target, headers))

    def getresponse(self) -> FakeWebsiteResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class ScanAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.database = Database(root / "automation.db")
        self.database.migrate()
        self.repository = Repository(self.database)
        self.control = ControlPlane(self.database, self.repository, SkillEngine(self.repository, root / "artifacts", ()))
        self.service = ScanAutomationService(self.database, self.control)
        self.principal = Principal("ten_automation", "admin-test", frozenset({"admin", "operator"}), "token")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_github_target_can_be_scheduled_paused_and_run(self) -> None:
        target = self.service.create_target(self.principal, {
            "name": "Trusted catalog", "target_type": "github", "url": "https://github.com/example/skills",
            "requested_ref": "main", "interval_minutes": 60, "auto_enabled": True,
        })
        with patch.object(self.control, "create_repository", return_value={"id": "repo_fixture"}), patch.object(
            self.control, "scan_repository", return_value={
                "id": "snap_fixture", "status": "scanned", "artifact_digest": f"sha256:{'c' * 64}",
            },
        ):
            run = self.service.run_target(self.principal, target["id"])

        paused = self.service.update_target(self.principal, target["id"], {"auto_enabled": False})
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["scanned_count"], 1)
        self.assertFalse(paused["auto_enabled"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(len(self.service.list_runs(self.principal)), 1)

    def test_website_discovery_records_partial_result(self) -> None:
        target = self.service.create_target(self.principal, {
            "name": "Partner index", "target_type": "website", "url": "https://skills.example.com/catalog.json",
            "interval_minutes": 1440, "auto_enabled": False,
        })
        self.assertEqual(target["status"], "paused")
        candidates = [
            {"url": "https://github.com/example/one", "ref": "main"},
            {"url": "https://github.com/example/two", "ref": "stable"},
        ]

        def create_repository(_principal: Principal, payload: dict) -> dict:
            if payload["url"].endswith("/two"):
                raise AppError("repository_unavailable", "Fixture repository unavailable.", 502)
            return {"id": "repo_one"}

        with patch.object(self.service.discovery, "discover", return_value=candidates), patch.object(
            self.control, "create_repository", side_effect=create_repository,
        ), patch.object(self.control, "scan_repository", return_value={
            "id": "snap_one", "status": "review_required", "artifact_digest": f"sha256:{'d' * 64}",
        }):
            run = self.service.run_target(self.principal, target["id"])

        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["discovered_count"], 2)
        self.assertEqual(run["scanned_count"], 1)
        self.assertEqual(run["result"]["errors"][0]["code"], "repository_unavailable")

    def test_source_validation_rejects_unsafe_addresses_and_intervals(self) -> None:
        with self.assertRaises(AppError) as context:
            self.service.create_target(self.principal, {
                "name": "Bad target", "target_type": "github", "url": "http://github.com/example/skills",
            })
        self.assertEqual(context.exception.code, "invalid_github_url")
        with self.assertRaises(AppError) as context:
            self.service.create_target(self.principal, {
                "name": "Too frequent", "target_type": "website", "url": "https://skills.example.com",
                "interval_minutes": 5,
            })
        self.assertEqual(context.exception.code, "invalid_scan_interval")
        with self.assertRaises(AppError) as context:
            self.service.discovery._assert_public_destination("https://127.0.0.1/catalog")
        self.assertEqual(context.exception.code, "website_private_address")

    def test_website_transport_pins_one_dns_resolution_and_ignores_https_proxy(self) -> None:
        resolver_calls: list[tuple[object, ...]] = []

        def rebinding_resolver(*args: object) -> list[tuple[object, ...]]:
            resolver_calls.append(args)
            if len(resolver_calls) == 1:
                return PUBLIC_DNS_RESULT
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))]

        response = FakeWebsiteResponse({
            "skills": [{
                "repository": "https://github.com/example/pinned-skill",
                "ref": "stable",
            }],
        })
        connections: list[FakeWebsiteConnection] = []

        def connection_factory(
            hostname: str,
            address: _PinnedAddress,
            *,
            timeout: float,
            context: ssl.SSLContext,
        ) -> FakeWebsiteConnection:
            connection = FakeWebsiteConnection(
                hostname,
                address,
                timeout=timeout,
                context=context,
                response=response,
            )
            connections.append(connection)
            return connection

        with (
            patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:8080"}),
            patch("urllib.request.build_opener", side_effect=AssertionError("proxy-aware opener used")),
        ):
            service = WebsiteSkillDiscovery(
                timeout=2,
                resolver=rebinding_resolver,
                connection_factory=connection_factory,
            )
            results = service.discover("https://skills.example.com/catalog.json?source=beta")

        self.assertEqual(results, [{"url": "https://github.com/example/pinned-skill", "ref": "stable"}])
        self.assertEqual(len(resolver_calls), 1)
        self.assertEqual(len(connections), 1)
        connection = connections[0]
        self.assertEqual(connection.hostname, "skills.example.com")
        self.assertEqual(connection.address.sockaddr, ("93.184.216.34", 443))
        self.assertGreater(connection.timeout, 0)
        self.assertLessEqual(connection.timeout, 2)
        self.assertTrue(connection.context.check_hostname)
        self.assertEqual(connection.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(connection.requests[0][0:2], ("GET", "/catalog.json?source=beta"))
        self.assertEqual(connection.requests[0][2]["Accept-Encoding"], "identity")
        self.assertEqual(connection.requests[0][2]["Connection"], "close")
        self.assertTrue(connection.closed)

    def test_website_transport_rejects_mixed_public_private_dns_before_connect(self) -> None:
        connection_calls: list[tuple[object, ...]] = []

        def connection_factory(*args: object, **kwargs: object) -> FakeWebsiteConnection:
            connection_calls.append((*args, kwargs))
            raise AssertionError("unsafe mixed resolution reached the network")

        service = WebsiteSkillDiscovery(
            resolver=lambda *_args: [
                *[
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (f"8.8.8.{index}", 443))
                    for index in range(1, 9)
                ],
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.7", 443)),
            ],
            connection_factory=connection_factory,
        )

        with self.assertRaises(AppError) as context:
            service.discover("https://skills.example.com/catalog.json")

        self.assertEqual(context.exception.code, "website_private_address")
        self.assertEqual(connection_calls, [])

    def test_website_transport_rejects_redirect_without_following(self) -> None:
        response = FakeWebsiteResponse(
            body=b"",
            status=302,
            headers={"Location": "https://127.0.0.1/private"},
        )
        connections: list[FakeWebsiteConnection] = []

        def connection_factory(
            hostname: str,
            address: _PinnedAddress,
            *,
            timeout: float,
            context: ssl.SSLContext,
        ) -> FakeWebsiteConnection:
            connection = FakeWebsiteConnection(
                hostname,
                address,
                timeout=timeout,
                context=context,
                response=response,
            )
            connections.append(connection)
            return connection

        service = WebsiteSkillDiscovery(
            resolver=lambda *_args: PUBLIC_DNS_RESULT,
            connection_factory=connection_factory,
        )
        with self.assertRaises(AppError) as context:
            service.discover("https://skills.example.com/catalog.json")

        self.assertEqual(context.exception.code, "website_redirect_blocked")
        self.assertEqual(len(connections), 1)
        self.assertEqual(len(connections[0].requests), 1)
        self.assertTrue(connections[0].closed)

    def test_website_transport_preserves_tls_hostname_for_pinned_public_address(self) -> None:
        address = _PinnedAddress(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            ("93.184.216.34", 443),
        )
        raw_socket = FakeSocket()
        context = FakeTLSContext()
        connection = _PinnedHTTPSConnection(
            "skills.example.com",
            address,
            timeout=2,
            context=context,  # type: ignore[arg-type]
        )

        with (
            patch("app.automation.socket.socket", return_value=raw_socket) as socket_factory,
            patch("app.automation.socket.getaddrinfo", side_effect=AssertionError("hostname was resolved twice")),
        ):
            connection.connect()

        socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self.assertEqual(raw_socket.connected_to, ("93.184.216.34", 443))
        self.assertEqual(context.calls, [(raw_socket, "skills.example.com")])
        self.assertIs(connection.sock, context.secure_socket)

    def test_website_response_read_obeys_total_deadline_and_size_limit(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.now = 0.0

            def __call__(self) -> float:
                return self.now

        class SlowResponse(FakeWebsiteResponse):
            def __init__(self, clock: FakeClock) -> None:
                super().__init__(body=b'{"skills":[]}')
                self.clock = clock

            def read1(self, amount: int) -> bytes:
                self.clock.now += 0.6
                return super().read1(min(amount, 1))

        def service_for(response: FakeWebsiteResponse, **kwargs: object) -> WebsiteSkillDiscovery:
            def factory(
                hostname: str,
                address: _PinnedAddress,
                *,
                timeout: float,
                context: ssl.SSLContext,
            ) -> FakeWebsiteConnection:
                return FakeWebsiteConnection(
                    hostname,
                    address,
                    timeout=timeout,
                    context=context,
                    response=response,
                )

            return WebsiteSkillDiscovery(
                resolver=lambda *_args: PUBLIC_DNS_RESULT,
                connection_factory=factory,
                **kwargs,
            )

        clock = FakeClock()
        with self.assertRaises(AppError) as context:
            service_for(SlowResponse(clock), timeout=1, clock=clock).discover(
                "https://skills.example.com/catalog.json"
            )
        self.assertEqual(context.exception.code, "website_scan_timeout")
        self.assertEqual(context.exception.status, 504)

        with self.assertRaises(AppError) as context:
            service_for(FakeWebsiteResponse(body=b"x" * 33), response_limit=32).discover(
                "https://skills.example.com/catalog.json"
            )
        self.assertEqual(context.exception.code, "website_response_too_large")

    def test_manual_due_run_is_scoped_to_the_callers_tenant(self) -> None:
        self.service.create_target(self.principal, {
            "name": "Tenant one", "target_type": "github", "url": "https://github.com/example/tenant-one",
            "interval_minutes": 60, "auto_enabled": True,
        })
        other = Principal("ten_other_automation", "other-admin", self.principal.roles, "token")
        self.service.create_target(other, {
            "name": "Tenant two", "target_type": "github", "url": "https://github.com/example/tenant-two",
            "interval_minutes": 60, "auto_enabled": True,
        })
        with patch.object(self.service, "run_target", return_value={"status": "succeeded"}) as runner:
            result = self.service.run_due(10, self.principal.tenant_id)

        self.assertEqual(len(result), 1)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.args[0].tenant_id, self.principal.tenant_id)

    def test_stale_running_scan_is_recovered_after_restart(self) -> None:
        target = self.service.create_target(self.principal, {
            "name": "Interrupted target", "target_type": "github", "url": "https://github.com/example/interrupted",
            "interval_minutes": 60, "auto_enabled": True,
        })
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO scan_runs(id, tenant_id, target_id, status, started_at) VALUES ('scanrun_stale', ?, ?, 'running', '2000-01-01T00:00:00+00:00')",
                (self.principal.tenant_id, target["id"]),
            )
            connection.execute("UPDATE scan_targets SET status='running', updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (target["id"],))

        with patch.object(self.service, "run_target", return_value={"status": "succeeded"}):
            self.service.run_due(1, self.principal.tenant_id)
        with self.database.session() as connection:
            run = connection.execute("SELECT * FROM scan_runs WHERE id='scanrun_stale'").fetchone()
            refreshed = connection.execute("SELECT * FROM scan_targets WHERE id=?", (target["id"],)).fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "scan_interrupted")
        self.assertEqual(refreshed["status"], "warning")


if __name__ == "__main__":
    unittest.main()
