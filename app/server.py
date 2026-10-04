from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import __version__  # type: ignore
    from app.ai_gateway import AIGateway, PROVIDERS  # type: ignore
    from app.host_collaboration import HostCollaboration  # type: ignore
    from app.accounts import AccountRegistrationPending, AccountService, AccountSession, SMTPEmailDelivery  # type: ignore
    from app.automation import ScanAutomationService, ScanScheduler  # type: ignore
    from app.config import AppConfig  # type: ignore
    from app.control_plane import ControlPlane, Principal, TokenAuthenticator  # type: ignore
    from app.database import Database  # type: ignore
    from app.discovery import GitHubSkillCandidateCatalog, SkillDiscoveryService  # type: ignore
    from app.edge_control import EdgeControl  # type: ignore
    from app.errors import AppError  # type: ignore
    from app.expert_matrix import expert_framework, validate_expert_review  # type: ignore
    from app.marketplace import MarketplaceService  # type: ignore
    from app.repository import Repository  # type: ignore
    from app.production import evaluate_production_readiness  # type: ignore
    from app.skill_engine import SkillEngine  # type: ignore
    from app.workflow import validate_step_contract  # type: ignore
else:
    from . import __version__
    from .ai_gateway import AIGateway, PROVIDERS
    from .host_collaboration import HostCollaboration
    from .accounts import AccountRegistrationPending, AccountService, AccountSession, SMTPEmailDelivery
    from .automation import ScanAutomationService, ScanScheduler
    from .config import AppConfig
    from .control_plane import ControlPlane, Principal, TokenAuthenticator
    from .database import Database
    from .discovery import GitHubSkillCandidateCatalog, SkillDiscoveryService
    from .edge_control import EdgeControl
    from .errors import AppError
    from .expert_matrix import expert_framework, validate_expert_review
    from .marketplace import MarketplaceService
    from .repository import Repository
    from .production import evaluate_production_readiness
    from .skill_engine import SkillEngine
    from .workflow import validate_step_contract


@dataclass(frozen=True)
class FileDownload:
    path: Path
    filename: str


@dataclass(frozen=True)
class SessionResponse:
    data: dict[str, Any]
    cookies: tuple[str, ...]


@dataclass(frozen=True)
class RedirectResponse:
    location: str
    cookies: tuple[str, ...] = ()


LOGGER = logging.getLogger("skillsentra")


def health_snapshot(database: Database) -> dict[str, Any]:
    """Return readiness only after SQLite and the migration ledger respond."""

    try:
        expected_migrations = database.expected_migration_versions()
        if not expected_migrations:
            raise RuntimeError("no application migrations are available")
        with database.session() as connection:
            connection.execute("SELECT 1").fetchone()
            applied_migrations = {
                str(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
    except Exception as exc:
        raise AppError("database_unavailable", "数据库健康检查失败。", HTTPStatus.SERVICE_UNAVAILABLE) from exc
    expected_set = set(expected_migrations)
    missing = sorted(expected_set - applied_migrations)
    unexpected = sorted(applied_migrations - expected_set)
    if missing or unexpected:
        raise AppError(
            "database_migration_mismatch",
            "数据库迁移状态与当前应用版本不匹配。",
            HTTPStatus.SERVICE_UNAVAILABLE,
            {"missing_versions": missing, "unexpected_versions": unexpected},
        )
    return {
        "status": "ok",
        "service": "skillsentra",
        "version": __version__,
        "database": "ready",
        "schema_version": expected_migrations[-1],
    }


class RequestMetrics:
    """Keep bounded process-local request totals without retaining request data."""

    _KNOWN_METHODS = frozenset({"DELETE", "GET", "OPTIONS", "POST", "PUT"})

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._total = 0
        self._in_flight = 0
        self._by_method: dict[str, int] = {}
        self._by_status = {"1xx": 0, "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
        self._latency_total_ms = 0.0
        self._latency_last_ms = 0.0
        self._latency_max_ms = 0.0

    def begin(self) -> None:
        with self._lock:
            self._in_flight += 1

    @classmethod
    def normalize_method(cls, method: str) -> str:
        normalized = method.upper()
        return normalized if normalized in cls._KNOWN_METHODS else "OTHER"

    def finish(self, method: str, status: int, elapsed_seconds: float) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            if not method:
                return
            normalized_method = self.normalize_method(method)
            status_class = f"{status // 100}xx" if 100 <= status <= 599 else "other"
            latency_ms = max(0.0, elapsed_seconds * 1000.0)
            self._total += 1
            self._by_method[normalized_method] = self._by_method.get(normalized_method, 0) + 1
            self._by_status[status_class] += 1
            self._latency_total_ms += latency_ms
            self._latency_last_ms = latency_ms
            self._latency_max_ms = max(self._latency_max_ms, latency_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            average_ms = self._latency_total_ms / self._total if self._total else 0.0
            return {
                "status": "ok",
                "service": "skillsentra",
                "version": __version__,
                "uptime_seconds": round(max(0.0, time.monotonic() - self._started_at), 3),
                "requests": {
                    "total": self._total,
                    "in_flight": self._in_flight,
                    "by_method": dict(sorted(self._by_method.items())),
                    "by_status": dict(self._by_status),
                    "latency_ms": {
                        "average": round(average_ms, 3),
                        "last": round(self._latency_last_ms, 3),
                        "maximum": round(self._latency_max_ms, 3),
                    },
                },
            }


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with a hard concurrency ceiling for the beta profile."""

    daemon_threads = True
    block_on_close = True
    request_queue_size = 128

    def __init__(self, server_address: tuple[str, int], handler: type[SimpleHTTPRequestHandler], max_workers: int = 64):
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        self._background_services: list[Any] = []
        super().__init__(server_address, handler)

    def add_background_service(self, service: Any) -> None:
        self._background_services.append(service)
        service.start()

    def server_close(self) -> None:
        for service in reversed(self._background_services):
            service.stop()
        self._background_services.clear()
        super().server_close()

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            try:
                body = b'{"error":{"code":"server_busy","message":"Service capacity is exhausted."}}'
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                    b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
                )
            finally:
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def validate_project_payload(payload: dict[str, Any]) -> tuple[str, str]:
    name = str(payload.get("name") or "未命名 Skill").strip()
    route = str(payload.get("route") or "template")
    if not name or len(name) > 120:
        raise AppError("invalid_project_name", "项目名称必须为 1–120 个字符。")
    if route not in {"template", "existing"}:
        raise AppError("invalid_project_route", "创建路线必须是 template 或 existing。")
    return name, route


def validate_ai_policy_payload(
    payload: dict[str, Any],
    repository: Repository,
    tenant_id: str = "ten_demo",
) -> dict[str, Any]:
    normalized = dict(payload)
    mode = normalized.get("mode")
    if mode is not None and mode not in {"suggest", "auto", "manual"}:
        raise AppError("invalid_ai_mode", "AI 运行方式必须是 suggest、auto 或 manual。")
    for field in ("enabled", "fallback_enabled", "redact_enabled", "external_files_enabled"):
        if field in normalized and not isinstance(normalized[field], bool):
            raise AppError("invalid_ai_boolean", f"{field} 必须是布尔值。")
    for field in ("creator_model", "evaluator_model", "safety_model"):
        if field in normalized:
            value = str(normalized[field]).strip()
            if not value or len(value) > 160:
                raise AppError("invalid_ai_model", "模型名称必须为 1–160 个字符。")
            normalized[field] = value
    if "daily_budget" in normalized:
        try:
            budget = float(normalized["daily_budget"])
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_ai_budget", "每日预算必须是有效数字。") from exc
        if not math.isfinite(budget) or budget < 0 or budget > 1_000_000:
            raise AppError("invalid_ai_budget", "每日预算必须在 0–1,000,000 之间。")
        normalized["daily_budget"] = budget
    connection_id = normalized.get("connection_id")
    if connection_id is not None and repository.get_connection(str(connection_id), tenant_id) is None:
        raise AppError("connection_not_found", "模型连接不存在。", 404)
    return normalized


def make_handler(
    config: AppConfig,
    repository: Repository,
    gateway: AIGateway,
    skill_engine: SkillEngine,
    control_plane: ControlPlane,
    authenticator: TokenAuthenticator,
    accounts: AccountService,
    marketplace: MarketplaceService,
    automation: ScanAutomationService,
    discovery: SkillDiscoveryService,
    github_candidates: GitHubSkillCandidateCatalog,
    edge_control: EdgeControl,
) -> type[SimpleHTTPRequestHandler]:
    rate_lock = threading.Lock()
    rate_windows: dict[str, tuple[float, int]] = {}
    request_metrics = RequestMetrics()
    host_collaboration = HostCollaboration(repository)

    class SkillSentraHandler(SimpleHTTPRequestHandler):
        server_version = "SkillSentra/0.6"

        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, directory=str(config.static_dir), **kwargs)

        def handle_one_request(self) -> None:
            started_at = time.perf_counter()
            self._observed_status = HTTPStatus.INTERNAL_SERVER_ERROR
            request_metrics.begin()
            try:
                super().handle_one_request()
            finally:
                request_metrics.finish(
                    str(getattr(self, "command", "")),
                    int(self._observed_status),
                    time.perf_counter() - started_at,
                )

        def send_response(self, code: int, message: str | None = None) -> None:
            self._observed_status = int(code)
            super().send_response(code, message)

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-DNS-Prefetch-Control", "off")
            request_headers = getattr(self, "headers", None)
            if request_headers is not None and request_headers.get("X-Forwarded-Proto", "").lower() == "https":
                self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'",
            )
            # SimpleHTTPRequestHandler otherwise allows heuristic browser caching for
            # HTML/JS/CSS. A refreshed external tab could therefore keep an old route
            # handler even though the server is serving the new build. Preserve the
            # explicit API header and add the same no-store policy to static assets.
            buffered_headers = getattr(self, "_headers_buffer", ())
            if not any(b"cache-control:" in chunk.lower() for chunk in buffered_headers if isinstance(chunk, bytes)):
                self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Allow", "GET, POST, PUT, OPTIONS")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            probe_path = urlparse(self.path).path.rstrip("/") or "/"
            if probe_path in {"/livez", "/readyz", "/metrics"}:
                # Probes intentionally bypass application auth for local orchestrators.
                # The supported compose profile publishes only on 127.0.0.1; any
                # non-loopback ingress must keep these paths network-restricted.
                self._dispatch_probe(probe_path)
                return
            if not probe_path.startswith("/api/"):
                if probe_path == "/":
                    self.path = "/marketplace.html"
                elif probe_path == "/studio.html":
                    self.path = "/index.html"
                return super().do_GET()
            self._dispatch_api("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch_api("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch_api("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch_api("DELETE")

        def _dispatch_probe(self, path: str) -> None:
            request_id = f"req_{uuid.uuid4().hex[:12]}"
            try:
                if path == "/livez":
                    data = {"status": "ok", "service": "skillsentra", "version": __version__}
                elif path == "/readyz":
                    data = health_snapshot(repository.database)
                else:
                    data = request_metrics.snapshot()
                self._send_json(HTTPStatus.OK, {"data": data, "meta": {"request_id": request_id}})
            except AppError as exc:
                self._send_json(
                    exc.status,
                    {"error": {"code": exc.code, "message": exc.message, "details": exc.details}, "meta": {"request_id": request_id}},
                )
            except Exception:
                LOGGER.exception("probe_failed request_id=%s", request_id)
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": {"code": "internal_error", "message": "服务探针执行失败。", "details": {}}, "meta": {"request_id": request_id}},
                )

        def _dispatch_api(self, method: str) -> None:
            request_id = f"req_{uuid.uuid4().hex[:12]}"
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                query = parse_qs(parsed.query)
                session_token = self._cookie("skillsentra_session")
                self.account_session: AccountSession | None = accounts.authenticate(session_token) if session_token else None
                public = self._is_public_route(method, path)
                if path == "/api/health":
                    principal = Principal("ten_health", "health-probe", frozenset(), "public")
                elif public:
                    principal = Principal("ten_public", "anonymous", frozenset(), "public")
                    auth_request = path in {
                        "/api/v1/auth/register",
                        "/api/v1/auth/login",
                        "/api/v1/auth/password-reset/request",
                        "/api/v1/auth/password-reset/complete",
                        "/api/v1/auth/verify-email/resend",
                        "/api/v1/auth/verify-email",
                    }
                    self._check_rate_limit(principal, 20 if auth_request else None, "auth" if auth_request else "public")
                    self._check_origin(method)
                elif self.account_session:
                    principal = self.account_session.principal
                    self._check_rate_limit(principal)
                    self._check_origin(method)
                    if method in {"POST", "PUT", "DELETE"}:
                        accounts.verify_csrf(session_token, self.headers.get("X-CSRF-Token", ""))
                else:
                    principal = authenticator.authenticate(self.headers.get("Authorization", ""))
                    self._check_rate_limit(principal)
                    self._check_origin(method)
                payload = self._read_json() if method in {"POST", "PUT"} else {}
                data = self._route(method, path, query, payload, principal)
                if isinstance(data, FileDownload):
                    self._send_download(data)
                    return
                if isinstance(data, RedirectResponse):
                    self._send_redirect(data.location, data.cookies)
                    return
                if isinstance(data, SessionResponse):
                    self._send_json(HTTPStatus.OK, {"data": data.data, "meta": {"request_id": request_id}}, data.cookies)
                    return
                self._send_json(HTTPStatus.OK, {"data": data, "meta": {"request_id": request_id}})
            except AppError as exc:
                self._send_json(
                    exc.status,
                    {"error": {"code": exc.code, "message": exc.message, "details": exc.details}, "meta": {"request_id": request_id}},
                )
            except KeyError as exc:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": {"code": str(exc).strip("'"), "message": "请求的资源不存在。", "details": {}}, "meta": {"request_id": request_id}},
                )
            except Exception:
                LOGGER.exception(
                    "request_failed request_id=%s method=%s",
                    request_id,
                    RequestMetrics.normalize_method(method),
                )
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": {"code": "internal_error", "message": "服务器处理请求时发生错误。", "details": {}}, "meta": {"request_id": request_id}},
                )

        @staticmethod
        def _is_public_route(method: str, path: str) -> bool:
            if path == "/api/health":
                return True
            if method == "POST" and path in {"/api/v1/auth/register", "/api/v1/auth/login"}:
                return True
            if method == "GET" and path in {
                "/api/v1/auth/providers",
                "/api/v1/auth/google/start",
                "/api/v1/auth/google/callback",
                "/api/v1/auth/chatgpt/start",
                "/api/v1/auth/chatgpt/callback",
                "/api/v1/auth/verify-email",
            }:
                return True
            if method == "POST" and path in {
                "/api/v1/auth/password-reset/request",
                "/api/v1/auth/password-reset/complete",
                "/api/v1/auth/verify-email/resend",
            }:
                return True
            if method == "GET" and (
                path in {"/api/v1/marketplace/publications", "/api/v1/marketplace/leaderboard", "/api/v1/discovery/github/top-skills"}
                or re.fullmatch(r"/api/v1/marketplace/publications/[^/]+", path)
            ):
                return True
            return False

        def _route(
            self,
            method: str,
            path: str,
            query: dict[str, list[str]],
            payload: dict[str, Any],
            principal: Principal,
        ) -> Any:
            if method == "GET" and path == "/api/health":
                return health_snapshot(repository.database)

            if principal.auth_mode != "public":
                control_plane.ensure_tenant(principal)

            if method == "GET" and path == "/api/v1/auth/providers":
                return {"providers": accounts.providers(detailed=False)}

            if method == "GET" and path == "/api/v1/admin/auth/providers":
                principal.require("admin", "operator")
                return {"providers": accounts.providers(detailed=True)}

            if method == "GET" and path == "/api/v1/auth/google/start":
                authorization_url, state = accounts.begin_google_login()
                return SessionResponse({"authorization_url": authorization_url}, (self._oauth_state_cookie(state),))

            if method == "GET" and path == "/api/v1/auth/google/callback":
                try:
                    session = accounts.finish_google_login(
                        (query.get("code") or [""])[0], (query.get("state") or [""])[0], self._cookie("skillsentra_oauth_state"),
                    )
                    return RedirectResponse("/marketplace.html?signin=google", self._account_cookies(session) + self._clear_oauth_state_cookie())
                except AppError as exc:
                    return RedirectResponse(f"/marketplace.html?auth_error={quote(exc.code, safe='')}", self._clear_oauth_state_cookie())

            if method == "GET" and path == "/api/v1/auth/chatgpt/start":
                authorization_url, state, verifier = accounts.begin_chatgpt_login()
                return SessionResponse(
                    {"authorization_url": authorization_url},
                    (self._oauth_state_cookie(state), self._oauth_verifier_cookie(verifier)),
                )

            if method == "GET" and path == "/api/v1/auth/chatgpt/callback":
                try:
                    session = accounts.finish_chatgpt_login(
                        (query.get("code") or [""])[0],
                        (query.get("state") or [""])[0],
                        self._cookie("skillsentra_oauth_state"),
                        self._cookie("skillsentra_oauth_verifier"),
                    )
                    return RedirectResponse(
                        "/marketplace.html?signin=chatgpt",
                        self._account_cookies(session) + self._clear_oauth_state_cookie(),
                    )
                except AppError as exc:
                    return RedirectResponse(
                        f"/marketplace.html?auth_error={quote(exc.code, safe='')}",
                        self._clear_oauth_state_cookie(),
                    )

            if method == "GET" and path == "/api/v1/auth/verify-email":
                accounts.verify_email((query.get("token") or [""])[0])
                return RedirectResponse("/marketplace.html?verified=1")

            if method == "POST" and path == "/api/v1/auth/register":
                result = accounts.register(payload)
                if isinstance(result, AccountRegistrationPending):
                    return {
                        "authenticated": False,
                        "verification_required": result.verification_required,
                        "user": result.user,
                    }
                return self._account_response(result)

            if method == "POST" and path == "/api/v1/auth/login":
                return self._account_response(accounts.login(payload))

            if method == "POST" and path == "/api/v1/auth/logout":
                accounts.logout(self._cookie("skillsentra_session"))
                return SessionResponse({"signed_out": True}, self._clear_account_cookies())

            if method == "POST" and path == "/api/v1/auth/password-reset/request":
                return accounts.request_password_reset(payload)

            if method == "POST" and path == "/api/v1/auth/verify-email/resend":
                return accounts.resend_email_verification(payload)

            if method == "POST" and path == "/api/v1/auth/password-reset/complete":
                return accounts.reset_password(payload)

            if method == "GET" and path == "/api/v1/session":
                control_plane.ensure_tenant(principal)
                return {
                    "authenticated": True,
                    "actor_id": principal.actor_id,
                    "tenant_id": principal.tenant_id,
                    "roles": sorted(principal.roles),
                    "auth_mode": principal.auth_mode,
                    "user": self.account_session.user if self.account_session else None,
                }

            if method == "GET" and path == "/api/v1/marketplace/publications":
                return marketplace.list_publications(query)

            if method == "GET" and path == "/api/v1/marketplace/leaderboard":
                return marketplace.leaderboard(self._query_limit(query, 10, 50))

            if method == "GET" and path == "/api/v1/discovery/github/top-skills":
                items = github_candidates.list_top_100()
                return {"source_query": github_candidates.QUERY, "count": len(items), "items": items, "verification_status": "unverified_candidate"}

            match = re.fullmatch(r"/api/v1/marketplace/publications/([^/]+)", path)
            if method == "GET" and match:
                return marketplace.get_publication(match.group(1))

            if method == "GET" and path == "/api/v1/marketplace/creator":
                return marketplace.creator_dashboard(principal)

            if method == "POST" and path == "/api/v1/marketplace/publications":
                return marketplace.publish(principal, payload)

            match = re.fullmatch(r"/api/v1/marketplace/publications/([^/]+)", path)
            if method == "PUT" and match:
                return marketplace.update_publication(principal, match.group(1), payload)

            match = re.fullmatch(r"/api/v1/marketplace/publications/([^/]+)/purchase", path)
            if method == "POST" and match:
                return marketplace.purchase(principal, match.group(1), payload, self.headers.get("Idempotency-Key", ""))

            match = re.fullmatch(r"/api/v1/marketplace/publications/([^/]+)/reviews", path)
            if method == "POST" and match:
                return marketplace.review(principal, match.group(1), payload)

            if method == "GET" and path == "/api/v1/marketplace/library":
                return marketplace.library(principal)

            if path == "/api/v1/marketplace/statements":
                principal.require("finance")
                if method == "GET":
                    return control_plane.list_statements(principal)
                if method == "POST":
                    statement_payload = dict(payload)
                    statement_payload["publisher_id"] = principal.actor_id
                    return control_plane.create_statement(principal, statement_payload)

            match = re.fullmatch(r"/api/v1/marketplace/statements/([^/]+)/payouts", path)
            if method == "POST" and match:
                principal.require("finance")
                return control_plane.submit_payout(principal, match.group(1), self.headers.get("Idempotency-Key", ""))

            if path == "/api/v1/admin/scan-targets":
                if method == "GET":
                    return automation.list_targets(principal)
                if method == "POST":
                    return automation.create_target(principal, payload)

            match = re.fullmatch(r"/api/v1/admin/scan-targets/([^/]+)", path)
            if method == "PUT" and match:
                return automation.update_target(principal, match.group(1), payload)

            match = re.fullmatch(r"/api/v1/admin/scan-targets/([^/]+)/run", path)
            if method == "POST" and match:
                return automation.run_target(principal, match.group(1))

            if method == "GET" and path == "/api/v1/admin/scan-runs":
                return automation.list_runs(principal, self._query_limit(query, 100, 300))

            if method == "POST" and path == "/api/v1/admin/scan-runs/run-due":
                principal.require("admin")
                return automation.run_due(self._payload_limit(payload, 10, 50), principal.tenant_id)

            if method == "POST" and path == "/api/v1/discovery/search":
                principal.require("operator", "creator")
                provider = str(payload.get("provider") or "github")
                query_text = str(payload.get("query") or "")
                limit = payload.get("limit", 10)
                if provider == "github":
                    items = discovery.search_github(query_text, limit)
                elif provider == "catalog":
                    items = discovery.search_catalog(str(payload.get("catalog_url") or ""), query_text, limit)
                else:
                    raise AppError("invalid_discovery_provider", "发现来源必须是 github 或 catalog。")
                return {
                    "provider": provider,
                    "query": query_text.strip(),
                    "count": len(items),
                    "items": items,
                    "verification_status": "unverified_candidate",
                }

            if method == "POST" and path == "/api/v1/discovery/github/top-skills":
                principal.require("admin")
                return github_candidates.refresh_top_100()

            project_scope = re.match(r"^/api/v1/projects/([^/]+)(?:/|$)", path)
            if project_scope:
                self._require_project_access(principal, project_scope.group(1))

            if method == "GET" and path == "/api/v1/bootstrap":
                mock_connection = gateway.ensure_mock_connection(principal.tenant_id)
                sources = skill_engine.discover_sources(principal.tenant_id)
                return {
                    "providers": PROVIDERS,
                    "routes": {"template": 9, "existing": 7},
                    "expert_framework": expert_framework(),
                    "default_connection": mock_connection,
                    "connections": repository.list_connections(principal.tenant_id),
                    "projects": repository.list_projects(principal.tenant_id)[:20],
                    "skill_sources": [self._public_source(item) for item in sources],
                    "pricebook": {
                        "version": gateway.pricebook.version,
                        "currency": gateway.pricebook.currency,
                        "server_calculated": True,
                    },
                    "security": {
                        "browser_secrets": False,
                        "credential_mode": "server_env_reference",
                        "auth_mode": principal.auth_mode,
                        "tenant_id": principal.tenant_id,
                    },
                    "control_plane": control_plane.overview(principal),
                }

            if path == "/api/v1/projects":
                if method == "GET":
                    return repository.list_projects(principal.tenant_id)
                if method == "POST":
                    principal.require("operator", "creator")
                    name, route = validate_project_payload(payload)
                    owner_user_id = principal.actor_id if principal.auth_mode == "session" else None
                    project = repository.create_project(name, route, principal.tenant_id, owner_user_id)
                    mock_connection = gateway.ensure_mock_connection(principal.tenant_id)
                    project["ai_policy"] = repository.update_policy(project["id"], {"connection_id": mock_connection["id"]})
                    return project

            match = re.fullmatch(r"/api/v1/projects/([^/]+)", path)
            if match:
                self._require_project_access(principal, match.group(1))
                if method == "GET":
                    project = repository.get_project(match.group(1), principal.tenant_id)
                    if project is None:
                        raise AppError("project_not_found", "项目不存在。", 404)
                    project["versions"] = [self._public_version(item) for item in repository.list_skill_versions(project["id"])]
                    project["validations"] = repository.list_validations(project["id"], 20)
                    project["expert_reviews"] = repository.list_expert_reviews(project["id"], 20)
                    project["update_policy"] = repository.get_update_policy(project["id"])
                    return project
                if method == "PUT":
                    principal.require("operator", "creator")
                    changes: dict[str, Any] = {}
                    if "name" in payload:
                        name = str(payload["name"]).strip()
                        if not name or len(name) > 120:
                            raise AppError("invalid_project_name", "项目名称必须为 1–120 个字符。")
                        changes["name"] = name
                    return repository.update_project(match.group(1), changes)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/steps/(\d+)", path)
            if method == "PUT" and match:
                principal.require("operator", "creator")
                route = str(payload.get("route") or "template")
                status = str(payload.get("status") or "draft")
                step_payload = payload.get("payload") or {}
                if route not in {"template", "existing"} or not isinstance(step_payload, dict):
                    raise AppError("invalid_step", "步骤路线或内容格式不正确。")
                step_index = int(match.group(2))
                maximum = 8 if route == "template" else 6
                if step_index > maximum:
                    raise AppError("invalid_step_index", "步骤编号超出当前路线范围。")
                if status not in {"draft", "complete", "blocked"}:
                    raise AppError("invalid_step_status", "步骤状态不正确。")
                validate_step_contract(repository, match.group(1), route, step_index, step_payload, status)
                expected_revision = payload.get("expected_revision")
                if expected_revision is not None:
                    try:
                        expected_revision = int(expected_revision)
                    except (TypeError, ValueError) as exc:
                        raise AppError("invalid_step_revision", "步骤修订号必须是整数。") from exc
                return repository.save_step(match.group(1), route, step_index, step_payload, status, expected_revision)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/ai-policy", path)
            if method == "PUT" and match:
                principal.require("operator", "creator")
                return repository.update_policy(
                    match.group(1),
                    validate_ai_policy_payload(payload, repository, principal.tenant_id),
                )

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/ai-runs", path)
            if match:
                if method == "POST":
                    principal.require("operator", "creator")
                    return gateway.run(match.group(1), payload)
                if method == "GET":
                    limit = self._query_limit(query, 50, 200)
                    return repository.list_ai_runs(match.group(1), limit)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/host-requests(?:/([^/]+)(/result)?)?", path)
            if match:
                project_id, request_id, result_suffix = match.groups()
                if method == "GET" and not result_suffix:
                    return host_collaboration.get(project_id, request_id) if request_id else host_collaboration.list(project_id)
                if method == "POST":
                    principal.require("operator", "creator")
                    if not request_id:
                        return host_collaboration.create(project_id, payload, principal.actor_id, principal.auth_mode)
                    if result_suffix:
                        return host_collaboration.submit(project_id, request_id, payload, principal.actor_id, principal.auth_mode)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/ai-contribution", path)
            if method == "GET" and match:
                return repository.ai_contribution_summary(match.group(1))

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/ai-orchestrations", path)
            if method == "POST" and match:
                principal.require("operator", "creator")
                return gateway.orchestrate(match.group(1), payload)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/audit", path)
            if method == "GET" and match:
                limit = self._query_limit(query, 100, 500)
                return repository.list_audit_events(match.group(1), limit)

            if path == "/api/v1/skill-sources" and method == "GET":
                return [self._public_source(item) for item in skill_engine.discover_sources(principal.tenant_id)]

            match = re.fullmatch(r"/api/v1/skill-sources/([^/]+)/snapshot", path)
            if method == "POST" and match:
                principal.require("operator", "creator")
                return self._public_source(skill_engine.snapshot_source(match.group(1), principal.tenant_id))

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/import", path)
            if method == "POST" and match:
                principal.require("operator", "creator")
                source_id = str(payload.get("source_id") or "")
                if not source_id:
                    raise AppError("skill_source_required", "请选择要导入的 Skill。")
                result = skill_engine.attach_source(match.group(1), source_id, principal.tenant_id)
                result["source"] = self._public_source(result["source"])
                result["baseline"] = self._public_version(result["baseline"])
                return result

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/candidate", path)
            if method == "POST" and match:
                principal.require("operator", "creator")
                result = skill_engine.generate_candidate(match.group(1), str(payload.get("version") or ""))
                result["version"] = self._public_version(result["version"])
                return result

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/validations", path)
            if match:
                if method == "GET":
                    return repository.list_validations(match.group(1), self._query_limit(query, 100, 200))
                if method == "POST":
                    principal.require("operator", "creator")
                    return skill_engine.validate_project(
                        match.group(1),
                        str(payload.get("stage") or "static"),
                        str(payload.get("version_id") or ""),
                    )

            if method == "GET" and path == "/api/v1/expert-framework":
                return expert_framework()

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/expert-reviews", path)
            if match:
                if method == "GET":
                    return repository.list_expert_reviews(match.group(1), self._query_limit(query, 20, 100))
                if method == "POST":
                    principal.require("operator", "creator")
                    return repository.create_expert_review(match.group(1), validate_expert_review(payload))

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/versions", path)
            if method == "GET" and match:
                return [self._public_version(item) for item in repository.list_skill_versions(match.group(1))]

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/deliveries", path)
            if method == "POST" and match:
                principal.require("operator", "creator")
                version = skill_engine.deliver(
                    match.group(1),
                    str(payload.get("version_id") or ""),
                    str(payload.get("version") or ""),
                )
                return self._public_version(version)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/versions/([^/]+)/download", path)
            if method == "GET" and match:
                version = repository.get_skill_version(match.group(2))
                if version is None or version["project_id"] != match.group(1) or not version.get("package_path"):
                    raise AppError("package_not_found", "交付包不存在。", 404)
                package = Path(version["package_path"]).resolve()
                artifact_root = (config.artifact_dir or (config.root_dir / "data" / "artifacts")).resolve()
                try:
                    package.relative_to(artifact_root)
                except ValueError as exc:
                    raise AppError("package_path_invalid", "交付包路径不安全。", 403) from exc
                if not package.is_file():
                    raise AppError("package_not_found", "交付包不存在。", 404)
                return FileDownload(package, package.name)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/update-policy", path)
            if match:
                if method == "GET":
                    return repository.get_update_policy(match.group(1))
                if method == "PUT":
                    principal.require("operator", "creator")
                    if "enabled" in payload and not isinstance(payload["enabled"], bool):
                        raise AppError("invalid_update_enabled", "更新策略 enabled 必须是布尔值。")
                    frequency = payload.get("frequency")
                    if frequency is not None and frequency not in {"daily", "weekly", "monthly", "每天", "每周", "每月"}:
                        raise AppError("invalid_update_frequency", "更新频率不正确。")
                    strategy = payload.get("strategy")
                    if strategy is not None and strategy not in {"review", "manual", "notify"}:
                        raise AppError("invalid_update_strategy", "更新策略不正确。")
                    sources = payload.get("sources")
                    if sources is not None and (not isinstance(sources, list) or any(not isinstance(item, str) for item in sources)):
                        raise AppError("invalid_update_sources", "更新来源必须是字符串数组。")
                    if sources is not None and any(item not in {"source", "policy", "manual", "upstream", "security"} for item in sources):
                        raise AppError("invalid_update_sources", "更新来源包含未知类型。")
                    allowed = {"enabled", "frequency", "sources", "strategy"}
                    changes = {key: value for key, value in payload.items() if key in allowed}
                    return repository.update_update_policy(match.group(1), changes)

            match = re.fullmatch(r"/api/v1/projects/([^/]+)/update-checks", path)
            if match:
                if method == "GET":
                    return repository.list_update_checks(match.group(1), self._query_limit(query, 50, 200))
                if method == "POST":
                    principal.require("operator", "creator")
                    return skill_engine.check_updates(match.group(1))

            if path == "/api/v1/update-jobs/run-due" and method == "POST":
                principal.require("operator")
                try:
                    limit = int(payload.get("limit", 20))
                except (TypeError, ValueError) as exc:
                    raise AppError("invalid_limit", "limit 必须是整数。") from exc
                if limit < 1 or limit > 100:
                    raise AppError("invalid_limit", "limit 必须在 1–100 之间。")
                return skill_engine.run_due_checks(limit)

            if path == "/api/v1/ai/connections":
                if method == "GET":
                    principal.require("operator")
                    return repository.list_connections(principal.tenant_id)
                if method == "POST":
                    principal.require("admin")
                    return gateway.create_connection(payload, principal.tenant_id)

            match = re.fullmatch(r"/api/v1/ai/connections/([^/]+)/test", path)
            if method == "POST" and match:
                principal.require("admin")
                return gateway.test_connection(match.group(1), principal.tenant_id)

            match = re.fullmatch(r"/api/v1/ai/connections/([^/]+)", path)
            if method == "DELETE" and match:
                principal.require("admin")
                return repository.delete_connection(match.group(1), principal.tenant_id)

            match = re.fullmatch(r"/api/v1/ai/connections/([^/]+)/models", path)
            if method == "GET" and match:
                principal.require("operator")
                connection = repository.get_connection(match.group(1), principal.tenant_id)
                if connection is None:
                    raise AppError("connection_not_found", "模型连接不存在。", 404)
                return connection.get("model_catalog", [])

            if path == "/api/v1/control/overview" and method == "GET":
                return control_plane.overview(principal)

            if path == "/api/v1/control/production-readiness" and method == "GET":
                principal.require("admin", "operator")
                return evaluate_production_readiness(
                    config,
                    email_delivery_configured=accounts.email_delivery is not None,
                    database_backend="sqlite",
                )

            if path == "/api/v1/control/repositories":
                if method == "GET":
                    return control_plane.list_repositories(principal)
                if method == "POST":
                    return control_plane.create_repository(principal, payload)

            match = re.fullmatch(r"/api/v1/control/repositories/([^/]+)/scans", path)
            if method == "POST" and match:
                return control_plane.scan_repository(principal, match.group(1), payload)

            if path == "/api/v1/control/snapshots" and method == "GET":
                return control_plane.list_snapshots(principal)

            match = re.fullmatch(r"/api/v1/control/snapshots/([^/]+)", path)
            if method == "GET" and match:
                return control_plane.get_snapshot(principal, match.group(1))

            if path == "/api/v1/control/evidence" and method == "GET":
                return control_plane.list_evidence(principal)

            if path == "/api/v1/control/agents":
                if method == "GET":
                    return control_plane.list_agents(principal)
                if method == "POST":
                    return control_plane.create_agent(principal, payload)

            if path == "/api/v1/control/edge/overview" and method == "GET":
                return edge_control.overview(principal)

            if path == "/api/v1/control/host-connections":
                if method == "GET":
                    return edge_control.list_host_connections(principal)
                if method == "POST":
                    return edge_control.create_host_connection(principal, payload)

            match = re.fullmatch(r"/api/v1/control/host-connections/([^/]+)/connectors", path)
            if method == "POST" and match:
                return edge_control.create_connector(principal, match.group(1), payload)

            if path == "/api/v1/control/connectors" and method == "GET":
                return edge_control.list_connectors(principal)

            match = re.fullmatch(r"/api/v1/control/connectors/([^/]+)/heartbeat", path)
            if method == "POST" and match:
                return edge_control.heartbeat_connector(principal, match.group(1), payload)

            if path == "/api/v1/control/bridges":
                if method == "GET":
                    return edge_control.list_bridges(principal)
                if method == "POST":
                    return edge_control.create_bridge(principal, payload)

            match = re.fullmatch(r"/api/v1/control/bridges/([^/]+)/heartbeat", path)
            if method == "POST" and match:
                return edge_control.heartbeat_bridge(principal, match.group(1), payload)

            if path == "/api/v1/control/deployments":
                if method == "GET":
                    return control_plane.list_deployments(principal)
                if method == "POST":
                    return control_plane.create_deployment(principal, payload)

            match = re.fullmatch(r"/api/v1/control/deployments/([^/]+)/observe", path)
            if method == "POST" and match:
                return control_plane.observe_deployment(principal, match.group(1), payload)

            if path == "/api/v1/control/revocations":
                if method == "GET":
                    return control_plane.list_revocations(principal)
                if method == "POST":
                    return control_plane.revoke_artifact(principal, payload)

            if path == "/api/v1/control/entitlements" and method == "POST":
                return control_plane.create_entitlement(principal, payload)

            if path == "/api/v1/control/receipts":
                if method == "GET":
                    return control_plane.list_receipts(principal)
                if method == "POST":
                    return control_plane.ingest_receipt(principal, payload)

            if path == "/api/v1/control/statements":
                if method == "GET":
                    return control_plane.list_statements(principal)
                if method == "POST":
                    return control_plane.create_statement(principal, payload)

            match = re.fullmatch(r"/api/v1/control/statements/([^/]+)/payouts", path)
            if method == "POST" and match:
                return control_plane.submit_payout(principal, match.group(1), self.headers.get("Idempotency-Key", ""))

            if path == "/api/v1/control/payouts" and method == "GET":
                return control_plane.list_payouts(principal)

            if path == "/api/v1/control/ledger" and method == "GET":
                return control_plane.ledger(principal)

            if path == "/api/v1/control/audit" and method == "GET":
                return control_plane.list_audit(principal, self._query_limit(query, 200, 500))

            if path == "/api/v1/control/demo" and method == "POST":
                return control_plane.run_demo_path(principal)

            raise AppError("route_not_found", "API 路径不存在。", 404)

        def _require_project_access(self, principal: Principal, project_id: str) -> None:
            if not repository.project_belongs_to(project_id, principal.tenant_id):
                raise AppError("project_not_found", "项目不存在或当前租户无权访问。", 404)

        def _check_rate_limit(self, principal: Principal, limit: int | None = None, scope: str = "api") -> None:
            now = time.monotonic()
            key = f"{scope}:{principal.tenant_id}:{principal.actor_id}:{self.client_address[0]}"
            effective_limit = limit or config.rate_limit_per_minute
            with rate_lock:
                started, count = rate_windows.get(key, (now, 0))
                if now - started >= 60:
                    started, count = now, 0
                count += 1
                rate_windows[key] = (started, count)
                if len(rate_windows) > 10_000:
                    stale = [item for item, value in rate_windows.items() if now - value[0] > 120]
                    for item in stale:
                        rate_windows.pop(item, None)
            if count > effective_limit:
                raise AppError("rate_limited", "请求过于频繁，请稍后重试。", 429, {"retry_after_seconds": max(1, int(60 - (now - started)))})

        def _check_origin(self, method: str) -> None:
            if method not in {"POST", "PUT", "DELETE"}:
                return
            origin = self.headers.get("Origin", "").rstrip("/")
            if not origin:
                return
            expected = f"http://{self.headers.get('Host', '')}".rstrip("/")
            allowed = set(config.trusted_origins)
            allowed.add(expected)
            forwarded_proto = self.headers.get("X-Forwarded-Proto", "")
            if forwarded_proto == "https":
                allowed.add(f"https://{self.headers.get('Host', '')}".rstrip("/"))
            if origin not in allowed:
                raise AppError("origin_not_allowed", "请求来源不在服务端允许列表中。", 403)

        def _read_json(self) -> dict[str, Any]:
            length_header = self.headers.get("Content-Length", "0")
            try:
                length = int(length_header)
            except ValueError as exc:
                raise AppError("invalid_content_length", "Content-Length 不正确。") from exc
            if length < 0:
                raise AppError("invalid_content_length", "Content-Length 不能为负数。")
            if length > config.request_body_limit:
                raise AppError("request_too_large", "请求内容超过 1 MB 限制。", 413)
            if length == 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AppError("invalid_json", "请求内容必须是 UTF-8 JSON。") from exc
            if not isinstance(payload, dict):
                raise AppError("invalid_json_object", "请求 JSON 顶层必须是对象。")
            return payload

        def _cookie(self, name: str) -> str:
            raw = self.headers.get("Cookie", "")
            if not raw:
                return ""
            try:
                cookie = SimpleCookie()
                cookie.load(raw)
            except Exception:
                return ""
            morsel = cookie.get(name)
            return morsel.value if morsel else ""

        def _account_response(self, session: AccountSession) -> SessionResponse:
            return SessionResponse(
                {"authenticated": True, "user": session.user, "expires_at": session.expires_at, "csrf_token": session.csrf_token},
                self._account_cookies(session),
            )

        def _account_cookies(self, session: AccountSession) -> tuple[str, ...]:
            secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            max_age = max(60, int((datetime.fromisoformat(session.expires_at) - datetime.now(timezone.utc)).total_seconds()))
            suffix = "; Path=/; SameSite=Strict" + ("; Secure" if secure else "")
            cookies = (
                f"skillsentra_session={session.session_token}; Max-Age={max_age}; HttpOnly{suffix}",
                f"skillsentra_csrf={session.csrf_token}; Max-Age={max_age}{suffix}",
            )
            return cookies

        def _oauth_state_cookie(self, state: str) -> str:
            secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            return f"skillsentra_oauth_state={state}; Max-Age=600; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")

        def _oauth_verifier_cookie(self, verifier: str) -> str:
            secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            return f"skillsentra_oauth_verifier={verifier}; Max-Age=600; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")

        def _clear_oauth_state_cookie(self) -> tuple[str, ...]:
            secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            suffix = "; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")
            return (f"skillsentra_oauth_state={suffix}", f"skillsentra_oauth_verifier={suffix}")

        def _clear_account_cookies(self) -> tuple[str, ...]:
            secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            suffix = "; Path=/; Max-Age=0; SameSite=Strict" + ("; Secure" if secure else "")
            return (f"skillsentra_session={suffix}; HttpOnly", f"skillsentra_csrf={suffix}")

        def _send_json(self, status: int, payload: dict[str, Any], cookies: tuple[str, ...] = ()) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers = [("Set-Cookie", cookie) for cookie in cookies]
            self._send_bytes(status, body, "application/json; charset=utf-8", headers)

        def _send_download(self, download: FileDownload) -> None:
            body = download.path.read_bytes()
            safe_name = re.sub(r"[^0-9A-Za-z._-]", "-", download.filename)
            self._send_bytes(
                HTTPStatus.OK,
                body,
                "application/zip",
                [("Content-Disposition", f'attachment; filename="{safe_name}"')],
            )

        def _send_redirect(self, location: str, cookies: tuple[str, ...] = ()) -> None:
            self._send_bytes(HTTPStatus.FOUND, b"", "text/plain; charset=utf-8", [("Location", location), *[("Set-Cookie", cookie) for cookie in cookies]])

        def _send_bytes(
            self,
            status: int,
            body: bytes,
            content_type: str,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for name, value in extra_headers or ():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
            except ConnectionError:
                # Browsers routinely abandon in-flight responses during navigation or teardown.
                # This is a transport outcome, not an application failure, and sending a second
                # 500 response would only trigger another write to the closed socket.
                self.close_connection = True
                LOGGER.debug(
                    "client_disconnected method=%s",
                    RequestMetrics.normalize_method(str(getattr(self, "command", ""))),
                )

        @staticmethod
        def _query_limit(query: dict[str, list[str]], default: int, maximum: int) -> int:
            raw = (query.get("limit") or [str(default)])[0]
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise AppError("invalid_limit", "limit 必须是整数。") from exc
            if value < 1 or value > maximum:
                raise AppError("invalid_limit", f"limit 必须在 1–{maximum} 之间。")
            return value

        @staticmethod
        def _payload_limit(payload: dict[str, Any], default: int, maximum: int) -> int:
            try:
                value = int(payload.get("limit", default))
            except (TypeError, ValueError) as exc:
                raise AppError("invalid_limit", "limit 必须是整数。") from exc
            if value < 1 or value > maximum:
                raise AppError("invalid_limit", f"limit 必须在 1–{maximum} 之间。")
            return value

        @staticmethod
        def _public_version(record: dict[str, Any]) -> dict[str, Any]:
            public = dict(record)
            artifact_path = str(public.pop("artifact_path", ""))
            package_path = str(public.pop("package_path", ""))
            public["artifact_name"] = Path(artifact_path).name if artifact_path else ""
            public["package_name"] = Path(package_path).name if package_path else ""
            public["download_url"] = (
                f"/api/v1/projects/{public['project_id']}/versions/{public['id']}/download"
                if package_path else ""
            )
            return public

        @staticmethod
        def _public_source(record: dict[str, Any]) -> dict[str, Any]:
            public = dict(record)
            public.pop("root_path", None)
            return public

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            LOGGER.info(
                "http_request method=%s status=%s size_bytes=%s",
                RequestMetrics.normalize_method(str(getattr(self, "command", ""))),
                code,
                size,
            )

        def log_error(self, format: str, *args: Any) -> None:
            status: int | str = "-"
            if format.startswith("code %d") and args and isinstance(args[0], int):
                status = args[0]
            LOGGER.warning("http_protocol_error status=%s", status)

        def log_message(self, format: str, *args: Any) -> None:
            # BaseHTTPRequestHandler messages can contain the raw request line.
            # Never retain client-controlled paths, queries, headers, or addresses.
            LOGGER.warning("http_server_message")

    return SkillSentraHandler


def create_server(config: AppConfig | None = None) -> BoundedThreadingHTTPServer:
    config = config or AppConfig.from_env()
    database = Database(config.database_path)
    database.migrate()
    repository = Repository(database)
    gateway = AIGateway(repository)
    artifact_dir = config.artifact_dir or (config.root_dir / "data" / "artifacts")
    skill_engine = SkillEngine(repository, artifact_dir, config.allowed_skill_roots)
    control_plane = ControlPlane(database, repository, skill_engine)
    edge_control = EdgeControl(database)
    authenticator = TokenAuthenticator(config.auth_mode, config.auth_tokens_json)
    accounts = AccountService(
        database, config.session_days, config.google_client_id, config.google_client_secret,
        config.google_redirect_uri, config.oauth_state_minutes,
        require_email_verification=config.require_email_verification,
        public_origin=config.public_origin,
        email_delivery=(
            SMTPEmailDelivery(
                config.smtp_host,
                config.smtp_port,
                config.smtp_username,
                config.smtp_password,
                config.smtp_from_email,
                starttls=config.smtp_starttls,
            )
            if config.smtp_host and config.smtp_from_email
            else None
        ),
        chatgpt_enabled=config.chatgpt_enabled,
        chatgpt_client_id=config.chatgpt_client_id,
        chatgpt_client_secret=config.chatgpt_client_secret,
        chatgpt_authorization_url=config.chatgpt_authorization_url,
        chatgpt_token_url=config.chatgpt_token_url,
        chatgpt_userinfo_url=config.chatgpt_userinfo_url,
        chatgpt_redirect_uri=config.chatgpt_redirect_uri,
        chatgpt_scope=config.chatgpt_scope,
    )
    marketplace = MarketplaceService(database, repository, control_plane)
    # Top-100 GitHub discovery is fetched as bounded 50-result pages so each
    # response stays below the existing 1 MB hard ceiling.
    discovery = SkillDiscoveryService(max_response_bytes=1024 * 1024)
    github_candidates = GitHubSkillCandidateCatalog(database, discovery)
    if config.demo_seed_enabled:
        marketplace.seed_curated_catalog(
            [
                {
                    "root": root,
                    "scan": skill_engine.scan_path(root),
                }
                for root in sorted((config.root_dir / "sample-skills").iterdir())
                if root.is_dir() and (root / "SKILL.md").is_file()
            ]
        )
    automation = ScanAutomationService(database, control_plane)
    handler = make_handler(
        config,
        repository,
        gateway,
        skill_engine,
        control_plane,
        authenticator,
        accounts,
        marketplace,
        automation,
        discovery,
        github_candidates,
        edge_control,
    )
    server = BoundedThreadingHTTPServer((config.host, config.port), handler, config.max_workers)
    if config.automation_enabled:
        server.add_background_service(ScanScheduler(automation, config.automation_poll_seconds))
    return server


def main() -> None:
    defaults = AppConfig.from_env()
    parser = argparse.ArgumentParser(description="Run SkillSentra Studio locally")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", default=defaults.port, type=int)
    parser.add_argument("--database", type=Path, default=defaults.database_path)
    args = parser.parse_args()
    config = AppConfig(
        root_dir=defaults.root_dir,
        static_dir=defaults.static_dir,
        database_path=args.database.resolve(),
        host=args.host,
        port=args.port,
        request_body_limit=defaults.request_body_limit,
        artifact_dir=defaults.artifact_dir,
        allowed_skill_roots=defaults.allowed_skill_roots,
        auth_mode=defaults.auth_mode,
        auth_tokens_json=defaults.auth_tokens_json,
        trusted_origins=defaults.trusted_origins,
        rate_limit_per_minute=defaults.rate_limit_per_minute,
        max_workers=defaults.max_workers,
        automation_enabled=defaults.automation_enabled,
        automation_poll_seconds=defaults.automation_poll_seconds,
        session_days=defaults.session_days,
        demo_seed_enabled=defaults.demo_seed_enabled,
        google_client_id=defaults.google_client_id,
        google_client_secret=defaults.google_client_secret,
        google_redirect_uri=defaults.google_redirect_uri,
        oauth_state_minutes=defaults.oauth_state_minutes,
        environment=defaults.environment,
        public_origin=defaults.public_origin,
        require_email_verification=defaults.require_email_verification,
        smtp_host=defaults.smtp_host,
        smtp_port=defaults.smtp_port,
        smtp_username=defaults.smtp_username,
        smtp_password=defaults.smtp_password,
        smtp_from_email=defaults.smtp_from_email,
        smtp_starttls=defaults.smtp_starttls,
        chatgpt_enabled=defaults.chatgpt_enabled,
        chatgpt_client_id=defaults.chatgpt_client_id,
        chatgpt_client_secret=defaults.chatgpt_client_secret,
        chatgpt_authorization_url=defaults.chatgpt_authorization_url,
        chatgpt_token_url=defaults.chatgpt_token_url,
        chatgpt_userinfo_url=defaults.chatgpt_userinfo_url,
        chatgpt_redirect_uri=defaults.chatgpt_redirect_uri,
        chatgpt_scope=defaults.chatgpt_scope,
        tls_termination_confirmed=defaults.tls_termination_confirmed,
        backup_verified=defaults.backup_verified,
        observability_endpoint=defaults.observability_endpoint,
    )
    logging.basicConfig(level=os.environ.get("SKILLSENTRA_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = create_server(config)
    print(f"SkillSentra Studio running at http://{config.host}:{server.server_address[1]}/")
    print(f"Database: {config.database_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
