from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from .config import AppConfig


def _check(check_id: str, label: str, status: str, detail: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "required": required,
    }


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def evaluate_production_readiness(
    config: AppConfig,
    *,
    email_delivery_configured: bool,
    database_backend: str = "sqlite",
) -> dict[str, Any]:
    """Return a secret-free, fail-closed production gate snapshot.

    This is a decision aid for an authorized operator. It never changes release
    state and it does not treat local tests or an internal score as evidence of
    a production go decision.
    """

    checks: list[dict[str, Any]] = []
    checks.append(_check(
        "environment",
        "Production profile",
        "pass" if config.environment == "production" else "block",
        "SKILLSENTRA_ENV=production is required." if config.environment != "production" else "Production profile selected.",
    ))
    checks.append(_check(
        "auth_mode",
        "Hybrid authentication",
        "pass" if config.auth_mode == "hybrid" else "block",
        "Use accounts plus operator tokens behind the deployment boundary." if config.auth_mode != "hybrid" else "Hybrid authentication is selected.",
    ))

    origin = urlparse(config.public_origin)
    origins_ok = bool(config.trusted_origins) and all(
        urlparse(value).scheme == "https" and bool(urlparse(value).netloc)
        for value in config.trusted_origins
    )
    checks.append(_check(
        "tls_origins",
        "TLS origins",
        "pass" if origin.scheme == "https" and bool(origin.netloc) and origins_ok else "block",
        "Public origin and every trusted origin must be an HTTPS URL." if not (origin.scheme == "https" and bool(origin.netloc) and origins_ok) else "Public and trusted origins are HTTPS.",
    ))
    checks.append(_check(
        "tls_termination",
        "TLS termination confirmation",
        "pass" if config.tls_termination_confirmed else "block",
        "Confirm the reverse proxy terminates TLS and forwards the secure scheme." if not config.tls_termination_confirmed else "TLS termination was explicitly confirmed in deployment configuration.",
    ))

    token_status = "pass"
    token_detail = "Operator token registry is configured without exposing token values."
    try:
        registry = json.loads(config.auth_tokens_json or "{}")
        if not isinstance(registry, dict) or not registry:
            raise ValueError("empty registry")
        for token in registry:
            if not isinstance(token, str) or len(token) < 24 or "replace-with" in token.lower() or "example" in token.lower():
                raise ValueError("weak or example token")
    except (TypeError, ValueError, json.JSONDecodeError):
        token_status = "block"
        token_detail = "Configure high-entropy operator tokens; example or missing values are not accepted."
    checks.append(_check("operator_tokens", "Operator tokens", token_status, token_detail))

    pepper = os.environ.get("SKILLSENTRA_PASSWORD_PEPPER", "")
    pepper_ok = len(pepper) >= 32 and "replace-with" not in pepper.lower() and "example" not in pepper.lower()
    checks.append(_check(
        "password_pepper",
        "Password pepper",
        "pass" if pepper_ok else "block",
        "Password pepper is not configured with sufficient entropy." if not pepper_ok else "Password pepper is configured without returning its value.",
    ))

    email_ok = bool(config.require_email_verification and email_delivery_configured)
    checks.append(_check(
        "account_email",
        "Verified account email",
        "pass" if email_ok else "block",
        "Production registration requires email verification and a configured STARTTLS delivery service." if not email_ok else "Email verification and delivery are configured.",
    ))

    google_configured = bool(config.google_client_id and config.google_client_secret and config.google_redirect_uri)
    google_verified = _is_true(os.environ.get("SKILLSENTRA_GOOGLE_SSO_VERIFIED"))
    checks.append(_check(
        "google_sso",
        "Google SSO evidence",
        "pass" if google_configured and google_verified else "review",
        "Run and record a real Google authorization-code callback test." if not (google_configured and google_verified) else "Google SSO configuration and real-host verification flag are present.",
        required=False,
    ))

    chatgpt_configured = bool(
        config.chatgpt_enabled
        and config.chatgpt_client_id
        and config.chatgpt_client_secret
        and config.chatgpt_authorization_url
        and config.chatgpt_token_url
        and config.chatgpt_userinfo_url
        and config.chatgpt_redirect_uri
    )
    chatgpt_urls_ok = all(
        urlparse(value).scheme == "https" and bool(urlparse(value).netloc)
        for value in (config.chatgpt_authorization_url, config.chatgpt_token_url, config.chatgpt_userinfo_url, config.chatgpt_redirect_uri)
    ) if config.chatgpt_enabled else False
    chatgpt_approved = _is_true(os.environ.get("SKILLSENTRA_CHATGPT_APPROVED"))
    if not config.chatgpt_enabled:
        chatgpt_status, chatgpt_detail = "review", "ChatGPT SSO remains disabled until external approval, credentials, callback review, and a real-host test are recorded."
    elif not chatgpt_configured or not chatgpt_urls_ok:
        chatgpt_status, chatgpt_detail = "block", "Enabled ChatGPT SSO must provide complete HTTPS OAuth endpoints and callback configuration."
    elif not chatgpt_approved:
        chatgpt_status, chatgpt_detail = "review", "Record the approved external application and a real authorization-code sign-in before release."
    else:
        chatgpt_status, chatgpt_detail = "pass", "ChatGPT OAuth configuration and approval flag are present; retain real-host evidence separately."
    checks.append(_check("chatgpt_sso", "ChatGPT SSO evidence", chatgpt_status, chatgpt_detail, required=False))

    backend = database_backend.strip().lower()
    checks.append(_check(
        "database_backend",
        "Managed database",
        "pass" if backend in {"postgres", "postgresql"} else "block",
        "A managed PostgreSQL deployment is required for multi-user production; SQLite is single-node only." if backend not in {"postgres", "postgresql"} else "PostgreSQL backend selected.",
    ))
    checks.append(_check(
        "backup_restore",
        "Backup and restore rehearsal",
        "pass" if config.backup_verified else "review",
        "Complete and record a restore rehearsal before release." if not config.backup_verified else "Backup and restore rehearsal was explicitly confirmed.",
        required=False,
    ))
    observability_ok = bool(config.observability_endpoint and urlparse(config.observability_endpoint).scheme in {"https", "http"})
    checks.append(_check(
        "observability",
        "Monitored observability",
        "pass" if observability_ok else "review",
        "Configure a monitored metrics/alert endpoint and verify notification ownership." if not observability_ok else "An observability endpoint is configured.",
        required=False,
    ))
    checks.append(_check(
        "real_host_evidence",
        "Real-host and human release evidence",
        "pass" if _is_true(os.environ.get("SKILLSENTRA_REAL_HOST_EVIDENCE")) else "review",
        "Automated tests do not replace real-host, accessibility, capacity, security, and human release evidence." if not _is_true(os.environ.get("SKILLSENTRA_REAL_HOST_EVIDENCE")) else "Real-host evidence flag is present; retain the underlying evidence package.",
        required=False,
    ))

    blocking = [item["id"] for item in checks if item["status"] == "block"]
    review = [item["id"] for item in checks if item["status"] == "review"]
    return {
        "status": "go" if not blocking and not review else "no_go",
        "release_authorized": False,
        "environment": config.environment,
        "checks": checks,
        "blocking_checks": blocking,
        "review_checks": review,
        "notes": [
            "This read-only snapshot does not publish, deploy, or authorize release.",
            "Internal tests and scores are evidence inputs, not a substitute for external gates or human approval.",
        ],
    }
