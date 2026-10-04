from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


MAX_RESPONSE_BYTES = 1_048_576
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class BridgeClientError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class BridgeClientConfig:
    base_url: str
    access_token: str = ""
    timeout_seconds: float = 10.0

    def normalized_base_url(self) -> str:
        raw = self.base_url.strip().rstrip("/")
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BridgeClientError("invalid_base_url", "Bridge 服务地址必须是有效的 HTTP(S) 地址。")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BridgeClientError("unsafe_base_url", "Bridge 服务地址不能包含凭据、查询参数或片段。")
        if parsed.path not in {"", "/"}:
            raise BridgeClientError("unsafe_base_url", "Bridge 服务地址不能包含额外路径。")
        if parsed.scheme == "http" and parsed.hostname.lower() not in LOOPBACK_HOSTS:
            raise BridgeClientError("https_required", "非本机 Bridge 连接必须使用 HTTPS。")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise BridgeClientError("invalid_timeout", "Bridge 超时必须大于 0 且不超过 60 秒。")
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


class BridgeClient:
    """Metadata-only outbound client for the first Local Bridge increment."""

    def __init__(self, config: BridgeClientConfig, opener: Any | None = None):
        self.config = config
        self.base_url = config.normalized_base_url()
        self.opener = opener or urllib.request.build_opener(_NoRedirect())

    def register(
        self,
        display_name: str,
        platform: str,
        execution_scope: str = "metadata-only",
        capabilities: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/control/bridges",
            {
                "display_name": display_name,
                "platform": platform,
                "execution_scope": execution_scope,
                "capabilities": capabilities or {},
            },
        )

    def heartbeat(
        self,
        bridge_id: str,
        status: str = "online",
        observed_state: dict[str, Any] | None = None,
        capabilities: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "observed_state": observed_state or {},
        }
        if capabilities is not None:
            payload["capabilities"] = capabilities
        safe_bridge_id = urllib.parse.quote(bridge_id.strip(), safe="")
        if not safe_bridge_id:
            raise BridgeClientError("bridge_id_required", "Bridge ID 不能为空。")
        return self._request(
            "POST", f"/api/v1/control/bridges/{safe_bridge_id}/heartbeat", payload
        )

    def overview(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/control/edge/overview")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SkillSentra-Bridge/0.1",
        }
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, method=method, headers=headers
        )
        try:
            with self.opener.open(request, timeout=self.config.timeout_seconds) as response:
                raw = self._bounded_read(response)
        except urllib.error.HTTPError as exc:
            raise BridgeClientError(
                "server_rejected_request",
                f"SkillSentra 服务拒绝了请求（HTTP {exc.code}）。",
                exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise BridgeClientError("service_unreachable", "无法连接 SkillSentra 服务。") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeClientError("invalid_server_response", "SkillSentra 服务返回了无效 JSON。") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), (dict, list)):
            raise BridgeClientError("invalid_server_response", "SkillSentra 服务响应缺少 data。")
        return decoded["data"]

    @staticmethod
    def _bounded_read(response: Any) -> bytes:
        header = response.headers.get("Content-Length", "")
        if header:
            try:
                if int(header) > MAX_RESPONSE_BYTES:
                    raise BridgeClientError("response_too_large", "SkillSentra 服务响应超过 1 MB。")
            except ValueError as exc:
                raise BridgeClientError("invalid_server_response", "SkillSentra 服务返回了无效长度。") from exc
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise BridgeClientError("response_too_large", "SkillSentra 服务响应超过 1 MB。")
        return raw


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeClientError("invalid_json_argument", "命令参数必须是 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise BridgeClientError("invalid_json_argument", "命令参数必须是 JSON 对象。")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SkillSentra metadata-only Local Bridge")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SKILLSENTRA_BASE_URL", "http://127.0.0.1:8766"),
        help="SkillSentra server URL; non-loopback addresses must use HTTPS",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser("register", help="Register one outbound-only Bridge")
    register.add_argument("--name", required=True)
    register.add_argument("--platform", required=True, choices=("windows", "macos", "linux", "other"))
    register.add_argument(
        "--execution-scope",
        default="metadata-only",
        choices=("metadata-only", "local-files", "sandbox"),
    )
    register.add_argument("--capabilities", default="{}", help="JSON capability-level object")

    heartbeat = subparsers.add_parser("heartbeat", help="Report observed Bridge state")
    heartbeat.add_argument("--bridge-id", required=True)
    heartbeat.add_argument("--status", default="online", choices=("online", "restricted"))
    heartbeat.add_argument("--state", default="{}", help="JSON observed-state object; never include credentials")
    heartbeat.add_argument("--capabilities", default="", help="Optional JSON capability-level object")

    subparsers.add_parser("overview", help="Read edge connection counts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        client = BridgeClient(
            BridgeClientConfig(
                base_url=args.base_url,
                access_token=os.environ.get("SKILLSENTRA_AUTH_TOKEN", "").strip(),
            )
        )
        if args.command == "register":
            result = client.register(
                args.name,
                args.platform,
                args.execution_scope,
                _json_object(args.capabilities),
            )
        elif args.command == "heartbeat":
            capabilities = _json_object(args.capabilities) if args.capabilities else None
            result = client.heartbeat(
                args.bridge_id,
                args.status,
                _json_object(args.state),
                capabilities,
            )
        else:
            result = client.overview()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except BridgeClientError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
