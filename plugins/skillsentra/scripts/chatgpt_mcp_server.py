#!/usr/bin/env python3
"""Bounded, stateless JSON-response MCP HTTP preview for SkillSentra.

This transport deliberately implements JSON responses only. It reuses the
stdio bridge's tool definitions and execution code, and it never treats a
successful transport exchange as evidence of a real ChatGPT invocation.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from mcp_server import MCPBridge, PROTOCOL_VERSION, _rpc_error


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_REQUEST_BYTES = 1024 * 1024
MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"
DEFAULT_MAX_CONCURRENCY = 16
DEFAULT_SOCKET_TIMEOUT_SECONDS = 15.0
DEFAULT_HEADER_TIMEOUT_SECONDS = 10.0
DEFAULT_BODY_TIMEOUT_SECONDS = 30.0


def _is_loopback_host(host: str) -> bool:
    candidate = host.strip().lower().strip("[]")
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def parse_allowed_origins(raw: str) -> frozenset[str]:
    """Parse explicit HTTP(S) origins without paths, credentials, or secrets."""

    origins: set[str] = set()
    for item in raw.split(","):
        origin = item.strip()
        if not origin:
            continue
        if origin in {"*", "null"}:
            raise ValueError("Wildcard and null origins are not allowed")
        parts = urlsplit(origin)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Allowed origins must be comma-separated HTTP(S) origins without paths or credentials")
        origins.add(origin)
    return frozenset(origins)


def _bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, token = value.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or not 24 <= len(token) <= 4096
        or any(not 0x21 <= ord(character) <= 0x7E for character in token)
    ):
        return None
    return token


def _validated_auth_token(value: str | None) -> str:
    token = value or ""
    if token and (
        not 24 <= len(token) <= 4096
        or token != token.strip()
        or any(not 0x21 <= ord(character) <= 0x7E for character in token)
    ):
        raise ValueError("Configured tokens must contain 24-4096 visible ASCII characters without whitespace")
    return token


def validate_runtime_credentials(
    gateway_token: str | None,
    service_token: str | None,
    *,
    allow_anonymous_loopback: bool,
) -> tuple[str, str]:
    gateway = _validated_auth_token(gateway_token)
    service = _validated_auth_token(service_token)
    if gateway and service and secrets.compare_digest(gateway, service):
        raise ValueError("Gateway and SkillSentra service tokens must be different")
    if allow_anonymous_loopback:
        return gateway, service
    if not gateway:
        raise ValueError("SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN is required")
    if not service:
        raise ValueError("SKILLSENTRA_API_TOKEN is required for authenticated gateway mode")
    return gateway, service


class ChatGPTMCPHTTPServer(ThreadingHTTPServer):
    """Threaded MCP server with bounded workers and request deadlines."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        allowed_origins: frozenset[str] = frozenset(),
        auth_token: str = "",
        allow_anonymous_loopback: bool = False,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        socket_timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
        header_timeout_seconds: float = DEFAULT_HEADER_TIMEOUT_SECONDS,
        body_timeout_seconds: float = DEFAULT_BODY_TIMEOUT_SECONDS,
    ) -> None:
        bind_host = server_address[0]
        loopback = _is_loopback_host(bind_host)
        if allow_anonymous_loopback and not loopback:
            raise ValueError("Anonymous MCP access is allowed only on an explicit loopback listener")
        validated_token = _validated_auth_token(auth_token)
        if not validated_token and not (loopback and allow_anonymous_loopback):
            raise ValueError(
                "SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN is required unless anonymous loopback is explicitly enabled"
            )
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or not 1 <= max_concurrency <= 128:
            raise ValueError("MCP max concurrency must be an integer from 1 to 128")
        timeout_values = {
            "socket timeout": socket_timeout_seconds,
            "header timeout": header_timeout_seconds,
            "body timeout": body_timeout_seconds,
        }
        for label, value in timeout_values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.1 <= float(value) <= 300.0:
                raise ValueError(f"MCP {label} must be from 0.1 to 300 seconds")
        self.allowed_origins = allowed_origins
        self._auth_token = validated_token
        self.allow_anonymous_loopback = loopback and allow_anonymous_loopback
        self.max_concurrency = max_concurrency
        self.socket_timeout_seconds = float(socket_timeout_seconds)
        self.header_timeout_seconds = float(header_timeout_seconds)
        self.body_timeout_seconds = float(body_timeout_seconds)
        self._request_slots = threading.BoundedSemaphore(max_concurrency)
        self._active_requests = 0
        self._active_requests_lock = threading.Lock()
        super().__init__(server_address, ChatGPTMCPHandler)

    @property
    def active_request_count(self) -> int:
        with self._active_requests_lock:
            return self._active_requests

    def get_request(self) -> tuple[socket.socket, Any]:
        request, client_address = super().get_request()
        request.settimeout(self.socket_timeout_seconds)
        return request, client_address

    def _release_request_slot(self) -> None:
        with self._active_requests_lock:
            self._active_requests -= 1
        self._request_slots.release()

    def _reject_busy(self, request: socket.socket) -> None:
        payload = json.dumps(
            _rpc_error(None, -32000, "Server is busy"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            + f"MCP-Protocol-Version: {PROTOCOL_VERSION}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\n"
            + b"Content-Type: application/json; charset=utf-8\r\n"
            + b"Connection: close\r\n"
            + b"Retry-After: 1\r\n"
            + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
            + payload
        )
        try:
            request.sendall(response)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self._reject_busy(request)
            return
        with self._active_requests_lock:
            self._active_requests += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_request_slot()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_request_slot()


class ChatGPTMCPHandler(BaseHTTPRequestHandler):
    """Serve one JSON-RPC message per POST without an SSE fallback."""

    server: ChatGPTMCPHTTPServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        self._deadline_lock = threading.Lock()
        self._deadline_timer: threading.Timer | None = None
        self._deadline_marker: object | None = None
        super().setup()

    def _start_deadline(self, seconds: float) -> None:
        marker = object()
        timer = threading.Timer(seconds, self._expire_deadline, args=(marker,))
        timer.daemon = True
        with self._deadline_lock:
            previous = self._deadline_timer
            self._deadline_marker = marker
            self._deadline_timer = timer
        if previous is not None:
            previous.cancel()
        timer.start()

    def _cancel_deadline(self) -> None:
        with self._deadline_lock:
            timer = self._deadline_timer
            self._deadline_timer = None
            self._deadline_marker = None
        if timer is not None:
            timer.cancel()

    def _expire_deadline(self, marker: object) -> None:
        with self._deadline_lock:
            if marker is not self._deadline_marker:
                return
            self._deadline_timer = None
            self._deadline_marker = None
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def handle_one_request(self) -> None:
        self._start_deadline(self.server.header_timeout_seconds)
        try:
            super().handle_one_request()
        except OSError:
            # Client disconnects and deadline-triggered socket shutdowns are
            # normal transport events, not application failures.
            self.close_connection = True
        finally:
            self._cancel_deadline()

    def parse_request(self) -> bool:
        try:
            return super().parse_request()
        finally:
            self._cancel_deadline()

    def finish(self) -> None:
        self._cancel_deadline()
        super().finish()

    def log_message(self, _format: str, *args: object) -> None:
        # Do not risk logging bearer credentials or request bodies. Operators
        # can observe health through the process state and bounded responses.
        del args

    def _origin(self) -> str | None:
        values = self.headers.get_all("Origin") or []
        return values[0] if len(values) == 1 else None

    def _origin_allowed(self, origin: str | None) -> bool:
        if origin is None:
            return not (self.headers.get_all("Origin") or [])
        allowed = self.server.allowed_origins
        return origin in allowed

    def _response_origin(self, origin: str | None) -> str | None:
        if origin is None or not self._origin_allowed(origin):
            return None
        return origin

    def _send(
        self,
        status: HTTPStatus,
        body: bytes = b"",
        *,
        content_type: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status.value)
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self._origin()
        response_origin = self._response_origin(origin)
        if response_origin:
            self.send_header("Access-Control-Allow-Origin", response_origin)
            if response_origin != "*":
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Expose-Headers", "MCP-Protocol-Version")
        if content_type:
            self.send_header("Content-Type", content_type)
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except OSError:
                pass

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(
            status,
            body,
            content_type="application/json; charset=utf-8",
            extra_headers=extra_headers,
        )

    def _send_http_error(
        self,
        status: HTTPStatus,
        message: str,
        *,
        rpc_code: int = -32600,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send_json(status, _rpc_error(None, rpc_code, message), extra_headers=extra_headers)

    def _reject_before_body(
        self,
        status: HTTPStatus,
        message: str,
        *,
        rpc_code: int = -32600,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Reject a request without leaving unread bytes on a persistent connection."""

        self.close_connection = True
        headers = dict(extra_headers or {})
        headers["Connection"] = "close"
        self._send_http_error(status, message, rpc_code=rpc_code, extra_headers=headers)

    def _authorize(self) -> bool:
        values = self.headers.get_all("Authorization") or []
        if len(values) > 1:
            self._reject_before_body(
                HTTPStatus.UNAUTHORIZED,
                "Bearer authorization failed",
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return False
        authorization = values[0] if values else None
        if authorization is None and self.server.allow_anonymous_loopback:
            return True
        supplied_token = _bearer_token(authorization)
        if (
            supplied_token is None
            or not self.server._auth_token
            or not secrets.compare_digest(supplied_token, self.server._auth_token)
        ):
            self._reject_before_body(
                HTTPStatus.UNAUTHORIZED,
                "Bearer authorization failed",
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return False
        return True

    def _check_origin(self) -> bool:
        origin = self._origin()
        if self._origin_allowed(origin):
            return True
        self._reject_before_body(HTTPStatus.FORBIDDEN, "Origin is not allowed")
        return False

    def _check_protocol_version(self, message: Any) -> bool:
        values = self.headers.get_all("MCP-Protocol-Version") or []
        if len(values) > 1:
            self._send_http_error(
                HTTPStatus.BAD_REQUEST,
                "Multiple MCP-Protocol-Version headers are not allowed",
            )
            return False
        requested = values[0].strip() if values else ""
        method = message.get("method") if isinstance(message, dict) else None
        if method == "initialize" and not requested:
            return True
        if not requested:
            self._send_http_error(
                HTTPStatus.BAD_REQUEST,
                "MCP-Protocol-Version header is required after initialization",
            )
            return False
        if requested != PROTOCOL_VERSION:
            self._send_http_error(
                HTTPStatus.BAD_REQUEST,
                f"Unsupported MCP-Protocol-Version; expected {PROTOCOL_VERSION}",
            )
            return False
        return True

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != MCP_PATH:
            self._send_http_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        if not self._check_origin():
            return
        self._send(
            HTTPStatus.NO_CONTENT,
            extra_headers={
                "Allow": "POST, OPTIONS",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version",
                "Access-Control-Max-Age": "600",
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == HEALTH_PATH:
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if not self._check_origin():
            return
        if self.path == MCP_PATH:
            self._send_http_error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "SSE is not available; send JSON-RPC requests with POST /mcp",
                extra_headers={"Allow": "POST, OPTIONS"},
            )
            return
        self._send_http_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != MCP_PATH:
            self._reject_before_body(HTTPStatus.NOT_FOUND, "Not Found")
            return
        if not self._check_origin():
            return
        if not self._authorize():
            return

        media_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self._reject_before_body(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            return
        if self.headers.get("Transfer-Encoding"):
            self._reject_before_body(HTTPStatus.BAD_REQUEST, "Transfer-Encoding is not supported")
            return
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            self._reject_before_body(HTTPStatus.LENGTH_REQUIRED, "One Content-Length header is required")
            return
        try:
            content_length = int(lengths[0], 10)
        except ValueError:
            self._reject_before_body(HTTPStatus.BAD_REQUEST, "Content-Length must be an integer")
            return
        if content_length < 0:
            self._reject_before_body(HTTPStatus.BAD_REQUEST, "Content-Length must not be negative")
            return
        if content_length > MAX_REQUEST_BYTES:
            self._reject_before_body(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body exceeds the 1 MB limit")
            return

        raw = self._read_request_body(content_length)
        if raw is None:
            return
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, _rpc_error(None, -32700, "Parse error"))
            return
        if not self._check_protocol_version(message):
            return

        is_notification = (
            isinstance(message, dict)
            and message.get("jsonrpc") == "2.0"
            and isinstance(message.get("method"), str)
            and "id" not in message
        )
        try:
            bridge = MCPBridge()
            response = bridge.handle(message)
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                _rpc_error(message.get("id") if isinstance(message, dict) else None, -32603, "Internal error"),
            )
            return

        if is_notification or response is None:
            self._send(HTTPStatus.ACCEPTED)
            return
        self._send_json(HTTPStatus.OK, response)

    def _read_request_body(self, content_length: int) -> bytes | None:
        self._start_deadline(self.server.body_timeout_seconds)
        try:
            raw = self.rfile.read(content_length)
        except OSError:
            self.close_connection = True
            return None
        finally:
            self._cancel_deadline()
        if len(raw) != content_length:
            self.close_connection = True
            return None
        return raw


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    allowed_origins: frozenset[str] = frozenset(),
    auth_token: str = "",
    allow_anonymous_loopback: bool = False,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    socket_timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
    header_timeout_seconds: float = DEFAULT_HEADER_TIMEOUT_SECONDS,
    body_timeout_seconds: float = DEFAULT_BODY_TIMEOUT_SECONDS,
) -> ChatGPTMCPHTTPServer:
    if not host.strip():
        raise ValueError("The MCP listener host must not be empty")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("The MCP listener port must be an integer from 0 to 65535")
    if ":" in host.strip("[]"):
        class IPv6ChatGPTMCPHTTPServer(ChatGPTMCPHTTPServer):
            address_family = socket.AF_INET6

        server_type = IPv6ChatGPTMCPHTTPServer
    else:
        server_type = ChatGPTMCPHTTPServer
    return server_type(
        (host.strip("[]"), port),
        allowed_origins=allowed_origins,
        auth_token=auth_token,
        allow_anonymous_loopback=allow_anonymous_loopback,
        max_concurrency=max_concurrency,
        socket_timeout_seconds=socket_timeout_seconds,
        header_timeout_seconds=header_timeout_seconds,
        body_timeout_seconds=body_timeout_seconds,
    )


def _configured_port() -> int:
    raw = os.environ.get("SKILLSENTRA_CHATGPT_MCP_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw, 10)
    except ValueError as exc:
        raise ValueError("SKILLSENTRA_CHATGPT_MCP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("SKILLSENTRA_CHATGPT_MCP_PORT must be from 1 to 65535")
    return port


def _configured_anonymous_loopback() -> bool:
    raw = os.environ.get("SKILLSENTRA_CHATGPT_ALLOW_ANONYMOUS_LOOPBACK", "").strip().lower()
    if raw in {"", "false"}:
        return False
    if raw == "true":
        return True
    raise ValueError("SKILLSENTRA_CHATGPT_ALLOW_ANONYMOUS_LOOPBACK must be true or false")


def _configured_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be from {minimum} to {maximum}")
    return value


def _configured_seconds(name: str, default: float, *, maximum: float = 300.0) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.1 <= value <= maximum:
        raise ValueError(f"{name} must be from 0.1 to {maximum:g} seconds")
    return value


def main() -> None:
    try:
        host = os.environ.get("SKILLSENTRA_CHATGPT_MCP_HOST", DEFAULT_HOST).strip()
        allowed_origins = parse_allowed_origins(os.environ.get("SKILLSENTRA_CHATGPT_ALLOWED_ORIGINS", ""))
        allow_anonymous = _configured_anonymous_loopback()
        auth_token, _service_token = validate_runtime_credentials(
            os.environ.get("SKILLSENTRA_CHATGPT_MCP_AUTH_TOKEN", ""),
            os.environ.get("SKILLSENTRA_API_TOKEN", ""),
            allow_anonymous_loopback=allow_anonymous,
        )
        server = create_server(
            host,
            _configured_port(),
            allowed_origins=allowed_origins,
            auth_token=auth_token,
            allow_anonymous_loopback=allow_anonymous,
            max_concurrency=_configured_int(
                "SKILLSENTRA_CHATGPT_MCP_MAX_CONCURRENCY",
                DEFAULT_MAX_CONCURRENCY,
                minimum=1,
                maximum=128,
            ),
            socket_timeout_seconds=_configured_seconds(
                "SKILLSENTRA_CHATGPT_MCP_SOCKET_TIMEOUT_SECONDS",
                DEFAULT_SOCKET_TIMEOUT_SECONDS,
            ),
            header_timeout_seconds=_configured_seconds(
                "SKILLSENTRA_CHATGPT_MCP_HEADER_TIMEOUT_SECONDS",
                DEFAULT_HEADER_TIMEOUT_SECONDS,
            ),
            body_timeout_seconds=_configured_seconds(
                "SKILLSENTRA_CHATGPT_MCP_BODY_TIMEOUT_SECONDS",
                DEFAULT_BODY_TIMEOUT_SECONDS,
            ),
        )
    except (OSError, ValueError) as exc:
        print(f"SkillSentra ChatGPT MCP startup failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
