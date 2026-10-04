from __future__ import annotations

import http.client
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import AppError
from .database import Database, utc_now


GITHUB_SEARCH_ENDPOINT = "https://api.github.com/search/repositories"
GITHUB_TOKEN_ENV = "SKILLSENTRA_GITHUB_TOKEN"
GITHUB_TIMEOUT_ENV = "SKILLSENTRA_GITHUB_TIMEOUT_SECONDS"
GITHUB_MAX_ATTEMPTS_ENV = "SKILLSENTRA_GITHUB_MAX_ATTEMPTS"
GITHUB_RETRY_BASE_SECONDS_ENV = "SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS"
GITHUB_TOTAL_BUDGET_ENV = "SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 10.0
DEFAULT_GITHUB_MAX_ATTEMPTS = 3
MAX_GITHUB_MAX_ATTEMPTS = 4
DEFAULT_GITHUB_RETRY_BASE_SECONDS = 0.25
MAX_GITHUB_RETRY_BASE_SECONDS = 2.0
MAX_GITHUB_TOTAL_RETRY_DELAY_SECONDS = 6.0
DEFAULT_GITHUB_TOTAL_BUDGET_SECONDS = 10.0
MAX_GITHUB_TOTAL_BUDGET_SECONDS = 11.0
DEFAULT_MAX_RESPONSE_BYTES = 512 * 1024
HARD_MAX_RESPONSE_BYTES = 1024 * 1024
USER_AGENT = "SkillSentra/0.6 external-skill-discovery"

_GITHUB_FULL_NAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$"
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_DNS_NAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)


class DiscoveryError(AppError):
    """A bounded, user-safe discovery failure."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class _PinnedAddress:
    family: int
    socktype: int
    proto: int
    sockaddr: tuple[Any, ...]


@dataclass(frozen=True)
class _CatalogTarget:
    url: str
    hostname: str
    request_target: str
    addresses: tuple[_PinnedAddress, ...]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP peer is a previously validated address.

    ``host`` remains the original DNS name, so HTTP Host, TLS SNI, and certificate
    hostname verification retain their normal meaning. Only the TCP destination is
    replaced; no DNS lookup or environment proxy is consulted here.
    """

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
        try:
            raw_socket.settimeout(self.timeout)
            raw_socket.connect(address.sockaddr)
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


class SkillDiscoveryService:
    """Discover unverified external Skill candidates without downloading repositories.

    The injected ``opener`` and ``resolver`` make every network boundary independently
    testable. The default opener rejects redirects, and catalog hosts must resolve only
    to globally routable addresses before any request is made.
    """

    def __init__(
        self,
        *,
        opener: Any | None = None,
        resolver: Callable[..., list[Any]] | None = None,
        environment: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        github_timeout: float | None = None,
        github_max_attempts: int | None = None,
        github_retry_base_seconds: float | None = None,
        github_total_budget_seconds: float | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self._environment = environment if environment is not None else os.environ
        self.timeout = self._validate_timeout(timeout)
        configured_github_timeout = (
            github_timeout
            if github_timeout is not None
            else self._environment_float(GITHUB_TIMEOUT_ENV, self.timeout)
        )
        configured_github_attempts = (
            github_max_attempts
            if github_max_attempts is not None
            else self._environment_int(GITHUB_MAX_ATTEMPTS_ENV, DEFAULT_GITHUB_MAX_ATTEMPTS)
        )
        configured_retry_base = (
            github_retry_base_seconds
            if github_retry_base_seconds is not None
            else self._environment_float(
                GITHUB_RETRY_BASE_SECONDS_ENV,
                DEFAULT_GITHUB_RETRY_BASE_SECONDS,
            )
        )
        configured_total_budget = (
            github_total_budget_seconds
            if github_total_budget_seconds is not None
            else self._environment_float(
                GITHUB_TOTAL_BUDGET_ENV,
                DEFAULT_GITHUB_TOTAL_BUDGET_SECONDS,
            )
        )
        self.github_timeout = self._validate_github_timeout(configured_github_timeout)
        self.github_max_attempts = self._validate_github_max_attempts(configured_github_attempts)
        self.github_retry_base_seconds = self._validate_github_retry_base(configured_retry_base)
        self.github_total_budget_seconds = self._validate_github_total_budget(configured_total_budget)
        self._sleep = sleeper or time.sleep
        self._clock = clock or time.monotonic
        self.max_response_bytes = self._validate_response_limit(max_response_bytes)
        # An opener is only an explicit test/integration seam. Production catalog
        # requests do not use urllib (and therefore cannot honor proxy variables or
        # re-resolve a hostname after the policy check).
        self._opener = opener
        self._resolver = resolver or socket.getaddrinfo

    def search_github(self, query: str, limit: int = 10, *, sort_by_stars: bool = False) -> list[dict[str, Any]]:
        """Search GitHub repositories and return static-scan-required candidates."""

        return self.search_github_with_metadata(query, limit, sort_by_stars=sort_by_stars)["items"]

    def search_github_with_metadata(
        self,
        query: str,
        limit: int = 10,
        *,
        sort_by_stars: bool = False,
        page: int | None = None,
    ) -> dict[str, Any]:
        """Search GitHub and expose bounded request metadata without credentials."""

        search_query = self._validate_query(query)
        result_limit = self._validate_limit(limit)
        parameters: dict[str, Any] = {'q': search_query, 'per_page': result_limit}
        if page is not None:
            parameters['page'] = self._validate_page(page)
        if sort_by_stars:
            parameters.update({'sort': 'stars', 'order': 'desc'})
        request_url = f"{GITHUB_SEARCH_ENDPOINT}?{urlencode(parameters)}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload, request_metadata = self._fetch_github_json(
            Request(request_url, headers=headers, method="GET"),
            expected_url=request_url,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise self._with_github_request_metadata(
                self._invalid_response("github"),
                request_metadata,
            )

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in payload["items"]:
            if not isinstance(item, dict):
                continue
            full_name = item.get("full_name")
            if not isinstance(full_name, str) or not _GITHUB_FULL_NAME_RE.fullmatch(full_name):
                continue
            repository = f"https://github.com/{full_name}"
            if repository.casefold() in seen:
                continue
            seen.add(repository.casefold())
            fallback_name = full_name.rsplit("/", 1)[-1]
            name = self._clean_text(item.get("name"), maximum=100) or fallback_name
            description = self._clean_text(item.get("description"), maximum=600)
            ref = self._safe_ref(item.get("default_branch"))
            stars = item.get("stargazers_count")
            star_count = int(stars) if isinstance(stars, int) and not isinstance(stars, bool) and stars >= 0 else 0
            repository_updated_at = self._safe_github_timestamp(item.get("updated_at"))
            candidates.append(
                self._candidate(
                    source="github",
                    name=name,
                    description=description,
                    repository=repository,
                    ref=ref,
                    github_stars=star_count,
                    repository_updated_at=repository_updated_at,
                )
            )
            if len(candidates) >= result_limit:
                break
        return {"items": candidates, "request": request_metadata}

    def search_github_pages(
        self,
        query: str,
        limit: int = 100,
        *,
        sort_by_stars: bool = False,
    ) -> dict[str, Any]:
        """Fetch a bounded GitHub result set without exceeding the response ceiling.

        GitHub's 100-result JSON response can exceed SkillSentra's 1 MB response
        ceiling. Two 50-result pages retain the same star ordering while keeping
        each network response independently bounded and auditable.
        """

        total_limit = self._validate_limit(limit)
        page_size = min(50, total_limit)
        page_count = (total_limit + page_size - 1) // page_size
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        page_requests: list[dict[str, Any]] = []
        for page in range(1, page_count + 1):
            result = self.search_github_with_metadata(
                query,
                page_size,
                sort_by_stars=sort_by_stars,
                page=page,
            )
            request_metadata = result.get("request")
            if isinstance(request_metadata, dict):
                page_requests.append(dict(request_metadata))
            page_items = result.get("items")
            if not isinstance(page_items, list):
                break
            for item in page_items:
                if not isinstance(item, dict):
                    continue
                repository = str(item.get("repository") or "").casefold()
                if not repository or repository in seen:
                    continue
                seen.add(repository)
                candidates.append(item)
                if len(candidates) >= total_limit:
                    break
            if len(page_items) < page_size or len(candidates) >= total_limit:
                break

        request: dict[str, Any] = {
            "source": "github",
            "outcome": "success",
            "pages": len(page_requests),
            "page_requests": page_requests,
        }
        if page_requests:
            request["started_at"] = page_requests[0].get("started_at")
            request["completed_at"] = page_requests[-1].get("completed_at")
            request["attempts"] = sum(int(item.get("attempts") or 0) for item in page_requests)
            request["max_attempts"] = sum(int(item.get("max_attempts") or 0) for item in page_requests)
            request["timeout_seconds"] = self.github_timeout
            request["total_request_budget_seconds"] = self.github_total_budget_seconds * len(page_requests)
        return {"items": candidates[:total_limit], "request": request}

    def search_catalog(self, catalog_url: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search a public HTTPS JSON catalog after SSRF-safe URL validation."""

        deadline = time.monotonic() + self.timeout
        target = self._validate_catalog_url(catalog_url, deadline=deadline)
        target_url = target.url
        search_query = self._validate_query(query)
        result_limit = self._validate_limit(limit)
        request = Request(
            target_url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": USER_AGENT,
            },
            method="GET",
        )
        if self._opener is None:
            payload = self._fetch_pinned_catalog_json(target, deadline=deadline)
        else:
            # Explicitly injected openers are retained for deterministic offline
            # testing. They are never constructed or selected by the default path.
            payload = self._fetch_json(request, source="catalog", expected_url=target_url)
        if isinstance(payload, dict):
            skills = payload.get("skills")
        elif isinstance(payload, list):
            skills = payload
        else:
            skills = None
        if not isinstance(skills, list):
            raise self._invalid_response("catalog")

        needle = search_query.casefold()
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str | None]] = set()
        for item in skills:
            if not isinstance(item, dict):
                continue
            name = self._clean_text(item.get("name"), maximum=100)
            description = self._clean_text(item.get("description"), maximum=600)
            repository = self._safe_repository_url(item.get("repository"))
            ref = self._safe_ref(item.get("ref"))
            if not name or not repository:
                continue
            if needle not in f"{name}\n{description}".casefold():
                continue
            identity = (repository.casefold(), ref)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(
                self._candidate(
                    source="catalog",
                    name=name,
                    description=description,
                    repository=repository,
                    ref=ref,
                )
            )
            if len(candidates) >= result_limit:
                break
        return candidates

    def _github_token(self) -> str | None:
        token = self._environment.get(GITHUB_TOKEN_ENV)
        if token is None or token == "":
            return None
        if not isinstance(token, str) or len(token) > 512 or any(character.isspace() for character in token):
            raise DiscoveryError(
                "discovery_invalid_github_token",
                "GitHub discovery token is invalid.",
                500,
                {"source": "github"},
            )
        return token

    def _fetch_github_json(self, request: Request, *, expected_url: str) -> tuple[Any, dict[str, Any]]:
        started_at = utc_now()
        deadline = self._clock() + self.github_total_budget_seconds
        retry_delays: list[float] = []
        retry_sources: list[str] = []
        for attempt in range(1, self.github_max_attempts + 1):
            remaining_request_budget = max(0.0, deadline - self._clock())
            if remaining_request_budget <= 0:
                error = DiscoveryError(
                    "discovery_upstream_timeout",
                    "GitHub discovery exceeded its total request budget.",
                    504,
                    {"source": "github"},
                )
                metadata = self._github_request_metadata(
                    started_at,
                    attempt - 1,
                    retry_delays,
                    retry_sources,
                    outcome="error",
                    error_code=error.code,
                    retry_skipped_reason="total_request_budget_exhausted",
                )
                raise self._with_github_request_metadata(error, metadata) from None
            try:
                payload = self._fetch_json(
                    request,
                    source="github",
                    expected_url=expected_url,
                    timeout=min(self.github_timeout, remaining_request_budget),
                    deadline=deadline,
                )
            except DiscoveryError as exc:
                retry_delay: float | None = None
                retry_source = ""
                retry_skipped_reason = ""
                remaining_delay_budget = max(
                    0.0,
                    MAX_GITHUB_TOTAL_RETRY_DELAY_SECONDS - sum(retry_delays),
                )
                remaining_request_budget = max(0.0, deadline - self._clock())
                if attempt >= self.github_max_attempts:
                    retry_skipped_reason = "maximum_attempts_reached"
                elif not self._is_retryable_github_error(exc):
                    retry_skipped_reason = "non_retryable_error"
                else:
                    server_delay = exc.details.get("retry_after_seconds")
                    if isinstance(server_delay, (int, float)) and not isinstance(server_delay, bool):
                        retry_delay = max(0.0, float(server_delay))
                        retry_source = str(exc.details.get("retry_after_source") or "server_retry_hint")
                    else:
                        retry_delay = min(
                            self.github_retry_base_seconds * (2 ** (attempt - 1)),
                            MAX_GITHUB_RETRY_BASE_SECONDS,
                        )
                        retry_source = "exponential_backoff"
                    if retry_delay > min(remaining_delay_budget, remaining_request_budget):
                        retry_skipped_reason = (
                            "server_retry_exceeds_budget"
                            if retry_source != "exponential_backoff"
                            else (
                                "total_request_budget_exhausted"
                                if retry_delay > remaining_request_budget
                                else "retry_delay_budget_exhausted"
                            )
                        )
                        retry_delay = None
                if retry_delay is None:
                    metadata = self._github_request_metadata(
                        started_at,
                        attempt,
                        retry_delays,
                        retry_sources,
                        outcome="error",
                        error_code=exc.code,
                        retry_skipped_reason=retry_skipped_reason,
                        server_retry_after_seconds=exc.details.get("retry_after_seconds"),
                    )
                    raise self._with_github_request_metadata(exc, metadata) from None
                retry_delays.append(retry_delay)
                retry_sources.append(retry_source)
                if retry_delay > 0:
                    self._sleep(retry_delay)
                continue
            metadata = self._github_request_metadata(
                started_at,
                attempt,
                retry_delays,
                retry_sources,
                outcome="success",
            )
            return payload, metadata
        raise AssertionError("GitHub request loop exhausted without a result")

    def _fetch_json(
        self,
        request: Request,
        *,
        source: str,
        expected_url: str,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> Any:
        opener = self._opener or build_opener(_NoRedirectHandler())
        request_timeout = self.timeout if timeout is None else timeout
        request_deadline = self._clock() + request_timeout
        if deadline is not None:
            request_deadline = min(request_deadline, deadline)
        try:
            with opener.open(request, timeout=request_timeout) as response:
                raw = self._read_bounded_response(
                    response,
                    source=source,
                    expected_url=expected_url,
                    deadline=request_deadline,
                )
        except DiscoveryError:
            raise
        except HTTPError as exc:
            if source == "catalog" and 300 <= int(exc.code) < 400:
                raise self._redirect_error() from None
            raise self._http_error(source, int(exc.code), getattr(exc, "headers", None)) from None
        except (TimeoutError, socket.timeout):
            raise DiscoveryError(
                "discovery_upstream_timeout",
                "The external discovery source timed out.",
                504,
                {"source": source},
            ) from None
        except (URLError, OSError, http.client.HTTPException):
            raise DiscoveryError(
                "discovery_upstream_unavailable",
                "The external discovery source is unavailable.",
                502,
                {"source": source},
            ) from None
        except Exception:
            # Opener implementations are injectable; never surface their exception text,
            # because it may contain request headers (including a GitHub token).
            raise DiscoveryError(
                "discovery_upstream_unavailable",
                "The external discovery source is unavailable.",
                502,
                {"source": source},
            ) from None

        return self._decode_json(raw, source)

    def _github_request_metadata(
        self,
        started_at: str,
        attempts: int,
        retry_delays: list[float],
        retry_sources: list[str],
        *,
        outcome: str,
        error_code: str | None = None,
        retry_skipped_reason: str = "",
        server_retry_after_seconds: Any = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "source": "github",
            "started_at": started_at,
            "completed_at": utc_now(),
            "outcome": outcome,
            "attempts": attempts,
            "max_attempts": self.github_max_attempts,
            "timeout_seconds": self.github_timeout,
            "retry_delays_seconds": list(retry_delays),
            "retry_delay_sources": list(retry_sources),
            "retry_delay_budget_seconds": MAX_GITHUB_TOTAL_RETRY_DELAY_SECONDS,
            "retry_delay_budget_remaining_seconds": max(
                0.0,
                MAX_GITHUB_TOTAL_RETRY_DELAY_SECONDS - sum(retry_delays),
            ),
            "total_request_budget_seconds": self.github_total_budget_seconds,
        }
        if error_code:
            metadata["error_code"] = error_code
        if retry_skipped_reason:
            metadata["retry_skipped_reason"] = retry_skipped_reason
        if isinstance(server_retry_after_seconds, (int, float)) and not isinstance(server_retry_after_seconds, bool):
            metadata["server_retry_after_seconds"] = max(0.0, float(server_retry_after_seconds))
        return metadata

    @staticmethod
    def _is_retryable_github_error(error: DiscoveryError) -> bool:
        if error.code in {
            "discovery_upstream_timeout",
            "discovery_upstream_unavailable",
            "discovery_upstream_invalid_response",
            "discovery_github_rate_limited",
        }:
            return True
        return error.details.get("http_status") in {403, 429, 500, 502, 503, 504}

    @staticmethod
    def _with_github_request_metadata(
        error: DiscoveryError,
        request_metadata: dict[str, Any],
    ) -> DiscoveryError:
        details = dict(error.details)
        details["request"] = request_metadata
        return DiscoveryError(error.code, error.message, error.status, details)

    def _fetch_pinned_catalog_json(self, target: _CatalogTarget, *, deadline: float) -> Any:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        if hasattr(ssl, "TLSVersion"):
            context.minimum_version = ssl.TLSVersion.TLSv1_2

        last_failure: BaseException | None = None
        for address in target.addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._timeout_error("catalog")
            connection = _PinnedHTTPSConnection(
                target.hostname,
                address,
                timeout=remaining,
                context=context,
            )
            try:
                connection.request(
                    "GET",
                    target.request_target,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "User-Agent": USER_AGENT,
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                raw = self._read_bounded_response(
                    response,
                    source="catalog",
                    expected_url=target.url,
                    deadline=deadline,
                )
                return self._decode_json(raw, "catalog")
            except DiscoveryError:
                raise
            except (TimeoutError, socket.timeout) as exc:
                last_failure = exc
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_failure = exc
            finally:
                connection.close()

        if isinstance(last_failure, (TimeoutError, socket.timeout)) or time.monotonic() >= deadline:
            raise self._timeout_error("catalog")
        raise DiscoveryError(
            "discovery_upstream_unavailable",
            "The external discovery source is unavailable.",
            502,
            {"source": "catalog"},
        ) from None

    def _read_bounded_response(
        self,
        response: Any,
        *,
        source: str,
        expected_url: str,
        deadline: float,
    ) -> bytes:
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        headers = getattr(response, "headers", {})
        if status is not None and not 200 <= int(status) < 300:
            if source == "catalog" and 300 <= int(status) < 400:
                raise self._redirect_error()
            raise self._http_error(source, int(status), headers)

        final_url = response.geturl() if hasattr(response, "geturl") else expected_url
        if final_url and final_url != expected_url:
            if source == "catalog":
                raise self._redirect_error()
            raise self._http_error(source, 302)

        content_encoding = str(headers.get("Content-Encoding", "identity")).strip().lower()
        if content_encoding not in {"", "identity"}:
            raise self._invalid_response(source)
        content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type and content_type != "application/json" and not content_type.endswith("+json"):
            raise self._invalid_response(source)
        declared_size = headers.get("Content-Length")
        if declared_size is not None:
            try:
                if int(declared_size) > self.max_response_bytes:
                    raise self._response_too_large(source)
            except (TypeError, ValueError):
                pass
        reader = getattr(response, "read1", None)
        if not callable(reader):
            remaining_seconds = deadline - self._clock()
            if remaining_seconds <= 0:
                raise self._timeout_error(source)
            self._set_response_timeout(response, remaining_seconds)
            raw = response.read(self.max_response_bytes + 1)
            if self._clock() >= deadline:
                raise self._timeout_error(source)
            if not isinstance(raw, (bytes, bytearray)):
                raise self._invalid_response(source)
            if len(raw) > self.max_response_bytes:
                raise self._response_too_large(source)
            return bytes(raw)
        chunks: list[bytes] = []
        received = 0
        while received <= self.max_response_bytes:
            remaining_seconds = deadline - self._clock()
            if remaining_seconds <= 0:
                raise self._timeout_error(source)
            self._set_response_timeout(response, remaining_seconds)
            amount = min(64 * 1024, self.max_response_bytes + 1 - received)
            raw_chunk = reader(amount)
            if self._clock() >= deadline:
                raise self._timeout_error(source)
            if not isinstance(raw_chunk, (bytes, bytearray)):
                raise self._invalid_response(source)
            if not raw_chunk:
                break
            chunk = bytes(raw_chunk)
            chunks.append(chunk)
            received += len(chunk)
        raw = b"".join(chunks)
        if len(raw) > self.max_response_bytes:
            raise self._response_too_large(source)
        return raw

    @staticmethod
    def _set_response_timeout(response: Any, timeout: float) -> None:
        """Best-effort socket deadline tightening for urllib/http.client responses."""

        candidates = [
            response,
            getattr(response, "fp", None),
            getattr(getattr(response, "fp", None), "raw", None),
            getattr(getattr(response, "fp", None), "_sock", None),
            getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None),
        ]
        for candidate in candidates:
            setter = getattr(candidate, "settimeout", None)
            if not callable(setter):
                continue
            try:
                setter(max(0.001, timeout))
                return
            except (OSError, ValueError):
                continue

    def _decode_json(self, raw: bytes, source: str) -> Any:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise self._invalid_response(source) from None

    def _validate_catalog_url(self, value: str, *, deadline: float) -> _CatalogTarget:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 2048
            or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise self._catalog_url_error()
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise self._catalog_url_error() from None
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.fragment
        ):
            raise self._catalog_url_error()

        hostname = parsed.hostname.rstrip(".").lower()
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            raise self._catalog_url_error() from None
        if (
            not ascii_hostname
            or "." not in ascii_hostname
            or not _DNS_NAME_RE.fullmatch(ascii_hostname)
            or ascii_hostname == "localhost"
            or ascii_hostname.endswith((".localhost", ".local", ".internal"))
        ):
            try:
                direct_ip = ipaddress.ip_address(ascii_hostname.split("%", 1)[0])
            except ValueError:
                raise self._catalog_url_error() from None
            if not direct_ip.is_global:
                raise self._catalog_url_error()

        try:
            direct_ip = ipaddress.ip_address(ascii_hostname.split("%", 1)[0])
        except ValueError:
            direct_ip = None
        if direct_ip is not None:
            if not direct_ip.is_global:
                raise self._catalog_url_error()
            family = socket.AF_INET6 if direct_ip.version == 6 else socket.AF_INET
            sockaddr: tuple[Any, ...]
            if family == socket.AF_INET6:
                sockaddr = (str(direct_ip), 443, 0, 0)
            else:
                sockaddr = (str(direct_ip), 443)
            return self._catalog_target(
                value,
                parsed,
                ascii_hostname,
                (_PinnedAddress(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, sockaddr),),
            )

        try:
            resolved = self._resolver(ascii_hostname, 443, 0, socket.SOCK_STREAM)
        except Exception:
            raise DiscoveryError(
                "discovery_catalog_resolution_failed",
                "The catalog host could not be safely resolved.",
                400,
                {"source": "catalog"},
            ) from None
        if time.monotonic() >= deadline:
            raise self._timeout_error("catalog")
        addresses: list[_PinnedAddress] = []
        seen_addresses: set[tuple[int, tuple[Any, ...]]] = set()
        for record in resolved:
            try:
                family, socktype, proto, _canonical_name, sockaddr = record
                address_text = str(sockaddr[0]).split("%", 1)[0]
                address = ipaddress.ip_address(address_text)
            except (IndexError, TypeError, ValueError):
                raise self._catalog_url_error() from None
            if (
                family not in {socket.AF_INET, socket.AF_INET6}
                or socktype not in {0, socket.SOCK_STREAM}
                or address.version != (6 if family == socket.AF_INET6 else 4)
                or not address.is_global
            ):
                raise self._catalog_url_error()
            normalized_sockaddr: tuple[Any, ...]
            if family == socket.AF_INET6:
                flowinfo = int(sockaddr[2]) if len(sockaddr) > 2 else 0
                scope_id = int(sockaddr[3]) if len(sockaddr) > 3 else 0
                if scope_id != 0:
                    raise self._catalog_url_error()
                normalized_sockaddr = (str(address), 443, flowinfo, 0)
            else:
                normalized_sockaddr = (str(address), 443)
            identity = (family, normalized_sockaddr)
            if identity in seen_addresses:
                continue
            seen_addresses.add(identity)
            addresses.append(
                _PinnedAddress(
                    family,
                    socket.SOCK_STREAM,
                    int(proto) or socket.IPPROTO_TCP,
                    normalized_sockaddr,
                )
            )
            if len(addresses) >= 8:
                break
        if not addresses:
            raise self._catalog_url_error()
        return self._catalog_target(value, parsed, ascii_hostname, tuple(addresses))

    @staticmethod
    def _catalog_target(
        value: str,
        parsed: Any,
        hostname: str,
        addresses: tuple[_PinnedAddress, ...],
    ) -> _CatalogTarget:
        request_target = parsed.path or "/"
        if parsed.query:
            request_target = f"{request_target}?{parsed.query}"
        try:
            request_target.encode("ascii")
        except UnicodeEncodeError:
            raise SkillDiscoveryService._catalog_url_error() from None
        return _CatalogTarget(value, hostname, request_target, addresses)

    @staticmethod
    def _safe_repository_url(value: Any) -> str | None:
        if not isinstance(value, str) or not value or len(value) > 2048:
            return None
        if value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            return None
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.fragment
            or parsed.query
            or parsed.path in {"", "/"}
        ):
            return None
        hostname = parsed.hostname.rstrip(".").lower()
        try:
            address = ipaddress.ip_address(hostname.split("%", 1)[0])
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        if address is None and not _DNS_NAME_RE.fullmatch(hostname):
            return None
        if hostname in {"localhost"} or hostname.endswith((".localhost", ".local", ".internal")):
            return None
        return value

    @staticmethod
    def _safe_ref(value: Any) -> str | None:
        ref = SkillDiscoveryService._clean_text(value, maximum=200)
        if (
            not _SAFE_REF_RE.fullmatch(ref)
            or ".." in ref
            or ref.startswith("/")
            or ref.endswith("/")
        ):
            return None
        return ref

    @staticmethod
    def _candidate(
        *, source: str, name: str, description: str, repository: str, ref: str | None,
        github_stars: int | None = None,
        repository_updated_at: str | None = None,
    ) -> dict[str, Any]:
        candidate = {
            "source": source,
            "name": name,
            "description": description,
            "repository": repository,
            "ref": ref,
            "verification_status": "unverified_candidate",
            "unverified_candidate": True,
            "requires_static_scan": True,
        }
        if source == "github" and github_stars is not None:
            candidate["github_stars"] = github_stars
        if source == "github" and repository_updated_at is not None:
            candidate["repository_updated_at"] = repository_updated_at
        return candidate

    @staticmethod
    def _safe_github_timestamp(value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 40:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _clean_text(value: Any, *, maximum: int) -> str:
        if not isinstance(value, str):
            return ""
        cleaned = " ".join(value.split())
        if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
            return ""
        return cleaned[:maximum]

    @staticmethod
    def _validate_query(value: str) -> str:
        if not isinstance(value, str):
            raise DiscoveryError("discovery_invalid_query", "Query must contain 2-80 characters.", 400)
        query = value.strip()
        if (
            not 2 <= len(query) <= 80
            or any(ord(character) < 32 or ord(character) == 127 for character in query)
        ):
            raise DiscoveryError("discovery_invalid_query", "Query must contain 2-80 characters.", 400)
        return query

    @staticmethod
    def _validate_limit(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise DiscoveryError("discovery_invalid_limit", "Limit must be an integer from 1 to 100.", 400)
        return value

    @staticmethod
    def _validate_page(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise DiscoveryError("discovery_invalid_page", "Page must be an integer from 1 to 100.", 400)
        return value

    def _environment_float(self, name: str, default: float) -> float:
        raw = self._environment.get(name)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise self._configuration_error(name) from None

    def _environment_int(self, name: str, default: int) -> int:
        raw = self._environment.get(name)
        if raw is None or raw == "":
            return default
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
            raise self._configuration_error(name)
        try:
            return int(raw)
        except ValueError:
            raise self._configuration_error(name) from None

    @staticmethod
    def _validate_timeout(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= MAX_TIMEOUT_SECONDS:
            raise DiscoveryError(
                "discovery_invalid_timeout",
                f"Timeout must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds.",
                500,
            )
        return float(value)

    @staticmethod
    def _validate_github_timeout(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= MAX_TIMEOUT_SECONDS:
            raise DiscoveryError(
                "discovery_invalid_configuration",
                f"GitHub timeout must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds.",
                500,
                {"configuration_field": GITHUB_TIMEOUT_ENV},
            )
        return float(value)

    @staticmethod
    def _validate_github_max_attempts(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_GITHUB_MAX_ATTEMPTS:
            raise DiscoveryError(
                "discovery_invalid_configuration",
                f"GitHub maximum attempts must be from 1 to {MAX_GITHUB_MAX_ATTEMPTS}.",
                500,
                {"configuration_field": GITHUB_MAX_ATTEMPTS_ENV},
            )
        return value

    @staticmethod
    def _validate_github_retry_base(value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= MAX_GITHUB_RETRY_BASE_SECONDS
        ):
            raise DiscoveryError(
                "discovery_invalid_configuration",
                f"GitHub retry base must be from 0 to {MAX_GITHUB_RETRY_BASE_SECONDS:g} seconds.",
                500,
                {"configuration_field": GITHUB_RETRY_BASE_SECONDS_ENV},
            )
        return float(value)

    @staticmethod
    def _validate_github_total_budget(value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < float(value) <= MAX_GITHUB_TOTAL_BUDGET_SECONDS
        ):
            raise DiscoveryError(
                "discovery_invalid_configuration",
                f"GitHub total request budget must be greater than 0 and at most {MAX_GITHUB_TOTAL_BUDGET_SECONDS:g} seconds.",
                500,
                {"configuration_field": GITHUB_TOTAL_BUDGET_ENV},
            )
        return float(value)

    @staticmethod
    def _configuration_error(name: str) -> DiscoveryError:
        return DiscoveryError(
            "discovery_invalid_configuration",
            "GitHub discovery retry configuration is invalid.",
            500,
            {"configuration_field": name},
        )

    @staticmethod
    def _validate_response_limit(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= HARD_MAX_RESPONSE_BYTES:
            raise DiscoveryError(
                "discovery_invalid_response_limit",
                f"Response limit must be from 1 to {HARD_MAX_RESPONSE_BYTES} bytes.",
                500,
            )
        return value

    @staticmethod
    def _catalog_url_error() -> DiscoveryError:
        return DiscoveryError(
            "discovery_catalog_url_blocked",
            "Catalog URL must be public HTTPS on standard port 443 without credentials or redirects.",
            400,
            {"source": "catalog"},
        )

    @staticmethod
    def _redirect_error() -> DiscoveryError:
        return DiscoveryError(
            "discovery_catalog_redirect_blocked",
            "Catalog redirects are not allowed.",
            400,
            {"source": "catalog"},
        )

    @staticmethod
    def _response_too_large(source: str) -> DiscoveryError:
        return DiscoveryError(
            "discovery_upstream_response_too_large",
            "The external discovery response exceeded the allowed size.",
            502,
            {"source": source},
        )

    @staticmethod
    def _invalid_response(source: str) -> DiscoveryError:
        return DiscoveryError(
            "discovery_upstream_invalid_response",
            "The external discovery source returned an invalid response.",
            502,
            {"source": source},
        )

    @staticmethod
    def _timeout_error(source: str) -> DiscoveryError:
        return DiscoveryError(
            "discovery_upstream_timeout",
            "The external discovery source timed out.",
            504,
            {"source": source},
        )

    @staticmethod
    def _github_retry_hint(headers: Any) -> tuple[float, str] | None:
        if headers is None or not hasattr(headers, "get"):
            return None
        candidates: list[tuple[float, str]] = []
        retry_after = str(headers.get("Retry-After", "")).strip()
        if 0 < len(retry_after) <= 128:
            if retry_after.isascii() and retry_after.isdecimal():
                candidates.append((float(int(retry_after)), "retry_after"))
            else:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    candidates.append((max(0.0, retry_at.timestamp() - time.time()), "retry_after"))
                except (TypeError, ValueError, OverflowError):
                    pass
        rate_limit_reset = str(headers.get("X-RateLimit-Reset", "")).strip()
        if (
            0 < len(rate_limit_reset) <= 20
            and rate_limit_reset.isascii()
            and rate_limit_reset.isdecimal()
        ):
            try:
                candidates.append(
                    (max(0.0, float(int(rate_limit_reset)) - time.time()), "x_rate_limit_reset")
                )
            except (ValueError, OverflowError):
                pass
        return max(candidates, key=lambda item: item[0]) if candidates else None

    @staticmethod
    def _http_error(source: str, status: int, headers: Any = None) -> DiscoveryError:
        if source == "github" and status in {403, 429}:
            code = "discovery_github_rate_limited"
            message = "GitHub discovery is rate limited or unavailable."
        elif source == "github" and status == 401:
            code = "discovery_github_auth_failed"
            message = "GitHub discovery authentication failed."
        else:
            code = "discovery_upstream_http_error"
            message = "The external discovery source returned an HTTP error."
        details: dict[str, Any] = {"source": source, "http_status": status}
        if source == "github":
            retry_hint = SkillDiscoveryService._github_retry_hint(headers)
            if retry_hint is not None:
                details["retry_after_seconds"], details["retry_after_source"] = retry_hint
        return DiscoveryError(code, message, 502, details)


class GitHubSkillCandidateCatalog:
    """Persist a transparent GitHub-star-sorted candidate list outside the marketplace."""

    QUERY = "topic:skill"

    def __init__(self, database: Database, discovery: SkillDiscoveryService):
        self.database = database
        self.discovery = discovery

    def refresh_top_100(self) -> dict[str, Any]:
        attempted_at = utc_now()
        request_metadata: dict[str, Any] = {
            "source": "github",
            "started_at": attempted_at,
            "completed_at": attempted_at,
            "outcome": "unknown",
            "attempts": 1,
        }
        try:
            search_pages = getattr(self.discovery, "search_github_pages", None)
            search_with_metadata = getattr(self.discovery, "search_github_with_metadata", None)
            if callable(search_pages):
                result = search_pages(self.QUERY, 100, sort_by_stars=True)
                candidates = result["items"]
                request_metadata = dict(result.get("request") or request_metadata)
            elif callable(search_with_metadata):
                result = search_with_metadata(self.QUERY, 100, sort_by_stars=True)
                candidates = result["items"]
                request_metadata = dict(result.get("request") or request_metadata)
            else:
                candidates = self.discovery.search_github(self.QUERY, 100, sort_by_stars=True)
                request_metadata.update({"completed_at": utc_now(), "outcome": "success"})
            if not candidates:
                raise DiscoveryError(
                    "discovery_github_empty_result",
                    "GitHub returned no Skill candidates; the last successful ranking was preserved.",
                    502,
                    {"source": "github", "request": request_metadata},
                )
        except DiscoveryError as exc:
            items = self.list_top_100()
            last_success_at = self._last_success_at(items)
            refresh = self._refresh_metadata(
                attempted_at=attempted_at,
                completed_at=utc_now(),
                status="stale_fallback" if items else "unavailable",
                items=items,
                used_cached_snapshot=bool(items),
                last_success_at=last_success_at,
                request_metadata=dict(exc.details.get("request") or request_metadata),
                error=exc,
            )
            if not items:
                details = dict(exc.details)
                details["refresh"] = refresh
                raise DiscoveryError(exc.code, exc.message, exc.status, details) from None
            return {
                "source_query": self.QUERY,
                "refreshed_at": last_success_at,
                "count": len(items),
                "items": items,
                "refresh": refresh,
            }
        captured_at = utc_now()
        with self.database.transaction() as connection:
            for rank, item in enumerate(candidates, start=1):
                repository = str(item["repository"])
                record_id = f"ghc_{hashlib.sha256(repository.encode('utf-8')).hexdigest()[:24]}"
                connection.execute(
                    """
                    INSERT INTO github_skill_candidates(
                        id, repository, name, description, ref, repository_updated_at, github_stars, source_rank,
                        source_query, verification_status, requires_static_scan, first_seen_at, refreshed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(repository) DO UPDATE SET
                        name=excluded.name, description=excluded.description, ref=excluded.ref,
                        repository_updated_at=excluded.repository_updated_at,
                        github_stars=excluded.github_stars, source_rank=excluded.source_rank,
                        source_query=excluded.source_query, verification_status='unverified_candidate',
                        requires_static_scan=1, refreshed_at=excluded.refreshed_at
                    """,
                    (
                        record_id, repository, item["name"], item["description"], item.get("ref"),
                        item.get("repository_updated_at"),
                        int(item.get("github_stars", 0)), rank, self.QUERY, "unverified_candidate", 1,
                        captured_at, captured_at,
                    ),
                )
            repositories = [str(item["repository"]) for item in candidates]
            placeholders = ",".join("?" for _ in repositories)
            connection.execute(
                f"DELETE FROM github_skill_candidates WHERE source_query=? AND repository NOT IN ({placeholders})",
                (self.QUERY, *repositories),
            )
        items = self.list_top_100()
        return {
            "source_query": self.QUERY,
            "refreshed_at": captured_at,
            "count": len(items),
            "items": items,
            "refresh": self._refresh_metadata(
                attempted_at=attempted_at,
                completed_at=utc_now(),
                status="success",
                items=items,
                used_cached_snapshot=False,
                last_success_at=captured_at,
                request_metadata=request_metadata,
                error=None,
            ),
        }

    def list_top_100(self) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT name, description, repository, ref, repository_updated_at, github_stars, source_rank,
                       verification_status, requires_static_scan, refreshed_at
                FROM github_skill_candidates
                WHERE source_query=?
                ORDER BY source_rank ASC, repository ASC
                LIMIT 100
                """,
                (self.QUERY,),
            ).fetchall()
        return [
            {
                **dict(row),
                "source": "github",
                "unverified_candidate": True,
                "requires_static_scan": bool(row["requires_static_scan"]),
            }
            for row in rows
        ]

    @staticmethod
    def _last_success_at(items: list[dict[str, Any]]) -> str | None:
        values = [str(item["refreshed_at"]) for item in items if item.get("refreshed_at")]
        return max(values) if values else None

    @classmethod
    def _refresh_metadata(
        cls,
        *,
        attempted_at: str,
        completed_at: str,
        status: str,
        items: list[dict[str, Any]],
        used_cached_snapshot: bool,
        last_success_at: str | None,
        request_metadata: dict[str, Any],
        error: DiscoveryError | None,
    ) -> dict[str, Any]:
        snapshot_sha256 = cls._snapshot_sha256(items)
        refresh_seed = "\0".join((cls.QUERY, attempted_at, status, snapshot_sha256))
        metadata: dict[str, Any] = {
            "id": f"ghr_{hashlib.sha256(refresh_seed.encode('utf-8')).hexdigest()[:24]}",
            "status": status,
            "attempted_at": attempted_at,
            "completed_at": completed_at,
            "used_cached_snapshot": used_cached_snapshot,
            "last_success_at": last_success_at,
            "snapshot_sha256": snapshot_sha256,
            "request": request_metadata,
            "error": None,
        }
        if error is not None:
            metadata["error"] = {
                "code": error.code,
                "status": error.status,
                "source": error.details.get("source", "github"),
                "http_status": error.details.get("http_status"),
            }
        return metadata

    @staticmethod
    def _snapshot_sha256(items: list[dict[str, Any]]) -> str:
        snapshot = [
            {
                "repository": item.get("repository"),
                "ref": item.get("ref"),
                "github_stars": item.get("github_stars"),
                "source_rank": item.get("source_rank"),
                "verification_status": item.get("verification_status"),
                "refreshed_at": item.get("refreshed_at"),
            }
            for item in items
        ]
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def search_github_skills(
    query: str,
    limit: int = 10,
    *,
    opener: Any | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> list[dict[str, Any]]:
    """Convenience wrapper for one GitHub discovery request."""

    return SkillDiscoveryService(
        opener=opener,
        environment=environment,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    ).search_github(query, limit)


def search_catalog_skills(
    catalog_url: str,
    query: str,
    limit: int = 10,
    *,
    opener: Any | None = None,
    resolver: Callable[..., list[Any]] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> list[dict[str, Any]]:
    """Convenience wrapper for one catalog discovery request."""

    return SkillDiscoveryService(
        opener=opener,
        resolver=resolver,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    ).search_catalog(catalog_url, query, limit)
