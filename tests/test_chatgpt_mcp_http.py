from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "skillsentra" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import chatgpt_mcp_server as transport  # noqa: E402
from mcp_server import PROTOCOL_VERSION, TOOLS  # noqa: E402


GATEWAY_TOKEN = "gateway-auth-token-1234567890"
SERVICE_TOKEN = "upstream-service-token-0987654321"


class UpstreamHandler(BaseHTTPRequestHandler):
    authorizations: list[str | None] = []

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.__class__.authorizations.append(self.headers.get("Authorization"))
        raw = json.dumps({"data": {"status": "ok"}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def running(server: ThreadingHTTPServer) -> Iterator[tuple[str, int]]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def rpc(method: str, request_id: int | None = 1, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        payload["id"] = request_id
    if method == "initialize" and params is None:
        params = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "skillsentra-http-unittest", "version": "1"},
        }
    if params is not None:
        payload["params"] = params
    return payload


def exchange(
    address: tuple[str, int],
    method: str,
    path: str,
    payload: dict[str, Any] | bytes | None = None,
    headers: dict[str, str] | None = None,
    *,
    use_default_auth: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    body = payload
    request_headers = dict(headers or {})
    if isinstance(payload, dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("MCP-Protocol-Version", PROTOCOL_VERSION)
    if method == "POST" and path == "/mcp" and use_default_auth:
        request_headers.setdefault("Authorization", f"Bearer {GATEWAY_TOKEN}")
    connection = http.client.HTTPConnection(address[0], address[1], timeout=5)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, raw
    finally:
        connection.close()


def exchange_with_authorizations(
    address: tuple[str, int],
    payload: dict[str, Any],
    authorizations: list[str],
) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection(address[0], address[1], timeout=5)
    try:
        connection.putrequest("POST", "/mcp")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.putheader("MCP-Protocol-Version", PROTOCOL_VERSION)
        for authorization in authorizations:
            connection.putheader("Authorization", authorization)
        connection.endheaders(body)
        response = connection.getresponse()
        raw = response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, raw
    finally:
        connection.close()


class ChatGPTMCPHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        UpstreamHandler.authorizations = []

    def test_runtime_requires_separate_gateway_and_service_tokens(self) -> None:
        with self.assertRaisesRegex(ValueError, "MCP_AUTH_TOKEN"):
            transport.validate_runtime_credentials("", SERVICE_TOKEN, allow_anonymous_loopback=False)
        with self.assertRaisesRegex(ValueError, "API_TOKEN"):
            transport.validate_runtime_credentials(GATEWAY_TOKEN, "", allow_anonymous_loopback=False)
        with self.assertRaisesRegex(ValueError, "different"):
            transport.validate_runtime_credentials(GATEWAY_TOKEN, GATEWAY_TOKEN, allow_anonymous_loopback=False)
        self.assertEqual(
            transport.validate_runtime_credentials(
                GATEWAY_TOKEN,
                SERVICE_TOKEN,
                allow_anonymous_loopback=False,
            ),
            (GATEWAY_TOKEN, SERVICE_TOKEN),
        )
        self.assertEqual(
            transport.validate_runtime_credentials("", "", allow_anonymous_loopback=True),
            ("", ""),
        )

    def test_listener_defaults_require_auth_and_anonymous_is_loopback_only(self) -> None:
        self.assertEqual((transport.DEFAULT_HOST, transport.DEFAULT_PORT), ("127.0.0.1", 8787))
        with self.assertRaisesRegex(ValueError, "AUTH_TOKEN"):
            transport.create_server("127.0.0.1", 0)
        with self.assertRaisesRegex(ValueError, "AUTH_TOKEN"):
            transport.create_server("0.0.0.0", 0)
        with self.assertRaisesRegex(ValueError, "loopback"):
            transport.create_server("0.0.0.0", 0, allow_anonymous_loopback=True)

        loopback = transport.create_server(
            "127.0.0.1",
            0,
            allow_anonymous_loopback=True,
        )
        external = transport.create_server("0.0.0.0", 0, auth_token=GATEWAY_TOKEN)
        try:
            self.assertTrue(loopback.allow_anonymous_loopback)
            self.assertFalse(external.allow_anonymous_loopback)
            self.assertEqual(external._auth_token, GATEWAY_TOKEN)
        finally:
            loopback.server_close()
            external.server_close()

    def test_anonymous_loopback_environment_requires_literal_true(self) -> None:
        with patch.dict(os.environ, {"SKILLSENTRA_CHATGPT_ALLOW_ANONYMOUS_LOOPBACK": "true"}):
            self.assertTrue(transport._configured_anonymous_loopback())
        with patch.dict(os.environ, {"SKILLSENTRA_CHATGPT_ALLOW_ANONYMOUS_LOOPBACK": "1"}):
            with self.assertRaisesRegex(ValueError, "true or false"):
                transport._configured_anonymous_loopback()

    def test_initialize_ping_and_tools_list_reuse_stdio_contract(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address:
            status, headers, raw = exchange(address, "POST", "/mcp", rpc("initialize"))
            self.assertEqual(status, 200)
            self.assertEqual(headers["mcp-protocol-version"], PROTOCOL_VERSION)
            initialized = json.loads(raw)["result"]
            self.assertEqual(initialized["protocolVersion"], PROTOCOL_VERSION)

            status, _, raw = exchange(address, "POST", "/mcp", rpc("ping", 2))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["result"], {})

            status, _, raw = exchange(address, "POST", "/mcp", rpc("tools/list", 3))
            self.assertEqual(status, 200)
            tools = json.loads(raw)["result"]["tools"]
            self.assertEqual(len(tools), 19)
            self.assertEqual(tools, TOOLS)

    def test_initialize_requires_protocol_capabilities_and_client_info(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        invalid_params = (
            None,
            {},
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}},
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": [],
                "clientInfo": {"name": "client", "version": "1"},
            },
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "client", "version": ""},
            },
        )
        with running(server) as address:
            for params in invalid_params:
                with self.subTest(params=params):
                    message = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
                    if params is not None:
                        message["params"] = params
                    status, _, raw = exchange(address, "POST", "/mcp", message)
                    self.assertEqual(status, 200)
                    error = json.loads(raw)["error"]
                    self.assertEqual(error["code"], -32602)
                    self.assertEqual(error["message"], "Invalid initialize params")

    def test_notification_returns_202_without_a_response_body(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address:
            status, headers, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("notifications/initialized", request_id=None),
            )
        self.assertEqual(status, 202)
        self.assertEqual(raw, b"")
        self.assertEqual(headers["mcp-protocol-version"], PROTOCOL_VERSION)

    def test_protocol_version_is_required_after_initialize_and_must_match(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address:
            status, _, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("initialize"),
                headers={"MCP-Protocol-Version": ""},
            )
            self.assertEqual(status, 200)

            status, _, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("tools/list"),
                headers={"MCP-Protocol-Version": ""},
            )
            self.assertEqual(status, 400)
            self.assertIn("required", json.loads(raw)["error"]["message"])

            status, _, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("tools/list"),
                headers={"MCP-Protocol-Version": "2024-11-05"},
            )
            self.assertEqual(status, 400)
            self.assertIn("Unsupported", json.loads(raw)["error"]["message"])

    def test_get_is_405_because_sse_is_not_exposed(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address:
            status, headers, raw = exchange(address, "GET", "/mcp")
        self.assertEqual(status, 405)
        self.assertEqual(headers["allow"], "POST, OPTIONS")
        self.assertEqual(headers["mcp-protocol-version"], PROTOCOL_VERSION)
        self.assertIn("SSE is not available", json.loads(raw)["error"]["message"])

    def test_request_body_is_limited_to_one_megabyte(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address:
            status, _, raw = exchange(
                address,
                "POST",
                "/mcp",
                b"{}",
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(transport.MAX_REQUEST_BYTES + 1),
                },
            )
        self.assertEqual(status, 413)
        self.assertIn("1 MB", json.loads(raw)["error"]["message"])

    def test_prebody_rejection_closes_the_same_socket_before_unread_bytes_can_be_reused(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address, socket.create_connection(address, timeout=5) as connection:
            valid_body = json.dumps(rpc("ping", 2), separators=(",", ":")).encode("utf-8")
            first = (
                "POST /mcp HTTP/1.1\r\n"
                f"Host: {address[0]}:{address[1]}\r\n"
                f"Authorization: Bearer {GATEWAY_TOKEN}\r\n"
                "Content-Type: text/plain\r\n"
                "Content-Length: 2\r\n"
                "\r\n"
            ).encode("ascii") + b"{}"
            second = (
                "POST /mcp HTTP/1.1\r\n"
                f"Host: {address[0]}:{address[1]}\r\n"
                f"Authorization: Bearer {GATEWAY_TOKEN}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(valid_body)}\r\n"
                f"MCP-Protocol-Version: {PROTOCOL_VERSION}\r\n"
                "\r\n"
            ).encode("ascii") + valid_body
            connection.sendall(first + second)

            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 415)
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()
            response.close()

            connection.settimeout(2)
            try:
                trailing = connection.recv(4096)
            except ConnectionResetError:
                trailing = b""
            self.assertEqual(trailing, b"")

    def test_configured_origin_controls_cors_and_preflight(self) -> None:
        origin = "https://chatgpt.com"
        server = transport.create_server(
            "127.0.0.1",
            0,
            allowed_origins=transport.parse_allowed_origins(origin),
            auth_token=GATEWAY_TOKEN,
        )
        with running(server) as address:
            status, headers, _ = exchange(
                address,
                "OPTIONS",
                "/mcp",
                headers={"Origin": origin},
            )
            self.assertEqual(status, 204)
            self.assertEqual(headers["access-control-allow-origin"], origin)
            self.assertIn("Authorization", headers["access-control-allow-headers"])

            status, headers, _ = exchange(
                address,
                "POST",
                "/mcp",
                rpc("ping"),
                headers={"Origin": origin},
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers["access-control-allow-origin"], origin)

            status, _, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("ping"),
                headers={"Origin": "https://example.invalid"},
            )
            self.assertEqual(status, 403)
            self.assertEqual(json.loads(raw)["error"]["message"], "Origin is not allowed")

            status, headers, raw = exchange(
                address,
                "GET",
                "/mcp",
                headers={"Origin": "https://example.invalid"},
                use_default_auth=False,
            )
            self.assertEqual(status, 403)
            self.assertEqual(headers["connection"], "close")
            self.assertEqual(json.loads(raw)["error"]["message"], "Origin is not allowed")

    def test_correct_gateway_token_is_required_by_default(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address:
            status, headers, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("tools/list"),
                use_default_auth=False,
            )
            self.assertEqual(status, 401)
            self.assertEqual(headers["www-authenticate"], "Bearer")
            self.assertEqual(json.loads(raw)["error"]["message"], "Bearer authorization failed")

            status, _, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("tools/list"),
                headers={"Authorization": "Bearer wrong-token"},
            )
            self.assertEqual(status, 401)
            self.assertNotIn("wrong-token", raw.decode("utf-8"))

            status, _, raw = exchange(address, "POST", "/mcp", rpc("tools/list"))
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(raw)["result"]["tools"]), 19)

    def test_inbound_token_is_not_forwarded_and_upstream_uses_only_service_token(self) -> None:
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        with running(upstream) as upstream_address:
            upstream_url = f"http://{upstream_address[0]}:{upstream_address[1]}"
            with patch.dict(
                os.environ,
                {
                    "SKILLSENTRA_BASE_URL": upstream_url,
                    "SKILLSENTRA_TIMEOUT_SECONDS": "3",
                    "SKILLSENTRA_API_TOKEN": SERVICE_TOKEN,
                },
            ):
                server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
                with running(server) as address:
                    status, _, raw = exchange(
                        address,
                        "POST",
                        "/mcp",
                        rpc("tools/call", params={"name": "health", "arguments": {}}),
                    )

        self.assertEqual(status, 200)
        self.assertEqual(UpstreamHandler.authorizations, [f"Bearer {SERVICE_TOKEN}"])
        self.assertTrue(json.loads(raw)["result"]["structuredContent"]["ok"])
        self.assertNotIn(GATEWAY_TOKEN, raw.decode("utf-8"))
        self.assertNotIn(SERVICE_TOKEN, raw.decode("utf-8"))

    def test_wrong_malformed_and_duplicate_bearers_never_reach_upstream(self) -> None:
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        with running(upstream) as upstream_address:
            upstream_url = f"http://{upstream_address[0]}:{upstream_address[1]}"
            with patch.dict(
                os.environ,
                {
                    "SKILLSENTRA_BASE_URL": upstream_url,
                    "SKILLSENTRA_TIMEOUT_SECONDS": "3",
                    "SKILLSENTRA_API_TOKEN": SERVICE_TOKEN,
                },
            ):
                server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
                with running(server) as address:
                    attempts = [
                        exchange(
                            address,
                            "POST",
                            "/mcp",
                            rpc("tools/call", params={"name": "health", "arguments": {}}),
                            headers={"Authorization": "Bearer wrong-secret"},
                        ),
                        exchange(
                            address,
                            "POST",
                            "/mcp",
                            rpc("tools/call", params={"name": "health", "arguments": {}}),
                            headers={"Authorization": "Basic malformed-secret"},
                        ),
                        exchange_with_authorizations(
                            address,
                            rpc("tools/call", params={"name": "health", "arguments": {}}),
                            [f"Bearer {GATEWAY_TOKEN}", f"Bearer {GATEWAY_TOKEN}"],
                        ),
                    ]

        self.assertEqual([status for status, _, _ in attempts], [401, 401, 401])
        self.assertEqual(UpstreamHandler.authorizations, [])
        combined = b"".join(raw for _, _, raw in attempts).decode("utf-8")
        for secret in ("wrong-secret", "malformed-secret", GATEWAY_TOKEN, SERVICE_TOKEN):
            self.assertNotIn(secret, combined)

    def test_explicit_anonymous_loopback_allows_no_authorization_header(self) -> None:
        server = transport.create_server(
            "127.0.0.1",
            0,
            allow_anonymous_loopback=True,
        )
        with running(server) as address:
            status, _, raw = exchange(
                address,
                "POST",
                "/mcp",
                rpc("ping"),
                use_default_auth=False,
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["result"], {})

    def test_healthz_is_minimal_and_does_not_require_a_bearer(self) -> None:
        server = transport.create_server("127.0.0.1", 0, auth_token=GATEWAY_TOKEN)
        with running(server) as address:
            status, headers, raw = exchange(
                address,
                "GET",
                "/healthz",
                use_default_auth=False,
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"status": "ok"})
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["mcp-protocol-version"], PROTOCOL_VERSION)

    def test_concurrency_limit_returns_bounded_busy_response(self) -> None:
        server = transport.create_server(
            "127.0.0.1",
            0,
            auth_token=GATEWAY_TOKEN,
            max_concurrency=1,
            header_timeout_seconds=5,
        )
        with running(server) as address, socket.create_connection(address, timeout=5) as held:
            held.sendall(b"GET /healthz HTTP/1.1\r\nHost: incomplete")
            deadline = time.monotonic() + 2
            while server.active_request_count != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(server.active_request_count, 1)

            status, headers, raw = exchange(
                address,
                "GET",
                "/healthz",
                use_default_auth=False,
            )

        self.assertEqual(status, 503)
        self.assertEqual(headers["connection"], "close")
        self.assertEqual(headers["retry-after"], "1")
        self.assertEqual(json.loads(raw)["error"]["message"], "Server is busy")

    def test_header_and_body_deadlines_close_slow_connections(self) -> None:
        server = transport.create_server(
            "127.0.0.1",
            0,
            auth_token=GATEWAY_TOKEN,
            socket_timeout_seconds=1,
            header_timeout_seconds=0.2,
            body_timeout_seconds=0.2,
        )
        with running(server) as address:
            with socket.create_connection(address, timeout=2) as slow_header:
                slow_header.sendall(b"GET /healthz HTTP/1.1\r\nHost: incomplete")
                slow_header.settimeout(2)
                try:
                    trailing = slow_header.recv(4096)
                except ConnectionResetError:
                    trailing = b""
                self.assertEqual(trailing, b"")

            body = json.dumps(rpc("ping"), separators=(",", ":")).encode("utf-8")
            request_headers = (
                "POST /mcp HTTP/1.1\r\n"
                f"Host: {address[0]}:{address[1]}\r\n"
                f"Authorization: Bearer {GATEWAY_TOKEN}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"MCP-Protocol-Version: {PROTOCOL_VERSION}\r\n"
                "\r\n"
            ).encode("ascii")
            with socket.create_connection(address, timeout=2) as slow_body:
                slow_body.sendall(request_headers + body[:1])
                slow_body.settimeout(2)
                try:
                    trailing = slow_body.recv(4096)
                except ConnectionResetError:
                    trailing = b""
                self.assertEqual(trailing, b"")

    def test_availability_environment_values_are_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SKILLSENTRA_CHATGPT_MCP_MAX_CONCURRENCY": "7",
                "SKILLSENTRA_CHATGPT_MCP_HEADER_TIMEOUT_SECONDS": "2.5",
            },
        ):
            self.assertEqual(
                transport._configured_int(
                    "SKILLSENTRA_CHATGPT_MCP_MAX_CONCURRENCY",
                    transport.DEFAULT_MAX_CONCURRENCY,
                    minimum=1,
                    maximum=128,
                ),
                7,
            )
            self.assertEqual(
                transport._configured_seconds(
                    "SKILLSENTRA_CHATGPT_MCP_HEADER_TIMEOUT_SECONDS",
                    transport.DEFAULT_HEADER_TIMEOUT_SECONDS,
                ),
                2.5,
            )

        with patch.dict(os.environ, {"SKILLSENTRA_CHATGPT_MCP_MAX_CONCURRENCY": "0"}):
            with self.assertRaisesRegex(ValueError, "from 1 to 128"):
                transport._configured_int(
                    "SKILLSENTRA_CHATGPT_MCP_MAX_CONCURRENCY",
                    transport.DEFAULT_MAX_CONCURRENCY,
                    minimum=1,
                    maximum=128,
                )
        with patch.dict(os.environ, {"SKILLSENTRA_CHATGPT_MCP_BODY_TIMEOUT_SECONDS": "nan"}):
            with self.assertRaisesRegex(ValueError, "from 0.1"):
                transport._configured_seconds(
                    "SKILLSENTRA_CHATGPT_MCP_BODY_TIMEOUT_SECONDS",
                    transport.DEFAULT_BODY_TIMEOUT_SECONDS,
                )

    def test_origin_configuration_rejects_unsafe_values(self) -> None:
        for value in (
            "https://chatgpt.com/path",
            "https://user:secret@chatgpt.com",
            "*",
            "null",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    transport.parse_allowed_origins(value)


if __name__ == "__main__":
    unittest.main()
