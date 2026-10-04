from __future__ import annotations

import json
import os
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from app.discovery import (
    DiscoveryError,
    GitHubSkillCandidateCatalog,
    SkillDiscoveryService,
    _PinnedAddress,
    _PinnedHTTPSConnection,
)
from app.database import Database


PUBLIC_CATALOG = "https://catalog.example.com/skills.json"
PUBLIC_DNS_RESULT = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
]


class FakeResponse:
    def __init__(
        self,
        payload: object | None = None,
        *,
        body: bytes | None = None,
        url: str | None = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.body = body if body is not None else json.dumps(payload).encode("utf-8")
        self.url = url
        self.status = status
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, amount: int) -> bytes:
        return self.body[:amount]

    def geturl(self) -> str | None:
        return self.url


class FakeOpener:
    def __init__(self, response: FakeResponse | None = None, failure: Exception | None = None):
        self.response = response
        self.failure = failure
        self.calls: list[tuple[object, float]] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        if self.failure:
            raise self.failure
        assert self.response is not None
        if self.response.url is None:
            self.response.url = request.full_url  # type: ignore[attr-defined]
        return self.response


class SequenceOpener:
    def __init__(self, outcomes: list[FakeResponse | Exception]):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[object, float]] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome.url is None:
            outcome.url = request.full_url  # type: ignore[attr-defined]
        return outcome


class SlowDripResponse(FakeResponse):
    def __init__(self, clock: object, payload: object):
        super().__init__(payload)
        self.clock = clock
        self.offset = 0

    def read1(self, _amount: int) -> bytes:
        self.clock.now += 0.25  # type: ignore[attr-defined]
        if self.offset >= len(self.body):
            return b""
        chunk = self.body[self.offset:self.offset + 1]
        self.offset += len(chunk)
        return chunk


class FakeDirectConnection:
    def __init__(
        self,
        hostname: str,
        address: _PinnedAddress,
        *,
        timeout: float,
        context: ssl.SSLContext,
        response: FakeResponse,
        failure: Exception | None = None,
    ):
        self.hostname = hostname
        self.address = address
        self.timeout = timeout
        self.context = context
        self.response = response
        self.failure = failure
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        self.requests.append((method, target, headers))
        if self.failure is not None:
            raise self.failure

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class FakeRawSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.connected_to: tuple[object, ...] | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, sockaddr: tuple[object, ...]) -> None:
        self.connected_to = sockaddr

    def close(self) -> None:
        self.closed = True


class FakeTLSContext:
    post_handshake_auth = False

    def __init__(self) -> None:
        self.calls: list[tuple[FakeRawSocket, str]] = []
        self.secure_socket = object()

    def wrap_socket(self, raw_socket: FakeRawSocket, *, server_hostname: str) -> object:
        self.calls.append((raw_socket, server_hostname))
        return self.secure_socket


def public_resolver(*_args: object) -> list[tuple[object, ...]]:
    return PUBLIC_DNS_RESULT


def _fast_test_tls_context() -> ssl.SSLContext:
    """Create a real client context without loading the host CA store.

    The pinned-transport tests replace the network connection, so loading the
    Windows trust store would add unrelated, machine-dependent latency to a
    one-second network deadline. The production context factory remains covered
    by assertions on the resulting hostname and certificate-verification policy.
    """

    return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class SkillDiscoveryTests(unittest.TestCase):
    def test_github_top_100_refresh_replaces_stale_snapshot_and_preserves_last_good(self) -> None:
        class StubDiscovery:
            def __init__(self) -> None:
                self.batches: list[object] = [
                    [
                        self.candidate("example/a", 300),
                        self.candidate("example/b", 200),
                    ],
                    [
                        self.candidate("example/b", 400),
                        self.candidate("example/c", 250),
                    ],
                    [],
                    DiscoveryError("discovery_upstream_unavailable", "unavailable", 502),
                ]

            @staticmethod
            def candidate(full_name: str, stars: int) -> dict[str, object]:
                return {
                    "name": full_name.rsplit("/", 1)[-1],
                    "description": "candidate",
                    "repository": f"https://github.com/{full_name}",
                    "ref": "main",
                    "repository_updated_at": "2026-08-28T12:00:00Z",
                    "github_stars": stars,
                    "verification_status": "unverified_candidate",
                    "requires_static_scan": True,
                }

            def search_github(self, query: str, limit: int, *, sort_by_stars: bool) -> list[dict[str, object]]:
                self.assert_request(query, limit, sort_by_stars)
                batch = self.batches.pop(0)
                if isinstance(batch, Exception):
                    raise batch
                return batch  # type: ignore[return-value]

            @staticmethod
            def assert_request(query: str, limit: int, sort_by_stars: bool) -> None:
                if (query, limit, sort_by_stars) != ("topic:skill", 100, True):
                    raise AssertionError("unexpected GitHub ranking request")

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "catalog.db")
            database.migrate()
            catalog = GitHubSkillCandidateCatalog(database, StubDiscovery())  # type: ignore[arg-type]

            first = catalog.refresh_top_100()
            self.assertEqual(first["refresh"]["status"], "success")
            self.assertFalse(first["refresh"]["used_cached_snapshot"])
            self.assertEqual(len(first["refresh"]["snapshot_sha256"]), 64)
            self.assertEqual([item["repository"] for item in first["items"]], [
                "https://github.com/example/a", "https://github.com/example/b",
            ])

            second = catalog.refresh_top_100()
            self.assertEqual([item["repository"] for item in second["items"]], [
                "https://github.com/example/b", "https://github.com/example/c",
            ])
            self.assertEqual([item["source_rank"] for item in second["items"]], [1, 2])
            self.assertTrue(all(item["verification_status"] == "unverified_candidate" for item in second["items"]))
            last_good = catalog.list_top_100()

            empty_fallback = catalog.refresh_top_100()
            self.assertEqual(empty_fallback["refresh"]["status"], "stale_fallback")
            self.assertTrue(empty_fallback["refresh"]["used_cached_snapshot"])
            self.assertEqual(empty_fallback["refresh"]["error"]["code"], "discovery_github_empty_result")
            self.assertEqual(empty_fallback["items"], last_good)
            self.assertEqual(catalog.list_top_100(), last_good)

            upstream_fallback = catalog.refresh_top_100()
            self.assertEqual(upstream_fallback["refresh"]["status"], "stale_fallback")
            self.assertEqual(upstream_fallback["refresh"]["error"]["code"], "discovery_upstream_unavailable")
            self.assertEqual(upstream_fallback["refresh"]["snapshot_sha256"], empty_fallback["refresh"]["snapshot_sha256"])
            self.assertEqual(catalog.list_top_100(), last_good)

    def test_github_top_100_cold_start_failure_is_explicit_without_fabricated_items(self) -> None:
        class EmptyDiscovery:
            def search_github(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "catalog.db")
            database.migrate()
            with self.assertRaises(DiscoveryError) as context:
                GitHubSkillCandidateCatalog(database, EmptyDiscovery()).refresh_top_100()  # type: ignore[arg-type]

        self.assertEqual(context.exception.code, "discovery_github_empty_result")
        self.assertEqual(context.exception.status, 502)
        refresh = context.exception.details["refresh"]
        self.assertEqual(refresh["status"], "unavailable")
        self.assertFalse(refresh["used_cached_snapshot"])
        self.assertEqual(refresh["error"]["code"], "discovery_github_empty_result")
        self.assertEqual(len(refresh["id"]), 28)

    def test_github_search_uses_bounded_api_request_and_marks_candidates_unverified(self) -> None:
        token = "github-test-token"
        opener = FakeOpener(
            FakeResponse(
                {
                    "items": [
                        {
                            "name": "research-skill",
                            "full_name": "example/research-skill",
                            "description": "Evidence-bound research workflows",
                            "default_branch": "stable",
                        },
                        {
                            "name": "duplicate",
                            "full_name": "example/research-skill",
                            "description": "duplicate",
                            "default_branch": "stable",
                        },
                    ]
                }
            )
        )
        service = SkillDiscoveryService(
            opener=opener,
            environment={"SKILLSENTRA_GITHUB_TOKEN": token},
            timeout=2.5,
        )

        results = service.search_github("research skill", limit=2)

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0],
            {
                "source": "github",
                "name": "research-skill",
                "description": "Evidence-bound research workflows",
                "repository": "https://github.com/example/research-skill",
                "ref": "stable",
                "verification_status": "unverified_candidate",
                "unverified_candidate": True,
                "requires_static_scan": True,
                "github_stars": 0,
            },
        )
        request, timeout = opener.calls[0]
        parsed = urlsplit(request.full_url)  # type: ignore[attr-defined]
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "api.github.com")
        self.assertEqual(parsed.path, "/search/repositories")
        self.assertEqual(parse_qs(parsed.query), {"q": ["research skill"], "per_page": ["2"]})
        self.assertEqual(request.get_header("Authorization"), f"Bearer {token}")  # type: ignore[attr-defined]
        self.assertNotIn(token, request.full_url)  # type: ignore[attr-defined]
        self.assertEqual(timeout, 2.5)

    def test_github_transient_failures_use_configured_bounded_backoff_and_metadata(self) -> None:
        opener = SequenceOpener([
            URLError("temporary network failure"),
            socket.timeout("temporary timeout"),
            FakeResponse({"items": [{
                "name": "resilient-skill",
                "full_name": "example/resilient-skill",
                "description": "retry proof",
                "default_branch": "main",
            }]}),
        ])
        delays: list[float] = []
        service = SkillDiscoveryService(
            opener=opener,
            environment={
                "SKILLSENTRA_GITHUB_TIMEOUT_SECONDS": "1.5",
                "SKILLSENTRA_GITHUB_MAX_ATTEMPTS": "3",
                "SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS": "0.1",
            },
            sleeper=delays.append,
        )

        result = service.search_github_with_metadata("topic:skill", 100, sort_by_stars=True)

        self.assertEqual([item["name"] for item in result["items"]], ["resilient-skill"])
        self.assertEqual([timeout for _request, timeout in opener.calls], [1.5, 1.5, 1.5])
        self.assertEqual(delays, [0.1, 0.2])
        self.assertEqual(result["request"]["outcome"], "success")
        self.assertEqual(result["request"]["attempts"], 3)
        self.assertEqual(result["request"]["max_attempts"], 3)
        self.assertEqual(result["request"]["retry_delays_seconds"], [0.1, 0.2])

    def test_github_non_retryable_auth_failure_stops_after_one_attempt(self) -> None:
        github_url = "https://api.github.com/search/repositories?q=valid&per_page=10"
        opener = SequenceOpener([
            HTTPError(github_url, 401, "Unauthorized", {}, None),
        ])
        delays: list[float] = []
        service = SkillDiscoveryService(opener=opener, environment={}, sleeper=delays.append)

        with self.assertRaises(DiscoveryError) as context:
            service.search_github("valid")

        self.assertEqual(context.exception.code, "discovery_github_auth_failed")
        self.assertEqual(context.exception.details["request"]["attempts"], 1)
        self.assertEqual(context.exception.details["request"]["retry_delays_seconds"], [])
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(delays, [])
        self.assertIn("api.github.com", github_url)

    def test_github_retry_after_within_budget_is_respected(self) -> None:
        github_url = "https://api.github.com/search/repositories?q=valid&per_page=10"
        opener = SequenceOpener([
            HTTPError(github_url, 429, "Too Many Requests", {"Retry-After": "1"}, None),
            FakeResponse({"items": []}),
        ])
        delays: list[float] = []
        service = SkillDiscoveryService(opener=opener, environment={}, sleeper=delays.append)

        result = service.search_github_with_metadata("valid")

        self.assertEqual(delays, [1.0])
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(result["request"]["attempts"], 2)
        self.assertEqual(result["request"]["retry_delay_sources"], ["retry_after"])

    def test_github_rate_limit_reset_within_budget_is_respected(self) -> None:
        github_url = "https://api.github.com/search/repositories?q=valid&per_page=10"
        opener = SequenceOpener([
            HTTPError(github_url, 403, "Rate Limited", {"X-RateLimit-Reset": "1002"}, None),
            FakeResponse({"items": []}),
        ])
        delays: list[float] = []
        service = SkillDiscoveryService(opener=opener, environment={}, sleeper=delays.append)

        with patch("app.discovery.time.time", return_value=1000.0):
            result = service.search_github_with_metadata("valid")

        self.assertEqual(delays, [2.0])
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(result["request"]["retry_delay_sources"], ["x_rate_limit_reset"])

    def test_github_server_retry_hint_over_total_budget_fails_without_sleeping(self) -> None:
        github_url = "https://api.github.com/search/repositories?q=valid&per_page=10"
        opener = SequenceOpener([
            HTTPError(github_url, 429, "Too Many Requests", {"Retry-After": "120"}, None),
        ])
        delays: list[float] = []
        service = SkillDiscoveryService(opener=opener, environment={}, sleeper=delays.append)

        with self.assertRaises(DiscoveryError) as context:
            service.search_github("valid")

        request = context.exception.details["request"]
        self.assertEqual(context.exception.code, "discovery_github_rate_limited")
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(delays, [])
        self.assertEqual(request["attempts"], 1)
        self.assertEqual(request["retry_skipped_reason"], "server_retry_exceeds_budget")
        self.assertEqual(request["server_retry_after_seconds"], 120.0)
        self.assertEqual(request["retry_delay_budget_seconds"], 6.0)
        self.assertEqual(request["total_request_budget_seconds"], 10.0)

    def test_github_network_attempts_and_backoff_share_one_total_budget(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.now = 0.0

            def __call__(self) -> float:
                return self.now

            def sleep(self, delay: float) -> None:
                self.now += delay

        class TimedFailureOpener:
            def __init__(self, clock: FakeClock) -> None:
                self.clock = clock
                self.timeouts: list[float] = []

            def open(self, _request: object, timeout: float) -> FakeResponse:
                self.timeouts.append(timeout)
                self.clock.now += min(4.0, timeout)
                raise socket.timeout("bounded timeout")

        clock = FakeClock()
        opener = TimedFailureOpener(clock)
        service = SkillDiscoveryService(
            opener=opener,
            environment={},
            github_total_budget_seconds=10.0,
            clock=clock,
            sleeper=clock.sleep,
        )

        with self.assertRaises(DiscoveryError) as context:
            service.search_github("valid")

        self.assertEqual(context.exception.code, "discovery_upstream_timeout")
        self.assertEqual(opener.timeouts, [5.0, 5.0, 1.25])
        self.assertEqual(clock.now, 10.0)
        self.assertEqual(context.exception.details["request"]["total_request_budget_seconds"], 10.0)

    def test_github_slow_drip_body_cannot_extend_the_wall_clock_budget(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.now = 0.0

            def __call__(self) -> float:
                return self.now

        clock = FakeClock()
        opener = FakeOpener(SlowDripResponse(clock, {"items": []}))
        service = SkillDiscoveryService(
            opener=opener,
            environment={},
            github_timeout=1.0,
            github_total_budget_seconds=1.0,
            clock=clock,
            sleeper=lambda delay: setattr(clock, "now", clock.now + delay),
        )

        with self.assertRaises(DiscoveryError) as context:
            service.search_github("valid")

        request = context.exception.details["request"]
        self.assertEqual(context.exception.code, "discovery_upstream_timeout")
        self.assertLessEqual(clock.now, 1.0)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(request["attempts"], 1)
        self.assertEqual(request["retry_skipped_reason"], "total_request_budget_exhausted")

    def test_github_query_limit_and_timeout_are_fail_closed_before_network(self) -> None:
        opener = FakeOpener(FakeResponse({"items": []}))
        service = SkillDiscoveryService(opener=opener, environment={})

        for query in ("x", "x" * 81, "ok\nunsafe"):
            with self.subTest(query=query), self.assertRaises(DiscoveryError) as context:
                service.search_github(query)
            self.assertEqual(context.exception.code, "discovery_invalid_query")
        for limit in (0, 101, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(DiscoveryError) as context:
                service.search_github("valid", limit)  # type: ignore[arg-type]
            self.assertEqual(context.exception.code, "discovery_invalid_limit")
        with self.assertRaises(DiscoveryError) as context:
            SkillDiscoveryService(opener=opener, timeout=11)
        self.assertEqual(context.exception.code, "discovery_invalid_timeout")
        self.assertEqual(opener.calls, [])

    def test_github_retry_configuration_accepts_bounds_and_rejects_invalid_values(self) -> None:
        bounded = SkillDiscoveryService(
            opener=FakeOpener(FakeResponse({"items": []})),
            environment={
                "SKILLSENTRA_GITHUB_TIMEOUT_SECONDS": "10",
                "SKILLSENTRA_GITHUB_MAX_ATTEMPTS": "4",
                "SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS": "2",
                "SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS": "11",
            },
            sleeper=lambda _delay: None,
        )
        self.assertEqual(bounded.github_timeout, 10.0)
        self.assertEqual(bounded.github_max_attempts, 4)
        self.assertEqual(bounded.github_retry_base_seconds, 2.0)
        self.assertEqual(bounded.github_total_budget_seconds, 11.0)

        cases = (
            ({"SKILLSENTRA_GITHUB_TIMEOUT_SECONDS": "0"}, "SKILLSENTRA_GITHUB_TIMEOUT_SECONDS"),
            ({"SKILLSENTRA_GITHUB_TIMEOUT_SECONDS": "not-a-number"}, "SKILLSENTRA_GITHUB_TIMEOUT_SECONDS"),
            ({"SKILLSENTRA_GITHUB_MAX_ATTEMPTS": "0"}, "SKILLSENTRA_GITHUB_MAX_ATTEMPTS"),
            ({"SKILLSENTRA_GITHUB_MAX_ATTEMPTS": "5"}, "SKILLSENTRA_GITHUB_MAX_ATTEMPTS"),
            ({"SKILLSENTRA_GITHUB_MAX_ATTEMPTS": "1.5"}, "SKILLSENTRA_GITHUB_MAX_ATTEMPTS"),
            ({"SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS": "-0.1"}, "SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS"),
            ({"SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS": "2.1"}, "SKILLSENTRA_GITHUB_RETRY_BASE_SECONDS"),
            ({"SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS": "0"}, "SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS"),
            ({"SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS": "11.1"}, "SKILLSENTRA_GITHUB_TOTAL_BUDGET_SECONDS"),
        )
        for environment, field in cases:
            with self.subTest(environment=environment), self.assertRaises(DiscoveryError) as context:
                SkillDiscoveryService(opener=FakeOpener(FakeResponse({"items": []})), environment=environment)
            self.assertEqual(context.exception.code, "discovery_invalid_configuration")
            self.assertEqual(context.exception.details.get("configuration_field"), field)

    def test_github_star_order_is_explicit_and_retained_as_source_metadata(self) -> None:
        opener = FakeOpener(
            FakeResponse({"items": [{
                "name": "top-skill", "full_name": "example/top-skill", "description": "Top candidate",
                "default_branch": "main", "stargazers_count": 1234, "updated_at": "2026-08-28T12:34:56Z",
            }]})
        )
        result = SkillDiscoveryService(opener=opener, environment={}).search_github("topic:skill", 100, sort_by_stars=True)
        request, _timeout = opener.calls[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {"q": ["topic:skill"], "per_page": ["100"], "sort": ["stars"], "order": ["desc"]})
        self.assertEqual(result[0]["github_stars"], 1234)
        self.assertEqual(result[0]["repository_updated_at"], "2026-08-28T12:34:56Z")
        self.assertEqual(result[0]["verification_status"], "unverified_candidate")

    def test_github_response_size_and_json_shape_are_bounded(self) -> None:
        too_large = SkillDiscoveryService(
            opener=FakeOpener(FakeResponse(body=b"x" * 33)),
            environment={},
            max_response_bytes=32,
        )
        with self.assertRaises(DiscoveryError) as context:
            too_large.search_github("valid")
        self.assertEqual(context.exception.code, "discovery_upstream_response_too_large")

        invalid = SkillDiscoveryService(
            opener=FakeOpener(FakeResponse(body=b'{"items": "wrong"}')),
            environment={},
        )
        with self.assertRaises(DiscoveryError) as context:
            invalid.search_github("valid")
        self.assertEqual(context.exception.code, "discovery_upstream_invalid_response")

    def test_github_large_result_sets_use_bounded_pages(self) -> None:
        def candidate(index: int) -> dict[str, object]:
            return {
                "name": f"skill-{index}",
                "full_name": f"example/skill-{index}",
                "description": "bounded page candidate",
                "default_branch": "main",
                "stargazers_count": 100 - index,
                "updated_at": "2026-08-28T12:34:56Z",
            }

        opener = SequenceOpener([
            FakeResponse({"items": [candidate(index) for index in range(50)]}),
            FakeResponse({"items": [candidate(index) for index in range(50, 100)]}),
        ])
        service = SkillDiscoveryService(opener=opener, environment={})

        result = service.search_github_pages("topic:skill", 100, sort_by_stars=True)

        self.assertEqual(len(result["items"]), 100)
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(
            [parse_qs(urlsplit(request.full_url).query) for request, _timeout in opener.calls],
            [
                {"q": ["topic:skill"], "per_page": ["50"], "page": ["1"], "sort": ["stars"], "order": ["desc"]},
                {"q": ["topic:skill"], "per_page": ["50"], "page": ["2"], "sort": ["stars"], "order": ["desc"]},
            ],
        )
        self.assertEqual(result["request"]["pages"], 2)

    def test_github_failures_never_echo_the_optional_token(self) -> None:
        token = "never-echo-this-token"
        opener = FakeOpener(failure=RuntimeError(f"request failed with {token}"))
        service = SkillDiscoveryService(
            opener=opener,
            environment={"SKILLSENTRA_GITHUB_TOKEN": token},
        )

        with self.assertRaises(DiscoveryError) as context:
            service.search_github("valid")

        rendered = f"{context.exception} {context.exception.details!r}"
        self.assertEqual(context.exception.code, "discovery_upstream_unavailable")
        self.assertNotIn(token, rendered)

    def test_catalog_filters_json_skills_by_query_and_preserves_repository_ref(self) -> None:
        opener = FakeOpener(
            FakeResponse(
                {
                    "skills": [
                        {
                            "name": "Data Asset Reviewer",
                            "description": "Reviews data asset evidence",
                            "repository": "https://github.com/example/data-asset-reviewer",
                            "ref": "v1.2.0",
                        },
                        {
                            "name": "Copy Editor",
                            "description": "Polishes prose",
                            "repository": "https://github.com/example/copy-editor",
                            "ref": "main",
                        },
                        {
                            "name": "Unsafe metadata",
                            "description": "data asset",
                            "repository": "http://127.0.0.1/private",
                            "ref": "main",
                        },
                        {
                            "name": "Data Asset Ref Guard",
                            "description": "data asset with an unsafe ref",
                            "repository": "https://github.com/example/ref-guard",
                            "ref": "../../secrets",
                        },
                    ]
                }
            )
        )
        service = SkillDiscoveryService(opener=opener, resolver=public_resolver)

        results = service.search_catalog(PUBLIC_CATALOG, "data asset", limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["name"], "Data Asset Reviewer")
        self.assertEqual(results[0]["repository"], "https://github.com/example/data-asset-reviewer")
        self.assertEqual(results[0]["ref"], "v1.2.0")
        self.assertEqual(results[0]["verification_status"], "unverified_candidate")
        self.assertTrue(results[0]["unverified_candidate"])
        self.assertTrue(results[0]["requires_static_scan"])
        self.assertEqual(results[1]["name"], "Data Asset Ref Guard")
        self.assertIsNone(results[1]["ref"])
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, PUBLIC_CATALOG)  # type: ignore[attr-defined]
        self.assertEqual(request.get_header("Accept"), "application/json")  # type: ignore[attr-defined]
        self.assertEqual(timeout, 5.0)

    def test_catalog_rejects_unsafe_urls_before_opening(self) -> None:
        cases = (
            "http://catalog.example.com/skills.json",
            "https://user:secret@catalog.example.com/skills.json",
            "https://catalog.example.com:8443/skills.json",
            "https://127.0.0.1/skills.json",
            "https://10.0.0.7/skills.json",
            "https://catalog.internal/skills.json",
        )
        for url in cases:
            opener = FakeOpener(FakeResponse({"skills": []}))
            service = SkillDiscoveryService(opener=opener, resolver=public_resolver)
            with self.subTest(url=url), self.assertRaises(DiscoveryError) as context:
                service.search_catalog(url, "valid")
            self.assertEqual(context.exception.code, "discovery_catalog_url_blocked")
            self.assertEqual(opener.calls, [])

        opener = FakeOpener(FakeResponse({"skills": []}))
        private_resolver = lambda *_args: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))
        ]
        service = SkillDiscoveryService(opener=opener, resolver=private_resolver)
        with self.assertRaises(DiscoveryError) as context:
            service.search_catalog(PUBLIC_CATALOG, "valid")
        self.assertEqual(context.exception.code, "discovery_catalog_url_blocked")
        self.assertEqual(opener.calls, [])

    def test_catalog_blocks_redirects_and_large_responses(self) -> None:
        redirected = SkillDiscoveryService(
            opener=FakeOpener(FakeResponse({"skills": []}, url="https://other.example.com/skills.json")),
            resolver=public_resolver,
        )
        with self.assertRaises(DiscoveryError) as context:
            redirected.search_catalog(PUBLIC_CATALOG, "valid")
        self.assertEqual(context.exception.code, "discovery_catalog_redirect_blocked")

        http_redirect = SkillDiscoveryService(
            opener=FakeOpener(
                failure=HTTPError(PUBLIC_CATALOG, 302, "Found", {"Location": "https://other.example"}, None)
            ),
            resolver=public_resolver,
        )
        with self.assertRaises(DiscoveryError) as context:
            http_redirect.search_catalog(PUBLIC_CATALOG, "valid")
        self.assertEqual(context.exception.code, "discovery_catalog_redirect_blocked")

        too_large = SkillDiscoveryService(
            opener=FakeOpener(FakeResponse(body=b"{" + b"x" * 32)),
            resolver=public_resolver,
            max_response_bytes=32,
        )
        with self.assertRaises(DiscoveryError) as context:
            too_large.search_catalog(PUBLIC_CATALOG, "valid")
        self.assertEqual(context.exception.code, "discovery_upstream_response_too_large")

    def test_default_catalog_transport_pins_first_resolution_and_ignores_proxy_environment(self) -> None:
        resolver_calls: list[tuple[object, ...]] = []

        def rebinding_resolver(*args: object) -> list[tuple[object, ...]]:
            resolver_calls.append(args)
            if len(resolver_calls) == 1:
                return PUBLIC_DNS_RESULT
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]

        response = FakeResponse(
            {
                "skills": [
                    {
                        "name": "Pinned Skill",
                        "description": "valid pinned transport",
                        "repository": "https://github.com/example/pinned-skill",
                        "ref": "main",
                    }
                ]
            }
        )
        connections: list[FakeDirectConnection] = []

        def connection_factory(
            hostname: str,
            address: _PinnedAddress,
            *,
            timeout: float,
            context: ssl.SSLContext,
        ) -> FakeDirectConnection:
            connection = FakeDirectConnection(
                hostname,
                address,
                timeout=timeout,
                context=context,
                response=response,
            )
            connections.append(connection)
            return connection

        with (
            patch.dict(
                os.environ,
                {
                    "HTTPS_PROXY": "http://127.0.0.1:8080",
                    "https_proxy": "http://10.0.0.7:8080",
                },
            ),
            patch("app.discovery.build_opener", side_effect=AssertionError("proxy opener used")),
            patch("app.discovery.ssl.create_default_context", side_effect=_fast_test_tls_context),
            patch("app.discovery._PinnedHTTPSConnection", side_effect=connection_factory),
        ):
            service = SkillDiscoveryService(resolver=rebinding_resolver, timeout=1.0)
            results = service.search_catalog(PUBLIC_CATALOG, "pinned")

        self.assertEqual([item["name"] for item in results], ["Pinned Skill"])
        self.assertEqual(len(resolver_calls), 1, "the request must not trigger a second DNS lookup")
        self.assertEqual(len(connections), 1)
        connection = connections[0]
        self.assertEqual(connection.hostname, "catalog.example.com")
        self.assertEqual(connection.address.sockaddr, ("93.184.216.34", 443))
        self.assertGreater(connection.timeout, 0)
        self.assertLessEqual(connection.timeout, 1.0)
        self.assertTrue(connection.context.check_hostname)
        self.assertEqual(connection.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(connection.requests[0][0:2], ("GET", "/skills.json"))
        self.assertEqual(connection.requests[0][2]["Connection"], "close")
        self.assertTrue(connection.closed)

    def test_default_catalog_transport_rejects_private_resolution_without_connecting(self) -> None:
        private_resolver = lambda *_args: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ]
        with patch("app.discovery._PinnedHTTPSConnection") as connection:
            service = SkillDiscoveryService(resolver=private_resolver)
            with self.assertRaises(DiscoveryError) as context:
                service.search_catalog(PUBLIC_CATALOG, "valid")

        self.assertEqual(context.exception.code, "discovery_catalog_url_blocked")
        connection.assert_not_called()

    def test_pinned_https_connection_preserves_tls_hostname_and_never_resolves_hostname(self) -> None:
        address = _PinnedAddress(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            ("93.184.216.34", 443),
        )
        raw_socket = FakeRawSocket()
        tls_context = FakeTLSContext()
        connection = _PinnedHTTPSConnection(
            "catalog.example.com",
            address,
            timeout=2.0,
            context=tls_context,  # type: ignore[arg-type]
        )

        with (
            patch("app.discovery.socket.socket", return_value=raw_socket) as socket_factory,
            patch("app.discovery.socket.getaddrinfo", side_effect=AssertionError("unexpected DNS")),
        ):
            connection.connect()

        socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self.assertEqual(raw_socket.timeout, 2.0)
        self.assertEqual(raw_socket.connected_to, ("93.184.216.34", 443))
        self.assertEqual(tls_context.calls, [(raw_socket, "catalog.example.com")])
        self.assertIs(connection.sock, tls_context.secure_socket)

    def test_default_catalog_redirect_size_and_timeout_fail_closed(self) -> None:
        def run_with_response(
            response: FakeResponse,
            *,
            failure: Exception | None = None,
            response_limit: int = 512 * 1024,
            timeout: float = 1.0,
        ) -> DiscoveryError:
            connections: list[FakeDirectConnection] = []

            def factory(
                hostname: str,
                address: _PinnedAddress,
                *,
                timeout: float,
                context: ssl.SSLContext,
            ) -> FakeDirectConnection:
                connection = FakeDirectConnection(
                    hostname,
                    address,
                    timeout=timeout,
                    context=context,
                    response=response,
                    failure=failure,
                )
                connections.append(connection)
                return connection

            with (
                patch("app.discovery.ssl.create_default_context", side_effect=_fast_test_tls_context),
                patch("app.discovery._PinnedHTTPSConnection", side_effect=factory),
            ):
                service = SkillDiscoveryService(
                    resolver=public_resolver,
                    timeout=timeout,
                    max_response_bytes=response_limit,
                )
                with self.assertRaises(DiscoveryError) as context:
                    service.search_catalog(PUBLIC_CATALOG, "valid")
            self.assertTrue(connections)
            self.assertTrue(all(connection.closed for connection in connections))
            self.assertTrue(all(0 < connection.timeout <= timeout for connection in connections))
            return context.exception

        redirect_error = run_with_response(
            FakeResponse(None, body=b"", status=302, headers={"Location": "https://127.0.0.1/"})
        )
        self.assertEqual(redirect_error.code, "discovery_catalog_redirect_blocked")

        size_error = run_with_response(
            FakeResponse(None, body=b"{" + b"x" * 32),
            response_limit=32,
        )
        self.assertEqual(size_error.code, "discovery_upstream_response_too_large")

        timeout_error = run_with_response(
            FakeResponse({"skills": []}),
            failure=socket.timeout("bounded timeout"),
            timeout=1.0,
        )
        self.assertEqual(timeout_error.code, "discovery_upstream_timeout")


if __name__ == "__main__":
    unittest.main()
