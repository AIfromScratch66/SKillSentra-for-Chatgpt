from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from .database import json_dumps
from .errors import AppError
from .repository import Repository


PROVIDERS = [
    {
        "id": "mock",
        "name": "内置 Mock",
        "description": "零密钥、确定性输出，用于开发与联调",
        "credential_mode": "none",
    },
    {
        "id": "openai",
        "name": "OpenAI API",
        "description": "服务端通过 Responses API 调用",
        "credential_mode": "server_env",
        "default_base_url": "https://api.openai.com/v1",
        "default_credential_env": "SKILLSENTRA_OPENAI_API_KEY",
    },
    {
        "id": "openai-compatible",
        "name": "OpenAI-compatible",
        "description": "企业网关或兼容 Responses API 的服务",
        "credential_mode": "server_env",
        "default_credential_env": "SKILLSENTRA_CUSTOM_API_KEY",
    },
]


TEMPLATE_RESULTS = [
    "已形成目标契约草案，并识别出“负触发样例”需要补充。",
    "建议扩展 professional-report-writing，并新增逐条来源台账。",
    "已形成评价样例建议；硬门与加权评分保持分离。",
    "已生成 5 项蓝图补丁，其中外部写入能力保持关闭。",
    "候选结构已生成；AI 提示 1 项引用优化，规则硬门仍需单独运行。",
    "已提出任务增量验证重点；L5 动态安全因未授权保持 Unknown。",
    "建议补充负触发与来源缺失停止条件，共影响 4 个回归样例。",
    "交付说明草案已生成；版本摘要、目标和权限仍需人工确认。",
    "已生成更新候选；没有自动覆盖已交付版本。",
]

EXISTING_RESULTS = [
    "已生成基线摘要；来源、摘要、许可和依赖仍由确定性流程锁定。",
    "已发现触发范围偏宽和上下文加载偏重两项主要问题。",
    "已形成 3 条修改假设，等待逐项接受、拒绝或暂缓。",
    "候选 GraphDiff 已生成；原 Skill 未被覆盖，相关证据已标记失效。",
    "候选版触发准确性提升；未发现关键退化，L5 仍保持未授权。",
    "升级说明草案已准备；精确版本、交付目标和权限需人工确认。",
    "发现 1 项可安全合并变更与 1 项需要暂缓的触发冲突。",
]


@dataclass
class AdapterResult:
    output: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    provider_request_id: str = ""
    usage_receipt: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(Protocol):
    def test(self) -> list[dict[str, Any]]: ...

    def run(self, model: str, instructions: str, input_text: str) -> AdapterResult: ...


class MockAdapter:
    MODELS = [
        {"id": "mock-balanced", "label": "智能平衡", "capabilities": ["text", "structured-output"]},
        {"id": "mock-evaluator", "label": "独立评价", "capabilities": ["text", "grading"]},
        {"id": "mock-safety", "label": "安全复核", "capabilities": ["text", "risk-review"]},
    ]

    def __init__(self, route: str = "template", step_index: int = 0, role: str = "creator", pricebook: "ModelPriceBook | None" = None):
        self.route = route
        self.step_index = step_index
        self.role = role
        self.pricebook = pricebook or ModelPriceBook()

    def test(self) -> list[dict[str, Any]]:
        return self.MODELS

    def run(self, model: str, instructions: str, input_text: str) -> AdapterResult:
        del instructions, input_text
        results = TEMPLATE_RESULTS if self.route == "template" else EXISTING_RESULTS
        index = min(max(self.step_index, 0), len(results) - 1)
        role_prefix = {
            "creator": "创建建议",
            "evaluator": "独立评价",
            "safety": "安全复核",
        }.get(self.role, "AI 建议")
        return AdapterResult(
            output={
                "summary": f"{role_prefix}：{results[index]}",
                "decision": "pending",
                "confidence": "demo",
                "model": model,
                "role": self.role,
                "warnings": ["Mock 输出只用于开发联调，不代表真实模型评价。"],
            },
            input_tokens=128,
            output_tokens=64,
            estimated_cost=self.pricebook.cost(model, 128, 64),
            provider_request_id=f"mock-{int(time.time() * 1000)}",
            usage_receipt={
                "usage_status": "simulated", "usage_source": "mock_fixture",
                "input_tokens": 128, "output_tokens": 64, "total_tokens": 192,
                "cost_status": "simulated", "provider_response_status": "simulated",
                "provider_model": model,
            },
        )


class ModelPriceBook:
    def __init__(self) -> None:
        self.version = os.environ.get("SKILLSENTRA_PRICEBOOK_VERSION", "local-pricebook-v1")
        self.currency = os.environ.get("SKILLSENTRA_PRICEBOOK_CURRENCY", "USD")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise AppError("invalid_pricebook_currency", "模型价格表币种必须是三位大写代码。", 500)
        raw = os.environ.get("SKILLSENTRA_MODEL_PRICES_JSON", "")
        try:
            configured = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise AppError("invalid_pricebook", "模型价格表环境变量不是有效 JSON。", 500) from exc
        self.prices: dict[str, dict[str, float]] = {
            "mock-": {"input_per_million": 5.2, "output_per_million": 5.2},
            "*": {"input_per_million": 2.0, "output_per_million": 8.0},
        }
        if isinstance(configured, dict):
            for prefix, values in configured.items():
                if not isinstance(values, dict):
                    continue
                try:
                    input_rate = max(0.0, float(values["input_per_million"]))
                    output_rate = max(0.0, float(values["output_per_million"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if not math.isfinite(input_rate) or not math.isfinite(output_rate):
                    continue
                self.prices[str(prefix)] = {"input_per_million": input_rate, "output_per_million": output_rate}

    def rates(self, model: str) -> dict[str, float]:
        matches = [prefix for prefix in self.prices if prefix != "*" and model.startswith(prefix)]
        return self.prices[max(matches, key=len)] if matches else self.prices["*"]

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        rates = self.rates(model)
        return round(
            input_tokens * rates["input_per_million"] / 1_000_000
            + output_tokens * rates["output_per_million"] / 1_000_000,
            8,
        )

    def reserve(self, model: str, input_tokens: int = 4000, output_tokens: int = 1200) -> float:
        return self.cost(model, input_tokens, output_tokens)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class OpenAIResponsesAdapter:
    def __init__(self, base_url: str, credential_env: str, pricebook: ModelPriceBook | None = None, timeout: float = 30):
        self.base_url = validate_base_url(base_url)
        self.credential_env = validate_credential_env(credential_env)
        self.pricebook = pricebook or ModelPriceBook()
        self.timeout = timeout

    def _api_key(self) -> str:
        api_key = os.environ.get(self.credential_env, "")
        if not api_key:
            raise AppError(
                "credential_unavailable",
                f"服务端环境变量 {self.credential_env} 尚未配置。",
                409,
            )
        return api_key

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        ensure_safe_destination(self.base_url)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
                "User-Agent": "SkillSentra/0.1",
            },
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        last_error: Exception | None = None
        # Retrying an ambiguous generation POST can create a second billable
        # response with no receipt for the first. Only read-only probes retry.
        attempts = 2 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise AppError("provider_response_too_large", "模型服务响应超过 2 MB。", 502)
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise AppError("provider_invalid_json", "模型服务响应必须是 JSON 对象。", 502)
                    payload["_transport_request_id"] = str(response.headers.get("x-request-id", ""))
                    return payload
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code < 500 or attempt == attempts - 1:
                    raise AppError(
                        "provider_http_error",
                        f"模型服务返回 HTTP {exc.code}。",
                        502,
                        {"provider_status": exc.code, "ai_receipt": {
                            "usage_status": "unknown", "usage_source": "unavailable",
                            "cost_status": "unknown", "provider_http_status": exc.code,
                            "transport_request_id": str(exc.headers.get("x-request-id", "")) if exc.headers else "",
                        }},
                    ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == attempts - 1:
                    raise AppError("provider_unreachable", "无法连接模型服务。", 502) from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise AppError("provider_invalid_json", "模型服务返回了无法解析的 JSON。", 502) from exc
            time.sleep(0.2 * (attempt + 1))
        raise AppError("provider_unreachable", "无法连接模型服务。", 502) from last_error

    def test(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/models")
        models = payload.get("data", [])
        return [
            {"id": str(item.get("id", "")), "label": str(item.get("id", "")), "capabilities": ["text"]}
            for item in models
            if isinstance(item, dict) and item.get("id")
        ][:200]

    def run(self, model: str, instructions: str, input_text: str) -> AdapterResult:
        payload = self._request(
            "POST",
            "/responses",
            {
                "model": model,
                "instructions": instructions,
                "input": input_text,
                "max_output_tokens": 1200,
                "store": False,
            },
        )
        receipt = response_receipt(payload, model, self.pricebook)
        if payload.get("status") != "completed":
            status = str(payload.get("status") or "unknown")
            raise AppError("provider_response_not_completed", "模型响应未完成，未作为有效结果应用。", 502,
                           {"response_status": status, "ai_receipt": receipt})
        if payload.get("error"):
            raise AppError("provider_response_failed", "模型响应包含错误，未作为有效结果应用。", 502,
                           {"ai_receipt": receipt})
        if not receipt["provider_request_id"] or not receipt["provider_model"]:
            raise AppError("provider_missing_receipt", "模型响应缺少响应编号或模型信息。", 502,
                           {"ai_receipt": receipt})
        try:
            text = extract_output_text(payload)
        except AppError as exc:
            exc.details["ai_receipt"] = receipt
            raise
        return AdapterResult(
            output={
                "summary": text,
                "decision": "pending",
                "confidence": "model",
                "model": receipt["provider_model"],
                "warnings": [] if receipt["usage_status"] == "reported" else ["供应商未返回完整有效的 Token 用量，耗用量待核对。"],
            },
            input_tokens=receipt["input_tokens"],
            output_tokens=receipt["output_tokens"],
            estimated_cost=receipt["estimated_cost"],
            provider_request_id=str(payload.get("id", "")),
            usage_receipt=receipt,
        )


def validate_credential_env(value: str) -> str:
    if value == "OPENAI_API_KEY":
        return value
    if not re.fullmatch(r"SKILLSENTRA_[A-Z0-9_]{3,80}", value or ""):
        raise AppError(
            "invalid_credential_reference",
            "凭据只能引用 OPENAI_API_KEY 或以 SKILLSENTRA_ 开头的服务端环境变量。",
        )
    return value


def validate_base_url(value: str) -> str:
    parsed = urlparse((value or "").rstrip("/"))
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AppError("invalid_base_url", "模型服务地址格式不正确。")
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise AppError("insecure_base_url", "模型服务必须使用 HTTPS；仅本机回环地址允许 HTTP。")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global and not address.is_loopback:
        raise AppError("blocked_provider_address", "模型服务地址不能指向私网、链路本地或保留地址。")
    return value.rstrip("/")


def ensure_safe_destination(value: str) -> None:
    parsed = urlparse(value)
    hostname = parsed.hostname or ""
    if hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    if parsed.scheme == "https" and hostname == "api.openai.com":
        return
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise AppError("provider_dns_failed", "无法解析模型服务地址。", 502) from exc
    if not addresses:
        raise AppError("provider_dns_failed", "模型服务地址没有可用解析结果。", 502)
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise AppError("blocked_provider_address", "模型服务解析到了私网、链路本地或保留地址。", 403)


def extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("status") not in {None, "completed"}:
            raise AppError("provider_response_not_completed", "模型消息未完成。", 502)
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    text = "\n".join(chunks).strip()
    if not text:
        raise AppError("provider_empty_output", "模型服务没有返回可用文本。", 502)
    return text


def parse_token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2_000_000_000:
        raise AppError("provider_invalid_usage", "模型服务返回了无效的 token 用量。", 502)
    return value


def response_receipt(payload: dict[str, Any], model: str, pricebook: ModelPriceBook) -> dict[str, Any]:
    """Keep supplier usage distinct from missing, invalid and simulated counts."""
    receipt: dict[str, Any] = {
        "usage_status": "unknown", "usage_source": "unavailable",
        "input_tokens": None, "output_tokens": None, "total_tokens": None,
        "cached_input_tokens": None, "reasoning_output_tokens": None,
        "estimated_cost": None, "cost_status": "unknown",
        "provider_request_id": str(payload.get("id") or ""),
        "transport_request_id": str(payload.get("_transport_request_id") or ""),
        "provider_response_status": str(payload.get("status") or "unknown"),
        "provider_model": str(payload.get("model") or ""),
    }
    provider_error = payload.get("error") or payload.get("incomplete_details")
    if isinstance(provider_error, dict):
        receipt["provider_error_code"] = str(provider_error.get("code") or provider_error.get("reason") or "")[:100]
    usage = payload.get("usage")
    if usage is None:
        return receipt
    if not isinstance(usage, dict):
        receipt["usage_status"] = "invalid"
        return receipt
    try:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            if usage.get(name) is not None:
                receipt[name] = parse_token_count(usage[name])
        for group, field_name, name in (("input_tokens_details", "cached_tokens", "cached_input_tokens"),
                                       ("output_tokens_details", "reasoning_tokens", "reasoning_output_tokens")):
            details = usage.get(group)
            if details is not None:
                if not isinstance(details, dict):
                    raise AppError("provider_invalid_usage", "模型用量明细无效。", 502)
                if details.get(field_name) is not None:
                    receipt[name] = parse_token_count(details[field_name])
        inp, out, total = (receipt[name] for name in ("input_tokens", "output_tokens", "total_tokens"))
        if inp is not None and out is not None and total is not None and total != inp + out:
            raise AppError("provider_invalid_usage", "模型用量合计不一致。", 502)
        for part, whole in ((receipt["cached_input_tokens"], inp), (receipt["reasoning_output_tokens"], out)):
            if part is not None and whole is not None and part > whole:
                raise AppError("provider_invalid_usage", "模型用量明细超过总量。", 502)
    except AppError:
        for name in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_output_tokens"):
            receipt[name] = None
        receipt["usage_status"] = "invalid"
        return receipt
    receipt["usage_source"] = "provider_response"
    receipt["usage_status"] = "reported" if all(receipt[name] is not None for name in ("input_tokens", "output_tokens", "total_tokens")) else "partial"
    if receipt["input_tokens"] is not None and receipt["output_tokens"] is not None:
        receipt["estimated_cost"] = pricebook.cost(receipt["provider_model"] or model, receipt["input_tokens"], receipt["output_tokens"])
        receipt["cost_status"] = "estimated_local_pricebook"
    return receipt


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items() if key.lower() not in {"api_key", "apikey", "secret", "token", "password"}}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\b(?:sk|key)-[A-Za-z0-9_-]{12,}\b", "[REDACTED_SECRET]", value)
        value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", value)
    return value


def build_manifest(context: dict[str, Any], redacted: bool) -> dict[str, Any]:
    encoded = json_dumps(context).encode("utf-8")
    return {
        "fields": sorted(context.keys()),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "redacted": redacted,
        "raw_persisted": False,
    }


class AIGateway:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.pricebook = ModelPriceBook()

    def ensure_mock_connection(self, tenant_id: str = "ten_demo") -> dict[str, Any]:
        existing = next(
            (item for item in self.repository.list_connections(tenant_id) if item["provider"] == "mock"),
            None,
        )
        if existing:
            return existing
        connection = self.repository.create_connection(
            "mock",
            "内置开发模型",
            "http://127.0.0.1/mock",
            "SKILLSENTRA_MOCK_KEY",
            tenant_id,
        )
        return self.repository.update_connection_test(
            connection["id"],
            "healthy",
            MockAdapter.MODELS,
            tenant_id=tenant_id,
        )

    def create_connection(self, payload: dict[str, Any], tenant_id: str = "ten_demo") -> dict[str, Any]:
        provider = str(payload.get("provider", "mock"))
        if provider not in {item["id"] for item in PROVIDERS}:
            raise AppError("unsupported_provider", "暂不支持该模型供应商。")
        display_name = str(payload.get("display_name") or "模型连接").strip()[:100]
        if provider == "mock":
            base_url = "http://127.0.0.1/mock"
            credential_env = "SKILLSENTRA_MOCK_KEY"
        else:
            base_url = str(payload.get("base_url") or ("https://api.openai.com/v1" if provider == "openai" else ""))
            credential_env = str(payload.get("credential_env") or ("SKILLSENTRA_OPENAI_API_KEY" if provider == "openai" else "SKILLSENTRA_CUSTOM_API_KEY"))
            validate_base_url(base_url)
            validate_credential_env(credential_env)
            if provider == "openai" and base_url.rstrip("/") != "https://api.openai.com/v1":
                raise AppError("provider_identity_mismatch", "自定义模型地址请选择 OpenAI-compatible，OpenAI API 连接仅使用官方地址。")
        return self.repository.create_connection(provider, display_name, base_url, credential_env, tenant_id)

    def test_connection(self, connection_id: str, tenant_id: str = "ten_demo") -> dict[str, Any]:
        connection = self.repository.get_connection_internal(connection_id, tenant_id)
        if connection is None:
            raise AppError("connection_not_found", "模型连接不存在。", 404)
        if connection.get("status") not in {"configured", "healthy", "error"}:
            raise AppError("connection_test_in_progress", "模型连接正在测试，请稍后重试。", 409)
        try:
            models = self._adapter(connection).test()
            if not models:
                raise AppError("provider_no_models", "模型服务没有返回可用模型。", 502)
            return self.repository.update_connection_test(
                connection_id,
                "healthy",
                models,
                tenant_id=tenant_id,
            )
        except AppError as exc:
            self.repository.update_connection_test(
                connection_id,
                "error",
                [],
                exc.message,
                tenant_id,
            )
            raise

    def run(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project is None:
            raise AppError("project_not_found", "项目不存在。", 404)
        tenant_id = str(project["tenant_id"])
        policy = self.repository.get_policy(project_id)
        if not policy["enabled"] or policy["mode"] == "manual":
            raise AppError("ai_disabled", "当前项目处于仅人工模式。", 409)
        connection_id = str(payload.get("connection_id") or policy.get("connection_id") or "")
        if not connection_id:
            raise AppError("ai_connection_required", "请先选择并测试 AI 连接。", 409)
        connection = self.repository.get_connection_internal(connection_id, tenant_id)
        if connection is None:
            raise AppError("connection_not_found", "模型连接不存在。", 404)
        if payload.get("require_real") and connection["provider"] == "mock":
            raise AppError("real_ai_required", "此操作需要真实模型连接，请在 AI 协作设置中选择 OpenAI API 连接。", 409)
        if connection.get("status") != "healthy":
            raise AppError("connection_not_healthy", "AI 连接尚未通过连接测试。", 409)
        configured_connection = str(policy.get("connection_id") or "")
        if configured_connection and connection_id != configured_connection:
            raise AppError("connection_policy_mismatch", "本次调用连接与项目策略不一致。", 409)
        route = str(payload.get("route", "template"))
        try:
            step_index = int(payload.get("step_index", 0))
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_run_scope", "AI 调用的步骤编号不正确。") from exc
        role = str(payload.get("role", "creator"))
        maximum = 8 if route == "template" else 6
        if route not in {"template", "existing"} or role not in {"creator", "evaluator", "safety"} or step_index < 0 or step_index > maximum:
            raise AppError("invalid_run_scope", "AI 调用的路线、步骤或角色不正确。")
        model_key = {"creator": "creator_model", "evaluator": "evaluator_model", "safety": "safety_model"}[role]
        model = str(payload.get("model") or policy[model_key])
        available_models = {str(item.get("id")) for item in connection.get("model_catalog") or [] if item.get("id")}
        if model not in available_models:
            raise AppError("model_not_available", "所选模型不在最近同步的模型目录中。", 409, {"model": model})
        context = payload.get("context") or {}
        if not isinstance(context, dict):
            raise AppError("invalid_context", "AI 上下文必须是对象。")
        if context.get("files") and not policy["external_files_enabled"]:
            raise AppError("external_files_disabled", "当前策略不允许发送外部文件。", 409)
        if policy["redact_enabled"]:
            context = redact_value(context)
        if len(json_dumps(context).encode("utf-8")) > 100_000:
            raise AppError("context_too_large", "本步 AI 上下文超过 100 KB 限制。", 413)
        task = str(payload.get("task") or "")
        if policy["redact_enabled"]:
            task = redact_value(task)
        instructions = self._instructions(role)
        request_input = json_dumps({"task": task, "context": context})
        if len(request_input.encode("utf-8")) > 100_000:
            raise AppError("context_too_large", "本步 AI 输入超过 100 KB 限制。", 413)
        reserve_cost = self.pricebook.reserve(model, max(4000, len(request_input.encode("utf-8")) + len(instructions.encode("utf-8")) + 100))
        spent = self.repository.daily_spend(project_id)
        if spent + reserve_cost > float(policy["daily_budget"]):
            raise AppError("daily_budget_exceeded", "项目每日 AI 预算不足。", 409, {"spent": spent, "budget": policy["daily_budget"]})

        manifest = build_manifest({"task": task, "context": context, "instructions": instructions}, policy["redact_enabled"])
        orchestration_id = str(payload.get("orchestration_id") or "")
        try:
            attempt = max(1, min(int(payload.get("attempt", 1)), 3))
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_run_attempt", "AI 调用次数参数不正确。") from exc
        run = self.repository.start_ai_run(
            project_id,
            connection_id,
            route,
            step_index,
            role,
            model,
            manifest,
            orchestration_id=orchestration_id,
            currency=self.pricebook.currency,
            pricebook_version=self.pricebook.version,
            attempt=attempt,
            reserved_cost=reserve_cost,
            provider=connection["provider"],
            budget_limit=float(policy["daily_budget"]),
        )
        started = time.perf_counter()
        try:
            adapter = self._adapter(connection, route, step_index, role)
            result = adapter.run(
                model,
                instructions,
                request_input,
            )
            if payload.get("fallback_used"):
                result.output.setdefault("warnings", []).append("主模型失败，已按项目策略切换备用模型。")
            result.usage_receipt["output_sha256"] = hashlib.sha256(json_dumps(result.output).encode("utf-8")).hexdigest()
            duration_ms = int((time.perf_counter() - started) * 1000)
            completed = self.repository.finish_ai_run(
                run["id"],
                {
                    "output": result.output,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "estimated_cost": result.estimated_cost,
                    "duration_ms": duration_ms,
                    "provider_request_id": result.provider_request_id,
                    "usage_receipt": result.usage_receipt,
                    "fallback_used": bool(payload.get("fallback_used")),
                },
            )
            return completed
        except AppError as exc:
            receipt = dict(exc.details.pop("ai_receipt", {}) or {})
            receipt.setdefault("usage_status", "unknown")
            receipt.setdefault("usage_source", "unavailable")
            receipt.setdefault("cost_status", "unknown")
            receipt["error_code"] = exc.code
            if exc.code in {"credential_unavailable", "provider_dns_failed", "blocked_provider_address"}:
                receipt["cost_status"] = "not_sent"
            self.repository.finish_ai_run(run["id"], {
                "duration_ms": int((time.perf_counter() - started) * 1000), "usage_receipt": receipt,
                "provider_request_id": receipt.get("provider_request_id", ""),
                "fallback_used": bool(payload.get("fallback_used")),
            }, exc.message)
            exc.details["run_id"] = run["id"]
            raise
        except Exception as exc:
            self.repository.finish_ai_run(run["id"], {
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "usage_receipt": {"usage_status": "unknown", "usage_source": "unavailable", "cost_status": "unknown", "error_code": "ai_run_failed"},
                "fallback_used": bool(payload.get("fallback_used")),
            }, "模型调用发生内部错误。")
            raise AppError("ai_run_failed", "模型调用发生内部错误。", 500, {"run_id": run["id"]}) from exc

    def orchestrate(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        policy = self.repository.get_policy(project_id)
        requested = payload.get("roles") or ["creator", "evaluator", "safety"]
        if not isinstance(requested, list) or not requested:
            raise AppError("invalid_orchestration_roles", "AI 编排角色必须是非空数组。")
        roles = []
        for role in requested:
            role = str(role)
            if role not in {"creator", "evaluator", "safety"}:
                raise AppError("invalid_orchestration_roles", "AI 编排包含未知角色。")
            if role not in roles:
                roles.append(role)
        orchestration_id = make_orchestration_id()
        runs: list[dict[str, Any]] = []
        shared_context = payload.get("context") or {}
        if not isinstance(shared_context, dict):
            raise AppError("invalid_context", "AI 上下文必须是对象。")
        previous: list[dict[str, Any]] = []
        for role in roles:
            role_payload = dict(payload)
            role_payload["role"] = role
            role_payload["orchestration_id"] = orchestration_id
            role_payload["context"] = {**shared_context, "prior_role_outputs": previous}
            try:
                run = self.run(project_id, role_payload)
                run["fallback_used"] = False
            except AppError as exc:
                if exc.code not in {"provider_http_error", "provider_unreachable", "provider_empty_output", "provider_invalid_json", "provider_response_too_large", "ai_run_failed"}:
                    raise
                model_key = {"creator": "creator_model", "evaluator": "evaluator_model", "safety": "safety_model"}[role]
                failed_model = str(role_payload.get("model") or policy.get(model_key) or "")
                fallback_model = self._fallback_model(policy, role, failed_model)
                if not policy.get("fallback_enabled") or not fallback_model:
                    raise
                role_payload["model"] = fallback_model
                role_payload["attempt"] = 2
                role_payload["fallback_used"] = True
                run = self.run(project_id, role_payload)
                run["fallback_used"] = True
            runs.append(run)
            previous.append({"role": role, "summary": (run.get("output") or {}).get("summary", "")})
        attempts = self.repository.list_orchestration_runs(project_id, orchestration_id)
        return {
            "id": orchestration_id,
            "status": "completed",
            "runs": runs,
            "attempts": attempts,
            "total_cost": round(sum(float(item.get("estimated_cost") or 0) for item in attempts), 8),
            "total_tokens": sum(int(item.get("total_tokens") if item.get("total_tokens") is not None else (item.get("input_tokens") or 0) + (item.get("output_tokens") or 0)) for item in attempts),
            "usage_complete": all(item.get("usage_status") in {"reported", "simulated"} for item in attempts),
            "unknown_usage_run_count": sum(item.get("usage_status") not in {"reported", "simulated"} for item in attempts),
            "currency": self.pricebook.currency,
            "pricebook_version": self.pricebook.version,
        }

    def _adapter(self, connection: dict[str, Any], route: str = "template", step_index: int = 0, role: str = "creator") -> ProviderAdapter:
        if connection["provider"] == "mock":
            return MockAdapter(route, step_index, role, self.pricebook)
        if connection["provider"] == "openai" and connection["base_url"].rstrip("/") != "https://api.openai.com/v1":
            raise AppError("provider_identity_mismatch", "OpenAI API 连接地址不是官方地址，请改用兼容接口类型。", 409)
        return OpenAIResponsesAdapter(connection["base_url"], connection["credential_env"], self.pricebook)

    @staticmethod
    def _instructions(role: str) -> str:
        return {
            "creator": "你是 SkillSentra 的 Skill 创建模型。只提出结构化候选，不声称通过安全、许可或交付硬门。",
            "evaluator": "你是独立评价模型。审查创建结果，按目标增量、触发、稳健和成本给出证据化问题，不自我批准。",
            "safety": "你是安全复核模型。识别 Secret、提示注入、越权、外部写入和危险脚本风险；确定性规则才是硬门事实来源。",
        }[role]

    @staticmethod
    def _fallback_model(policy: dict[str, Any], role: str, failed_model: str) -> str:
        candidates = [
            str(policy.get("creator_model") or ""),
            str(policy.get("evaluator_model") or ""),
            str(policy.get("safety_model") or ""),
        ]
        return next((model for model in candidates if model and model != failed_model), "")


def make_orchestration_id() -> str:
    return f"orch_{hashlib.sha256(f'{time.time_ns()}'.encode()).hexdigest()[:16]}"
