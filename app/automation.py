from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

from .control_plane import GITHUB_REPOSITORY_RE, ControlPlane, Principal
from .database import Database, json_dumps, utc_now
from .errors import AppError
from .repository import make_id


LOGGER = logging.getLogger("skillsentra.automation")


@dataclass(frozen=True)
class _PinnedAddress:
    family: int
    socktype: int
    proto: int
    sockaddr: tuple[Any, ...]


@dataclass(frozen=True)
class _WebsiteTarget:
    url: str
    hostname: str
    request_target: str
    addresses: tuple[_PinnedAddress, ...]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to one vetted address while retaining hostname TLS checks."""

    def __init__(
        self,
        hostname: str,
        address: _PinnedAddress,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ):
        super().__init__(hostname, port=443, timeout=timeout, context=context)
        self._pinned_address = address

    def connect(self) -> None:
        address = self._pinned_address
        raw_socket = socket.socket(address.family, address.socktype, address.proto)
        self.sock = raw_socket
        try:
            raw_socket.settimeout(self.timeout)
            raw_socket.connect(address.sockaddr)
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            self.close()
            raise


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", "link"}:
            return
        for key, value in attrs:
            if key == "href" and value:
                self.links.append(value)


class WebsiteSkillDiscovery:
    def __init__(
        self,
        timeout: float = 15,
        response_limit: int = 2_000_000,
        *,
        resolver: Callable[..., list[Any]] | None = None,
        connection_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= 30:
            raise ValueError("website discovery timeout must be greater than 0 and at most 30 seconds")
        if isinstance(response_limit, bool) or not isinstance(response_limit, int) or not 1 <= response_limit <= 2_000_000:
            raise ValueError("website discovery response limit must be from 1 to 2000000 bytes")
        self.timeout = float(timeout)
        self.response_limit = response_limit
        self._resolver = resolver or socket.getaddrinfo
        self._connection_factory = connection_factory or _PinnedHTTPSConnection
        self._clock = clock or time.monotonic

    def discover(self, url: str) -> list[dict[str, str]]:
        normalized = self.validate_url(url)
        deadline = self._clock() + self.timeout
        target = self._resolve_target(normalized, deadline)
        raw, content_type = self._fetch(target, deadline)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError("website_encoding_unsupported", "指定网站必须提供 UTF-8 内容。", 409) from exc
        candidates: list[dict[str, str]] = []
        if content_type == "application/json" or normalized.endswith(".json"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise AppError("website_manifest_invalid", "Skill 目录 JSON 格式不正确。", 409) from exc
            entries = payload.get("skills") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                raise AppError("website_manifest_invalid", "Skill 目录 JSON 必须包含 skills 数组。", 409)
            for item in entries[:50]:
                if isinstance(item, dict):
                    repo = self._github_repository(str(item.get("repository") or ""))
                    if repo:
                        candidates.append({"url": repo, "ref": str(item.get("ref") or "main")[:200]})
        else:
            parser = _LinkCollector()
            parser.feed(text)
            for link in parser.links[:500]:
                resolved = urllib.parse.urljoin(normalized, link)
                repo = self._github_repository(resolved)
                if repo:
                    candidates.append({"url": repo, "ref": "main"})
        unique: dict[str, dict[str, str]] = {}
        for item in candidates:
            unique.setdefault(item["url"].casefold(), item)
        return list(unique.values())[:10]

    @staticmethod
    def validate_url(value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate or len(candidate) > 2048 or any(ord(character) < 32 or ord(character) == 127 for character in candidate):
            raise AppError("invalid_website_url", "指定网站必须是无凭据、无片段的 HTTPS 地址。")
        try:
            parsed = urllib.parse.urlsplit(candidate)
            port = parsed.port
        except ValueError:
            raise AppError("invalid_website_url", "指定网站必须是无凭据、无片段的 HTTPS 地址。") from None
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise AppError("invalid_website_url", "指定网站必须是无凭据、无片段的 HTTPS 地址。")
        if port not in {None, 443}:
            raise AppError("invalid_website_port", "指定网站只允许 HTTPS 标准端口。")
        return parsed.geturl()

    def _assert_public_destination(self, url: str) -> None:
        normalized = self.validate_url(url)
        self._resolve_target(normalized, self._clock() + self.timeout)

    def _resolve_target(self, url: str, deadline: float) -> _WebsiteTarget:
        parsed = urllib.parse.urlsplit(url)
        hostname = str(parsed.hostname or "").rstrip(".").lower()
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            raise AppError("invalid_website_url", "指定网站域名格式不正确。") from None
        request_target = parsed.path or "/"
        if parsed.query:
            request_target = f"{request_target}?{parsed.query}"
        try:
            request_target.encode("ascii")
        except UnicodeEncodeError:
            raise AppError("invalid_website_url", "指定网站路径必须使用 URL 编码。") from None

        try:
            literal = ipaddress.ip_address(hostname)
            family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
            if not literal.is_global:
                raise AppError("website_private_address", "指定网站解析到了不允许的内部地址。", 403)
            sockaddr: tuple[Any, ...] = (str(literal), 443, 0, 0) if family == socket.AF_INET6 else (str(literal), 443)
            return _WebsiteTarget(
                url,
                hostname,
                request_target,
                (_PinnedAddress(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, sockaddr),),
            )
        except ValueError:
            pass

        resolved = self._resolve_once(hostname, deadline)
        addresses: list[_PinnedAddress] = []
        seen: set[tuple[int, tuple[Any, ...]]] = set()
        for record in resolved:
            try:
                family, socktype, proto, _canonical_name, raw_sockaddr = record
                address = ipaddress.ip_address(str(raw_sockaddr[0]).split("%", 1)[0])
            except (IndexError, TypeError, ValueError):
                raise AppError("website_private_address", "指定网站解析结果不安全。", 403) from None
            if (
                family not in {socket.AF_INET, socket.AF_INET6}
                or socktype not in {0, socket.SOCK_STREAM}
                or address.version != (6 if family == socket.AF_INET6 else 4)
                or not address.is_global
            ):
                raise AppError("website_private_address", "指定网站解析到了不允许的内部地址。", 403)
            if family == socket.AF_INET6:
                flowinfo = int(raw_sockaddr[2]) if len(raw_sockaddr) > 2 else 0
                scope_id = int(raw_sockaddr[3]) if len(raw_sockaddr) > 3 else 0
                if scope_id != 0:
                    raise AppError("website_private_address", "指定网站解析结果不安全。", 403)
                sockaddr = (str(address), 443, flowinfo, 0)
            else:
                sockaddr = (str(address), 443)
            identity = (family, sockaddr)
            if identity in seen:
                continue
            seen.add(identity)
            if len(addresses) < 8:
                addresses.append(
                    _PinnedAddress(family, socket.SOCK_STREAM, int(proto) or socket.IPPROTO_TCP, sockaddr)
                )
        if not addresses:
            raise AppError("website_dns_failed", "指定网站没有可用的公网解析结果。", 502)
        self._remaining(deadline)
        return _WebsiteTarget(url, hostname, request_target, tuple(addresses))

    def _resolve_once(self, hostname: str, deadline: float) -> list[Any]:
        outcome: list[list[Any]] = []
        failures: list[Exception] = []
        completed = threading.Event()

        def resolve() -> None:
            try:
                outcome.append(list(self._resolver(hostname, 443, 0, socket.SOCK_STREAM)))
            except Exception as exc:
                failures.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(target=resolve, name="skillsentra-website-dns", daemon=True)
        worker.start()
        if not completed.wait(self._remaining(deadline)):
            raise self._deadline_error()
        self._remaining(deadline)
        if failures or not outcome:
            raise AppError("website_dns_failed", "无法安全解析指定网站。", 502) from None
        return outcome[0]

    def _fetch(self, target: _WebsiteTarget, deadline: float) -> tuple[bytes, str]:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        if hasattr(ssl, "TLSVersion"):
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._remaining(deadline)

        last_failure: Exception | None = None
        for address in target.addresses:
            connection = self._connection_factory(
                target.hostname,
                address,
                timeout=self._remaining(deadline),
                context=context,
            )
            watchdog = threading.Timer(self._remaining(deadline), connection.close)
            watchdog.daemon = True
            watchdog.start()
            response: Any | None = None
            try:
                connection.request(
                    "GET",
                    target.request_target,
                    headers={
                        "Accept": "text/html,application/json;q=0.9",
                        "Accept-Encoding": "identity",
                        "User-Agent": "SkillSentra-Discovery/0.6",
                        "Connection": "close",
                    },
                )
                self._remaining(deadline)
                response = connection.getresponse()
                self._remaining(deadline)
                raw, content_type = self._read_bounded_response(response, connection, deadline)
                return raw, content_type
            except AppError:
                raise
            except (TimeoutError, socket.timeout):
                last_failure = self._deadline_error()
            except (OSError, ssl.SSLError, http.client.HTTPException):
                last_failure = AppError("website_scan_failed", "指定网站当前不可达。", 502)
            finally:
                watchdog.cancel()
                if response is not None and hasattr(response, "close"):
                    response.close()
                connection.close()
        if self._clock() >= deadline:
            raise self._deadline_error()
        if last_failure is not None:
            raise last_failure from None
        raise AppError("website_scan_failed", "指定网站当前不可达。", 502)

    def _read_bounded_response(self, response: Any, connection: Any, deadline: float) -> tuple[bytes, str]:
        status = int(getattr(response, "status", 0) or 0)
        headers = getattr(response, "headers", {})
        if 300 <= status < 400:
            raise AppError("website_redirect_blocked", "指定网站返回了不允许的重定向。", 502)
        if not 200 <= status < 300:
            raise AppError("website_scan_failed", "指定网站拒绝了发现请求。", 502, {"provider_status": status})
        encoding = str(headers.get("Content-Encoding") or "identity").strip().lower()
        if encoding not in {"", "identity"}:
            raise AppError("website_encoding_unsupported", "指定网站必须返回未压缩内容。", 409)
        declared_size = headers.get("Content-Length")
        if declared_size is not None:
            try:
                if int(declared_size) > self.response_limit:
                    raise AppError("website_response_too_large", "指定网站响应超过 2 MB 限制。", 413)
            except (TypeError, ValueError):
                pass

        raw = bytearray()
        reader = getattr(response, "read1", None) or response.read
        while len(raw) <= self.response_limit:
            remaining = self._remaining(deadline)
            peer = getattr(connection, "sock", None)
            if peer is not None and hasattr(peer, "settimeout"):
                peer.settimeout(remaining)
            chunk = reader(min(65_536, self.response_limit + 1 - len(raw)))
            self._remaining(deadline)
            if not isinstance(chunk, (bytes, bytearray)):
                raise AppError("website_scan_failed", "指定网站返回了无效响应。", 502)
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > self.response_limit:
            raise AppError("website_response_too_large", "指定网站响应超过 2 MB 限制。", 413)
        content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        return bytes(raw), content_type

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise self._deadline_error()
        return remaining

    @staticmethod
    def _deadline_error() -> AppError:
        return AppError("website_scan_timeout", "指定网站发现请求超过总时间限制。", 504)

    @staticmethod
    def _github_repository(value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
            return ""
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return ""
        candidate = f"https://github.com/{parts[0]}/{parts[1].removesuffix('.git')}"
        return candidate if GITHUB_REPOSITORY_RE.fullmatch(candidate) else ""


class ScanAutomationService:
    def __init__(self, database: Database, control_plane: ControlPlane):
        self.database = database
        self.control_plane = control_plane
        self.discovery = WebsiteSkillDiscovery()

    def create_target(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("admin", "operator")
        self.control_plane.ensure_tenant(principal)
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 100:
            raise AppError("invalid_scan_target_name", "扫描目标名称必须为 1–100 个字符。")
        target_type = str(payload.get("target_type") or "github")
        url = str(payload.get("url") or "").strip()
        if target_type == "github":
            if not GITHUB_REPOSITORY_RE.fullmatch(url):
                raise AppError("invalid_github_url", "GitHub 目标必须是仓库根地址。")
        elif target_type == "website":
            url = self.discovery.validate_url(url)
        else:
            raise AppError("invalid_scan_target_type", "扫描目标只能是 GitHub 或指定网站。")
        try:
            interval = int(payload.get("interval_minutes", 1440))
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_scan_interval", "扫描周期必须是分钟整数。") from exc
        if interval < 15 or interval > 43_200:
            raise AppError("invalid_scan_interval", "自动扫描周期必须为 15–43,200 分钟。")
        now = utc_now()
        target_id = make_id("tgt")
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO scan_targets(
                        id, tenant_id, name, target_type, url, requested_ref, interval_minutes,
                        auto_enabled, status, next_run_at, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_id, principal.tenant_id, name, target_type, url,
                        str(payload.get("requested_ref") or "main")[:200], interval,
                        int(payload.get("auto_enabled", True) is True),
                        "ready" if payload.get("auto_enabled", True) is True else "paused",
                        now, principal.actor_id, now, now,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise AppError("scan_target_exists", "该地址已经在自动扫描列表中。", 409) from exc
                raise
            self.control_plane._audit(connection, principal, "scan.target.created", "scan_target", target_id, {"type": target_type, "url": url})
            row = connection.execute("SELECT * FROM scan_targets WHERE id=?", (target_id,)).fetchone()
        return self._decode_target(dict(row))

    def list_targets(self, principal: Principal) -> list[dict[str, Any]]:
        principal.require("admin", "operator")
        with self.database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM scan_targets WHERE tenant_id=? ORDER BY created_at DESC", (principal.tenant_id,)
            ).fetchall()
        return [self._decode_target(dict(row)) for row in rows]

    def update_target(self, principal: Principal, target_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("admin", "operator")
        target = self._target(target_id, principal.tenant_id)
        changes: dict[str, Any] = {}
        if "auto_enabled" in payload:
            if not isinstance(payload["auto_enabled"], bool):
                raise AppError("invalid_auto_enabled", "auto_enabled 必须是布尔值。")
            changes["auto_enabled"] = int(payload["auto_enabled"])
            changes["status"] = "ready" if payload["auto_enabled"] else "paused"
        if "interval_minutes" in payload:
            try:
                interval = int(payload["interval_minutes"])
            except (TypeError, ValueError) as exc:
                raise AppError("invalid_scan_interval", "扫描周期必须是分钟整数。") from exc
            if interval < 15 or interval > 43_200:
                raise AppError("invalid_scan_interval", "自动扫描周期必须为 15–43,200 分钟。")
            changes["interval_minutes"] = interval
            changes["next_run_at"] = self._next_run(interval)
        if not changes:
            return target
        changes["updated_at"] = utc_now()
        columns = list(changes)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE scan_targets SET {', '.join(f'{key}=?' for key in columns)} WHERE id=? AND tenant_id=?",
                [changes[key] for key in columns] + [target_id, principal.tenant_id],
            )
            self.control_plane._audit(connection, principal, "scan.target.updated", "scan_target", target_id, {"fields": columns})
            row = connection.execute("SELECT * FROM scan_targets WHERE id=?", (target_id,)).fetchone()
        return self._decode_target(dict(row))

    def run_target(self, principal: Principal, target_id: str) -> dict[str, Any]:
        principal.require("admin", "operator")
        target = self._target(target_id, principal.tenant_id)
        run_id = make_id("scanrun")
        now = utc_now()
        with self.database.transaction() as connection:
            claimed = connection.execute(
                "UPDATE scan_targets SET status='running', updated_at=? WHERE id=? AND tenant_id=? AND status!='running'",
                (now, target_id, principal.tenant_id),
            )
            if claimed.rowcount != 1:
                raise AppError("scan_target_running", "该扫描目标正在运行，请稍后重试。", 409)
            connection.execute(
                "INSERT INTO scan_runs(id, tenant_id, target_id, status, started_at) VALUES (?, ?, ?, 'running', ?)",
                (run_id, principal.tenant_id, target_id, now),
            )
        candidates = [{"url": target["url"], "ref": target["requested_ref"]}]
        errors: list[dict[str, str]] = []
        results: list[dict[str, Any]] = []
        try:
            if target["target_type"] == "website":
                candidates = self.discovery.discover(target["url"])
            for candidate in candidates:
                try:
                    repository = self.control_plane.create_repository(principal, {"url": candidate["url"]})
                    snapshot = self.control_plane.scan_repository(principal, repository["id"], {"ref": candidate.get("ref") or "main"})
                    results.append({"repository_id": repository["id"], "snapshot_id": snapshot["id"], "status": snapshot["status"], "digest": snapshot["artifact_digest"]})
                except AppError as exc:
                    errors.append({"url": candidate["url"], "code": exc.code, "message": exc.message})
            status = "succeeded" if not errors else ("partial" if results else "failed")
            error_code = errors[0]["code"] if errors else ""
            error_message = errors[0]["message"] if errors else ""
        except AppError as exc:
            status, error_code, error_message = "failed", exc.code, exc.message
            errors.append({"url": target["url"], "code": exc.code, "message": exc.message})
        except Exception:
            LOGGER.exception("scan_target_failed target_id=%s run_id=%s", target_id, run_id)
            status, error_code, error_message = "failed", "scan_internal_error", "扫描过程发生内部错误。"
            errors.append({"url": target["url"], "code": error_code, "message": error_message})
        finished = utc_now()
        next_run = self._next_run(int(target["interval_minutes"]))
        blocked = sum(1 for item in results if item["status"] == "blocked")
        target_status = (
            ("ready" if target["auto_enabled"] else "paused") if status == "succeeded"
            else ("warning" if status == "partial" else "error")
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE scan_runs SET status=?, discovered_count=?, scanned_count=?, blocked_count=?, result_json=?,
                    error_code=?, error_message=?, finished_at=? WHERE id=?
                """,
                (status, len(candidates), len(results), blocked, json_dumps({"results": results, "errors": errors}), error_code, error_message, finished, run_id),
            )
            connection.execute(
                "UPDATE scan_targets SET status=?, last_run_at=?, next_run_at=?, last_error=?, updated_at=? WHERE id=?",
                (target_status, finished, next_run, error_message, finished, target_id),
            )
            self.control_plane._audit(connection, principal, "scan.target.completed", "scan_run", run_id, {"status": status, "scanned": len(results), "blocked": blocked})
            row = connection.execute("SELECT * FROM scan_runs WHERE id=?", (run_id,)).fetchone()
        return self._decode_run(dict(row))

    def list_runs(self, principal: Principal, limit: int = 100) -> list[dict[str, Any]]:
        principal.require("admin", "operator")
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT r.*, t.name AS target_name, t.target_type, t.url FROM scan_runs r
                JOIN scan_targets t ON t.id=r.target_id WHERE r.tenant_id=? ORDER BY r.started_at DESC LIMIT ?
                """,
                (principal.tenant_id, max(1, min(limit, 300))),
            ).fetchall()
        return [self._decode_run(dict(row)) for row in rows]

    def run_due(self, limit: int = 10, tenant_id: str | None = None) -> list[dict[str, Any]]:
        self._recover_stale_runs(tenant_id)
        tenant_clause = " AND tenant_id=?" if tenant_id else ""
        values: list[Any] = [utc_now()]
        if tenant_id:
            values.append(tenant_id)
        values.append(max(1, min(limit, 50)))
        with self.database.session() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM scan_targets WHERE auto_enabled=1 AND status!='running' AND next_run_at<=?
                {tenant_clause} ORDER BY next_run_at LIMIT ?
                """,
                values,
            ).fetchall()
        output = []
        for row in rows:
            principal = Principal(str(row["tenant_id"]), "scan-scheduler", frozenset({"admin", "operator"}), "service")
            try:
                output.append(self.run_target(principal, str(row["id"])))
            except AppError as exc:
                if exc.code != "scan_target_running":
                    raise
        return output

    def _recover_stale_runs(self, tenant_id: str | None) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="milliseconds")
        tenant_clause = " AND tenant_id=?" if tenant_id else ""
        values: list[Any] = [cutoff]
        if tenant_id:
            values.append(tenant_id)
        with self.database.transaction() as connection:
            stale = connection.execute(
                f"SELECT id, target_id FROM scan_runs WHERE status='running' AND started_at<?{tenant_clause}", values
            ).fetchall()
            if not stale:
                return
            message = "扫描进程中断，已自动重置。"
            connection.executemany(
                "UPDATE scan_runs SET status='failed', error_code='scan_interrupted', error_message=?, finished_at=? WHERE id=?",
                [(message, utc_now(), row["id"]) for row in stale],
            )
            connection.executemany(
                "UPDATE scan_targets SET status='warning', last_error=?, next_run_at=?, updated_at=? WHERE id=? AND status='running'",
                [(message, utc_now(), utc_now(), row["target_id"]) for row in stale],
            )
            LOGGER.warning("recovered_stale_scan_runs count=%s tenant=%s", len(stale), tenant_id or "all")

    def _target(self, target_id: str, tenant_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM scan_targets WHERE id=? AND tenant_id=?", (target_id, tenant_id)).fetchone()
        if row is None:
            raise AppError("scan_target_not_found", "扫描目标不存在。", 404)
        return self._decode_target(dict(row))

    @staticmethod
    def _next_run(interval_minutes: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)).isoformat(timespec="milliseconds")

    @staticmethod
    def _decode_target(record: dict[str, Any]) -> dict[str, Any]:
        record["auto_enabled"] = bool(record["auto_enabled"])
        return record

    @staticmethod
    def _decode_run(record: dict[str, Any]) -> dict[str, Any]:
        record["result"] = json.loads(record.pop("result_json") or "{}")
        return record


class ScanScheduler:
    def __init__(self, service: ScanAutomationService, poll_seconds: int = 60):
        self.service = service
        self.poll_seconds = max(10, min(poll_seconds, 3600))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="skillsentra-scan-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop_event.wait(self.poll_seconds):
            try:
                self.service.run_due(10)
            except Exception:
                LOGGER.exception("scan_scheduler_poll_failed")
