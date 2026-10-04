from __future__ import annotations

import io
import json
import unittest
import urllib.error

from bridge.skillsentra_bridge import BridgeClient, BridgeClientConfig, BridgeClientError


class FakeResponse:
    def __init__(self, payload: dict, content_length: str | None = None):
        self.body = io.BytesIO(json.dumps(payload).encode("utf-8"))
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.requests = []

    def open(self, request, timeout: float):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class BridgeClientTests(unittest.TestCase):
    def test_non_loopback_http_and_url_credentials_are_rejected(self) -> None:
        with self.assertRaises(BridgeClientError) as insecure:
            BridgeClient(BridgeClientConfig("http://example.com"))
        self.assertEqual(insecure.exception.code, "https_required")

        with self.assertRaises(BridgeClientError) as credential:
            BridgeClient(BridgeClientConfig("https://user:secret@example.com"))
        self.assertEqual(credential.exception.code, "unsafe_base_url")

    def test_register_and_heartbeat_are_outbound_api_calls(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse({"data": {"id": "brg_test", "connection_mode": "outbound-only"}}),
                FakeResponse({"data": {"id": "brg_test", "status": "online"}}),
            ]
        )
        client = BridgeClient(
            BridgeClientConfig("https://skillsentra.example", "test-token-never-print"), opener
        )

        registered = client.register(
            "Windows Bridge",
            "windows",
            capabilities={"local.files": "unsupported"},
        )
        heartbeat = client.heartbeat(
            registered["id"], observed_state={"runtime": "python", "pending_jobs": 0}
        )

        register_request = opener.requests[0][0]
        heartbeat_request = opener.requests[1][0]
        self.assertEqual(register_request.full_url, "https://skillsentra.example/api/v1/control/bridges")
        self.assertEqual(register_request.get_header("Authorization"), "Bearer test-token-never-print")
        self.assertEqual(
            heartbeat_request.full_url,
            "https://skillsentra.example/api/v1/control/bridges/brg_test/heartbeat",
        )
        self.assertEqual(registered["connection_mode"], "outbound-only")
        self.assertEqual(heartbeat["status"], "online")

    def test_server_error_does_not_echo_token_or_body(self) -> None:
        class ErrorOpener:
            def open(self, request, timeout: float):
                del request, timeout
                raise urllib.error.HTTPError(
                    "https://skillsentra.example/api/v1/control/bridges",
                    500,
                    "secret server body test-token-never-print",
                    {},
                    None,
                )

        client = BridgeClient(
            BridgeClientConfig("https://skillsentra.example", "test-token-never-print"),
            ErrorOpener(),
        )
        with self.assertRaises(BridgeClientError) as error:
            client.register("Bridge", "windows")

        self.assertEqual(error.exception.status, 500)
        self.assertNotIn("test-token-never-print", error.exception.message)
        self.assertNotIn("secret server body", error.exception.message)


if __name__ == "__main__":
    unittest.main()
