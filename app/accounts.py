from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage as MIMEEmailMessage
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .control_plane import Principal
from .database import Database, json_dumps, utc_now
from .errors import AppError
from .repository import make_id


EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,63}$")
COMMON_PASSWORDS = {
    "1234567890", "password123", "qwerty12345", "administrator", "skillsentra", "letmein1234",
}
GOOGLE_CONFIGURATION_FIELDS = (
    ("SKILLSENTRA_GOOGLE_CLIENT_ID", "google_client_id", False),
    ("SKILLSENTRA_GOOGLE_CLIENT_SECRET", "google_client_secret", True),
    ("SKILLSENTRA_GOOGLE_REDIRECT_URI", "google_redirect_uri", False),
)
CHATGPT_EXTERNAL_REQUIREMENTS = (
    "external_application_partner_access",
    "oauth_client_id",
    "oauth_client_secret",
    "approved_redirect_uri",
    "server_callback_implementation",
)
CHATGPT_CONFIGURATION_FIELDS = (
    ("SKILLSENTRA_CHATGPT_CLIENT_ID", "chatgpt_client_id", False),
    ("SKILLSENTRA_CHATGPT_CLIENT_SECRET", "chatgpt_client_secret", True),
    ("SKILLSENTRA_CHATGPT_AUTHORIZATION_URL", "chatgpt_authorization_url", False),
    ("SKILLSENTRA_CHATGPT_TOKEN_URL", "chatgpt_token_url", False),
    ("SKILLSENTRA_CHATGPT_USERINFO_URL", "chatgpt_userinfo_url", False),
    ("SKILLSENTRA_CHATGPT_REDIRECT_URI", "chatgpt_redirect_uri", False),
)


@dataclass(frozen=True)
class AccountEmailMessage:
    to_email: str
    subject: str
    body: str


class EmailDelivery(Protocol):
    def send(self, message: AccountEmailMessage) -> None:
        """Deliver one account email without returning secrets to the caller."""


class SMTPEmailDelivery:
    """Small server-side SMTP adapter used only when production config opts in."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
        *,
        starttls: bool = True,
        timeout: float = 10.0,
    ):
        self.host = host.strip()
        self.port = int(port)
        self.username = username.strip()
        self.password = password
        self.from_email = from_email.strip()
        self.starttls = bool(starttls)
        self.timeout = max(2.0, min(float(timeout), 30.0))
        if not self.host or not self.from_email or not (1 <= self.port <= 65535):
            raise ValueError("invalid SMTP configuration")
        if not self.starttls:
            raise ValueError("SMTP STARTTLS is required")

    def send(self, message: AccountEmailMessage) -> None:
        email = MIMEEmailMessage()
        email["From"] = self.from_email
        email["To"] = message.to_email
        email["Subject"] = message.subject
        email.set_content(message.body)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            if self.username:
                client.login(self.username, self.password)
            client.send_message(email)


@dataclass(frozen=True)
class AccountSession:
    principal: Principal
    user: dict[str, Any]
    session_token: str
    csrf_token: str
    expires_at: str


@dataclass(frozen=True)
class AccountRegistrationPending:
    user: dict[str, Any]
    verification_required: bool = True


class AccountService:
    def __init__(
        self, database: Database, session_days: int = 7, google_client_id: str = "",
        google_client_secret: str = "", google_redirect_uri: str = "", oauth_state_minutes: int = 10,
        *,
        require_email_verification: bool = False,
        public_origin: str = "http://127.0.0.1:8766",
        email_delivery: EmailDelivery | None = None,
        chatgpt_enabled: bool = False,
        chatgpt_client_id: str = "",
        chatgpt_client_secret: str = "",
        chatgpt_authorization_url: str = "",
        chatgpt_token_url: str = "",
        chatgpt_userinfo_url: str = "",
        chatgpt_redirect_uri: str = "",
        chatgpt_scope: str = "openid email profile",
    ):
        self.database = database
        self.session_days = max(1, min(session_days, 30))
        self._pepper = os.environ.get("SKILLSENTRA_PASSWORD_PEPPER", "").encode("utf-8")
        self.google_client_id = google_client_id
        self.google_client_secret = google_client_secret
        self.google_redirect_uri = google_redirect_uri
        self.oauth_state_minutes = max(5, min(oauth_state_minutes, 30))
        self.require_email_verification = bool(require_email_verification)
        self.public_origin = public_origin.strip().rstrip("/") or "http://127.0.0.1:8766"
        self.email_delivery = email_delivery
        self.chatgpt_enabled = bool(chatgpt_enabled)
        self.chatgpt_client_id = chatgpt_client_id.strip()
        self.chatgpt_client_secret = chatgpt_client_secret
        self.chatgpt_authorization_url = chatgpt_authorization_url.strip()
        self.chatgpt_token_url = chatgpt_token_url.strip()
        self.chatgpt_userinfo_url = chatgpt_userinfo_url.strip()
        self.chatgpt_redirect_uri = chatgpt_redirect_uri.strip()
        self.chatgpt_scope = chatgpt_scope.strip() or "openid email profile"

    def register(self, payload: dict[str, Any]) -> AccountSession | AccountRegistrationPending:
        email = self._email(payload.get("email"))
        display_name = str(payload.get("display_name") or "").strip()
        password = self._password(payload.get("password"))
        if len(display_name) < 2 or len(display_name) > 60:
            raise AppError("invalid_display_name", "显示名称必须为 2–60 个字符。")
        if self.require_email_verification and self.email_delivery is None:
            raise AppError("email_delivery_unconfigured", "账户验证邮件服务尚未配置，请联系管理员。", 503)
        salt = secrets.token_bytes(16)
        password_hash = self._derive(password, salt)
        user_id = make_id("usr")
        tenant_id = make_id("ten")
        now = utc_now()
        slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@", 1)[0].lower()).strip("-") or "member"
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM users WHERE normalized_email=?", (email,)).fetchone():
                raise AppError("account_exists", "该邮箱已注册，请直接登录。", 409)
            slug = f"{slug_base}-{user_id[-6:]}"
            connection.execute(
                "INSERT INTO tenants(id, name, slug, plan, status, created_at, updated_at) VALUES (?, ?, ?, 'personal', 'active', ?, ?)",
                (tenant_id, f"{display_name} Workspace", slug, now, now),
            )
            connection.execute(
                """
                INSERT INTO users(id, email, normalized_email, display_name, password_salt, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, email, email, display_name,
                    base64.b64encode(salt).decode("ascii"), base64.b64encode(password_hash).decode("ascii"), now, now,
                ),
            )
            connection.execute(
                "INSERT INTO tenant_memberships(tenant_id, user_id, roles_json, created_at) VALUES (?, ?, ?, ?)",
                (tenant_id, user_id, json_dumps(["creator", "buyer", "finance"]), now),
            )
        if self.require_email_verification:
            token = self._issue_action_token(user_id, "email_verification", ttl_minutes=24 * 60)
            try:
                self._send_account_email(
                    email,
                    "验证你的 SkillSentra 账户",
                    f"请在 24 小时内打开以下链接完成邮箱验证：{self.public_origin}/api/v1/auth/verify-email?token={token}",
                )
            except Exception as exc:
                raise AppError("email_delivery_failed", "验证邮件发送失败，请稍后重试。", 503) from exc
            return AccountRegistrationPending(
                {
                    "id": user_id,
                    "email": email,
                    "display_name": display_name,
                    "tenant_id": tenant_id,
                    "roles": ["buyer", "creator", "finance"],
                    "email_verified": False,
                }
            )
        return self._new_session(user_id, tenant_id)

    def login(self, payload: dict[str, Any]) -> AccountSession:
        email = self._email(payload.get("email"))
        password = str(payload.get("password") or "")
        if not 1 <= len(password) <= 128:
            self._derive("invalid-password", b"\0" * 16)
            raise AppError("login_failed", "邮箱或密码不正确。", 401)
        with self.database.session() as connection:
            row = connection.execute("SELECT * FROM users WHERE normalized_email=?", (email,)).fetchone()
        if row is None:
            self._derive(password or "invalid-password", b"\0" * 16)
            raise AppError("login_failed", "邮箱或密码不正确。", 401)
        user = dict(row)
        try:
            salt = base64.b64decode(user["password_salt"], validate=True)
            expected = base64.b64decode(user["password_hash"], validate=True)
        except (ValueError, TypeError) as exc:
            raise AppError("account_unavailable", "账户暂时不可用。", 503) from exc
        if not hmac.compare_digest(self._derive(password, salt), expected) or user["status"] != "active":
            raise AppError("login_failed", "邮箱或密码不正确。", 401)
        if self.require_email_verification and not bool(user.get("email_verified")):
            raise AppError("email_unverified", "请先验证邮箱后再登录。", 403)
        with self.database.session() as connection:
            membership = connection.execute(
                "SELECT tenant_id FROM tenant_memberships WHERE user_id=? AND status='active' ORDER BY created_at LIMIT 1",
                (user["id"],),
            ).fetchone()
        if membership is None:
            raise AppError("membership_missing", "账户没有可用工作区。", 403)
        return self._new_session(str(user["id"]), str(membership["tenant_id"]))

    def authenticate(self, session_token: str) -> AccountSession | None:
        if len(session_token) < 32:
            return None
        token_hash = self._token_hash(session_token)
        now = utc_now()
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT s.*, u.email, u.display_name, u.email_verified, u.status AS user_status, m.roles_json, m.status AS membership_status
                FROM user_sessions s JOIN users u ON u.id=s.user_id
                JOIN tenant_memberships m ON m.user_id=s.user_id AND m.tenant_id=s.tenant_id
                WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?
                """,
                (token_hash, now),
            ).fetchone()
        if row is None or row["user_status"] != "active" or row["membership_status"] != "active":
            return None
        if self.require_email_verification and not bool(row["email_verified"]):
            return None
        record = dict(row)
        roles = frozenset(self._roles(record.get("roles_json")))
        principal = Principal(str(record["tenant_id"]), str(record["user_id"]), roles, "session")
        user = {
            "id": record["user_id"], "email": record["email"], "display_name": record["display_name"],
            "email_verified": bool(record["email_verified"]),
            "tenant_id": record["tenant_id"], "roles": sorted(roles),
        }
        return AccountSession(principal, user, session_token, "", str(record["expires_at"]))

    def verify_csrf(self, session_token: str, csrf_token: str) -> None:
        if not session_token or not csrf_token:
            raise AppError("csrf_required", "登录会话缺少安全校验信息，请刷新后重试。", 403)
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT csrf_hash FROM user_sessions WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
                (self._token_hash(session_token), utc_now()),
            ).fetchone()
        if row is None or not hmac.compare_digest(str(row["csrf_hash"]), self._token_hash(csrf_token)):
            raise AppError("csrf_invalid", "安全校验失败，请刷新后重试。", 403)

    def logout(self, session_token: str) -> None:
        if not session_token:
            return
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (utc_now(), self._token_hash(session_token)),
            )

    def verify_email(self, token: str) -> dict[str, Any]:
        token = self._validate_action_token(token, "email_verification")
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT t.id, u.id AS user_id, u.email, u.display_name
                FROM account_action_tokens t JOIN users u ON u.id=t.user_id
                WHERE t.token_type='email_verification' AND t.token_hash=?
                  AND t.expires_at>? AND t.consumed_at IS NULL AND u.status='active'
                """,
                (self._token_hash(token), now),
            ).fetchone()
            if row is None:
                raise AppError("email_verification_invalid", "邮箱验证链接无效或已过期。", 400)
            connection.execute("UPDATE users SET email_verified=1, updated_at=? WHERE id=?", (now, row["user_id"]))
            connection.execute("UPDATE account_action_tokens SET consumed_at=? WHERE id=?", (now, row["id"]))
        return {
            "verified": True,
            "user_id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"],
        }

    def request_password_reset(self, payload: dict[str, Any]) -> dict[str, Any]:
        email = self._email(payload.get("email"))
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT id, email, status FROM users WHERE normalized_email=?", (email,)
            ).fetchone()
        if row is not None and row["status"] == "active":
            token = self._issue_action_token(str(row["id"]), "password_reset", ttl_minutes=60)
            if self.email_delivery is not None:
                try:
                    self._send_account_email(
                        email,
                        "重置你的 SkillSentra 密码",
                        f"请在 60 分钟内打开以下链接完成密码重置：{self.public_origin}/marketplace.html?reset_token={token}",
                    )
                except Exception as exc:
                    raise AppError("email_delivery_failed", "密码重置邮件发送失败，请稍后重试。", 503) from exc
        # This response is intentionally identical for unknown and known emails.
        return {"accepted": True}

    def resend_email_verification(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Issue a fresh verification link without revealing account existence."""
        email = self._email(payload.get("email"))
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT id, email, email_verified, status FROM users WHERE normalized_email=?", (email,)
            ).fetchone()
        if row is not None and row["status"] == "active" and not bool(row["email_verified"]):
            if self.email_delivery is not None:
                token = self._issue_action_token(str(row["id"]), "email_verification", ttl_minutes=24 * 60)
                try:
                    self._send_account_email(
                        email,
                        "验证你的 SkillSentra 账户",
                        f"请在 24 小时内打开以下链接完成邮箱验证：{self.public_origin}/api/v1/auth/verify-email?token={token}",
                    )
                except Exception as exc:
                    raise AppError("email_delivery_failed", "验证邮件发送失败，请稍后重试。", 503) from exc
        return {"accepted": True}

    def reset_password(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._validate_action_token(payload.get("token"), "password_reset")
        password = self._password(payload.get("password"))
        now = utc_now()
        salt = secrets.token_bytes(16)
        password_hash = self._derive(password, salt)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, user_id FROM account_action_tokens
                WHERE token_type='password_reset' AND token_hash=?
                  AND expires_at>? AND consumed_at IS NULL
                """,
                (self._token_hash(token), now),
            ).fetchone()
            if row is None:
                raise AppError("password_reset_invalid", "密码重置链接无效或已过期。", 400)
            connection.execute(
                """
                UPDATE users SET password_salt=?, password_hash=?, email_verified=1, updated_at=?
                WHERE id=? AND status='active'
                """,
                (
                    base64.b64encode(salt).decode("ascii"),
                    base64.b64encode(password_hash).decode("ascii"),
                    now,
                    row["user_id"],
                ),
            )
            connection.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, row["user_id"]),
            )
            connection.execute("UPDATE account_action_tokens SET consumed_at=? WHERE id=?", (now, row["id"]))
            connection.execute(
                "UPDATE account_action_tokens SET consumed_at=? WHERE user_id=? AND token_type='password_reset' AND consumed_at IS NULL",
                (now, row["user_id"]),
            )
        return {"reset": True}

    def _issue_action_token(self, user_id: str, token_type: str, *, ttl_minutes: int) -> str:
        if token_type not in {"email_verification", "password_reset"}:
            raise ValueError("unsupported account action token")
        token = secrets.token_urlsafe(32)
        now_dt = datetime.now(timezone.utc)
        expires_at = (now_dt + timedelta(minutes=max(5, min(ttl_minutes, 7 * 24 * 60)))).isoformat(timespec="milliseconds")
        with self.database.transaction() as connection:
            # Keep only the newest token for each action so a resend invalidates
            # previously delivered links and bounds the token table per user.
            connection.execute(
                "DELETE FROM account_action_tokens WHERE user_id=? AND token_type=?",
                (user_id, token_type),
            )
            connection.execute(
                """
                INSERT INTO account_action_tokens(id, user_id, token_type, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (make_id("act"), user_id, token_type, self._token_hash(token), expires_at, now_dt.isoformat(timespec="milliseconds")),
            )
        return token

    def _send_account_email(self, to_email: str, subject: str, body: str) -> None:
        if self.email_delivery is None:
            raise RuntimeError("account email delivery is not configured")
        self.email_delivery.send(AccountEmailMessage(to_email=to_email, subject=subject, body=body))

    @staticmethod
    def _validate_action_token(value: Any, token_type: str) -> str:
        token = str(value or "").strip()
        if token_type not in {"email_verification", "password_reset"} or not 32 <= len(token) <= 512:
            raise AppError("account_action_invalid", "账户操作链接无效或已过期。", 400)
        return token

    def providers(self, *, detailed: bool = True) -> dict[str, dict[str, Any]]:
        google_configuration = [
            {
                "field": field,
                "configured": bool(getattr(self, attribute)),
                "secret": secret,
                "source": "deployment_environment",
            }
            for field, attribute, secret in GOOGLE_CONFIGURATION_FIELDS
        ]
        google_missing = [item["field"] for item in google_configuration if not item["configured"]]
        google_ready = not google_missing
        if google_ready:
            google_reason = ""
            google_next_steps = [
                "Confirm that Google Cloud lists the exact configured HTTPS redirect URI ending in /api/v1/auth/google/callback.",
                "Run a real authorization-code sign-in and verify the returned email is marked verified.",
            ]
        else:
            google_reason = f"Google OAuth 尚未就绪，缺少服务器配置：{', '.join(google_missing)}。"
            google_next_steps = [
                "Create or select a Google OAuth web client in Google Cloud.",
                "Store every missing field in the deployment secret/configuration manager; do not put client secrets in browser code.",
                "Register the same HTTPS callback URI ending in /api/v1/auth/google/callback, restart SkillSentra, and check this endpoint again.",
            ]
        chatgpt_configuration = [
            {
                "field": field,
                "configured": bool(getattr(self, attribute)),
                "secret": secret,
                "source": "openai_external_application_onboarding",
            }
            for field, attribute, secret in CHATGPT_CONFIGURATION_FIELDS
        ]
        chatgpt_missing = [item["field"] for item in chatgpt_configuration if not item["configured"]]
        chatgpt_ready = self.chatgpt_enabled and not chatgpt_missing
        if chatgpt_ready:
            chatgpt_status = "ready"
            chatgpt_reason = ""
            chatgpt_next_steps = [
                "Confirm the approved OpenAI/ChatGPT OAuth application lists the exact HTTPS redirect URI.",
                "Run a real authorization-code sign-in and verify the returned email is marked verified.",
            ]
        elif self.chatgpt_enabled:
            chatgpt_status = "missing_configuration"
            chatgpt_reason = f"ChatGPT OAuth 已启用但缺少服务器配置：{', '.join(chatgpt_missing)}。"
            chatgpt_next_steps = [
                "Store the approved OAuth client settings in the deployment secret/configuration manager.",
                "Register the same HTTPS callback URI and keep PKCE enabled.",
                "Run a real-host sign-in test; do not simulate a ChatGPT identity.",
            ]
        else:
            chatgpt_status = "external_access_required"
            chatgpt_reason = "Sign in with ChatGPT 需要 OpenAI 对外部应用的合作准入、获批 OAuth 凭据和服务端回调实现；当前未启用，因此保持不可用。"
            chatgpt_next_steps = [
                "Obtain OpenAI external-application partner access and approved OAuth client credentials.",
                "Set SKILLSENTRA_CHATGPT_ENABLED=true only after the callback and security review are complete.",
                "Run a real-host sign-in test; do not simulate a ChatGPT identity while approval or credentials are absent.",
            ]
        providers = {
            "google": {
                "available": google_ready,
                "start_url": "/api/v1/auth/google/start" if google_ready else "",
                "configuration_status": "ready" if google_ready else "missing_configuration",
                "required_configuration": google_configuration,
                "missing_configuration": google_missing,
                "reason": google_reason,
                "next_steps": google_next_steps,
            },
            "chatgpt": {
                "available": chatgpt_ready,
                "start_url": "/api/v1/auth/chatgpt/start" if chatgpt_ready else "",
                "configuration_status": chatgpt_status,
                "required_configuration": chatgpt_configuration,
                "missing_configuration": (
                    chatgpt_missing if self.chatgpt_enabled
                    else list(CHATGPT_EXTERNAL_REQUIREMENTS)
                ),
                "reason": chatgpt_reason,
                "next_steps": chatgpt_next_steps,
            },
        }
        if detailed:
            return providers
        return {
            "google": {
                "available": google_ready,
                "start_url": "/api/v1/auth/google/start" if google_ready else "",
                "reason_code": "ready" if google_ready else "provider_unavailable",
                "reason": "" if google_ready else "Google 登录当前不可用。",
            },
            "chatgpt": {
                "available": chatgpt_ready,
                "start_url": "/api/v1/auth/chatgpt/start" if chatgpt_ready else "",
                "reason_code": (
                    "ready" if chatgpt_ready
                    else "provider_unavailable" if self.chatgpt_enabled
                    else "external_access_required"
                ),
                "reason": "" if chatgpt_ready else chatgpt_reason,
            },
        }

    def begin_google_login(self) -> tuple[str, str]:
        if not (self.google_client_id and self.google_client_secret and self.google_redirect_uri):
            raise AppError("provider_not_configured", "Google 登录尚未配置，请联系管理员。", 503)
        state = secrets.token_urlsafe(32)
        now_dt = datetime.now(timezone.utc)
        expires_at = (now_dt + timedelta(minutes=self.oauth_state_minutes)).isoformat(timespec="milliseconds")
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM oauth_login_states WHERE expires_at<? OR consumed_at IS NOT NULL", (utc_now(),))
            connection.execute(
                "INSERT INTO oauth_login_states(id, provider, state_hash, expires_at, created_at) VALUES (?, 'google', ?, ?, ?)",
                (make_id("ost"), self._token_hash(state), expires_at, now_dt.isoformat(timespec="milliseconds")),
            )
        query = urlencode({
            "client_id": self.google_client_id,
            "redirect_uri": self.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        })
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}", state

    def begin_chatgpt_login(self) -> tuple[str, str, str]:
        if not self.chatgpt_enabled or not self._chatgpt_ready():
            raise AppError("provider_not_configured", "ChatGPT 登录尚未完成外部应用配置，请联系管理员。", 503)
        self._validate_oauth_endpoint(self.chatgpt_authorization_url, "ChatGPT 授权地址")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        now_dt = datetime.now(timezone.utc)
        expires_at = (now_dt + timedelta(minutes=self.oauth_state_minutes)).isoformat(timespec="milliseconds")
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM oauth_login_states WHERE expires_at<? OR consumed_at IS NOT NULL", (utc_now(),))
            connection.execute(
                "INSERT INTO oauth_login_states(id, provider, state_hash, expires_at, created_at) VALUES (?, 'chatgpt', ?, ?, ?)",
                (make_id("ost"), self._token_hash(state), expires_at, now_dt.isoformat(timespec="milliseconds")),
            )
        query = urlencode({
            "client_id": self.chatgpt_client_id,
            "redirect_uri": self.chatgpt_redirect_uri,
            "response_type": "code",
            "scope": self.chatgpt_scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        })
        separator = "&" if "?" in self.chatgpt_authorization_url else "?"
        return f"{self.chatgpt_authorization_url}{separator}{query}", state, verifier

    def finish_google_login(self, code: str, state: str, cookie_state: str) -> AccountSession:
        if not code or len(code) > 4096 or not state or len(state) > 512:
            raise AppError("oauth_callback_invalid", "第三方登录返回无效，请重新开始。")
        if not cookie_state or not hmac.compare_digest(state, cookie_state):
            raise AppError("oauth_state_invalid", "第三方登录安全校验失败，请重新开始。", 403)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM oauth_login_states WHERE provider='google' AND state_hash=? AND expires_at>? AND consumed_at IS NULL",
                (self._token_hash(state), utc_now()),
            ).fetchone()
            if row is None:
                raise AppError("oauth_state_invalid", "第三方登录已过期或无效，请重新开始。", 403)
            connection.execute("UPDATE oauth_login_states SET consumed_at=? WHERE id=?", (utc_now(), row["id"]))
        identity = self._google_userinfo(code)
        return self._external_login("google", identity["sub"], identity["email"], identity["name"])

    def finish_chatgpt_login(
        self, code: str, state: str, cookie_state: str, code_verifier: str
    ) -> AccountSession:
        if not code or len(code) > 4096 or not state or len(state) > 512:
            raise AppError("oauth_callback_invalid", "第三方登录返回无效，请重新开始。")
        if not cookie_state or not hmac.compare_digest(state, cookie_state):
            raise AppError("oauth_state_invalid", "第三方登录安全校验失败，请重新开始。", 403)
        if not code_verifier or len(code_verifier) > 512:
            raise AppError("oauth_callback_invalid", "第三方登录缺少安全校验信息，请重新开始。", 400)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM oauth_login_states WHERE provider='chatgpt' AND state_hash=? AND expires_at>? AND consumed_at IS NULL",
                (self._token_hash(state), utc_now()),
            ).fetchone()
            if row is None:
                raise AppError("oauth_state_invalid", "第三方登录已过期或无效，请重新开始。", 403)
            connection.execute("UPDATE oauth_login_states SET consumed_at=? WHERE id=?", (utc_now(), row["id"]))
        identity = self._chatgpt_userinfo(code, code_verifier)
        return self._external_login("chatgpt", identity["sub"], identity["email"], identity["name"])

    def _google_userinfo(self, code: str) -> dict[str, str]:
        token_body = urlencode({
            "code": code,
            "client_id": self.google_client_id,
            "client_secret": self.google_client_secret,
            "redirect_uri": self.google_redirect_uri,
            "grant_type": "authorization_code",
        }).encode("utf-8")
        try:
            token_request = Request(
                "https://oauth2.googleapis.com/token", data=token_body,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST",
            )
            with urlopen(token_request, timeout=10, context=ssl.create_default_context()) as response:
                token_payload = self._oauth_json(response.read())
            access_token = str(token_payload.get("access_token") or "")
            if not access_token or len(access_token) > 4096:
                raise ValueError("missing access token")
            profile_request = Request(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            with urlopen(profile_request, timeout=10, context=ssl.create_default_context()) as response:
                profile = self._oauth_json(response.read())
        except Exception as exc:
            raise AppError("oauth_provider_unavailable", "Google 登录暂时不可用，请稍后重试。", 503) from exc
        subject = str(profile.get("sub") or "")
        email = self._email(profile.get("email"))
        verified = profile.get("email_verified") is True or str(profile.get("email_verified")).lower() == "true"
        if not 1 <= len(subject) <= 255 or not verified:
            raise AppError("oauth_identity_invalid", "Google 账户未提供可验证邮箱，请改用邮箱注册。", 403)
        name = str(profile.get("name") or email.split("@", 1)[0]).strip()
        return {"sub": subject, "email": email, "name": name[:60] if len(name) >= 2 else "Google User"}

    def _chatgpt_userinfo(self, code: str, code_verifier: str) -> dict[str, str]:
        self._validate_oauth_endpoint(self.chatgpt_token_url, "ChatGPT 令牌地址")
        self._validate_oauth_endpoint(self.chatgpt_userinfo_url, "ChatGPT 用户信息地址")
        token_body = urlencode({
            "code": code,
            "client_id": self.chatgpt_client_id,
            "client_secret": self.chatgpt_client_secret,
            "redirect_uri": self.chatgpt_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }).encode("utf-8")
        try:
            token_request = Request(
                self.chatgpt_token_url,
                data=token_body,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                method="POST",
            )
            with urlopen(token_request, timeout=10, context=ssl.create_default_context()) as response:
                token_payload = self._oauth_json(response.read())
            access_token = str(token_payload.get("access_token") or "")
            if not access_token or len(access_token) > 4096:
                raise ValueError("missing access token")
            profile_request = Request(
                self.chatgpt_userinfo_url,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            with urlopen(profile_request, timeout=10, context=ssl.create_default_context()) as response:
                profile = self._oauth_json(response.read())
        except AppError:
            raise
        except Exception as exc:
            raise AppError("oauth_provider_unavailable", "ChatGPT 登录暂时不可用，请稍后重试。", 503) from exc
        return self._validated_external_identity(profile, "ChatGPT")

    @staticmethod
    def _validate_oauth_endpoint(value: str, label: str) -> None:
        parsed = urlparse(str(value or ""))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
        ):
            raise AppError("provider_not_configured", f"{label}必须使用不含凭据和查询参数的 HTTPS 地址。", 503)

    @staticmethod
    def _validated_external_identity(profile: dict[str, Any], provider_label: str) -> dict[str, str]:
        subject = str(profile.get("sub") or "")
        email = AccountService._email(profile.get("email"))
        verified = profile.get("email_verified") is True or str(profile.get("email_verified")).lower() == "true"
        if not 1 <= len(subject) <= 255 or not verified:
            raise AppError("oauth_identity_invalid", f"{provider_label}账户未提供可验证邮箱，请改用邮箱注册。", 403)
        name = str(profile.get("name") or email.split("@", 1)[0]).strip()
        return {"sub": subject, "email": email, "name": name[:60] if len(name) >= 2 else f"{provider_label} User"}

    def _chatgpt_ready(self) -> bool:
        return bool(
            self.chatgpt_client_id
            and self.chatgpt_client_secret
            and self.chatgpt_authorization_url
            and self.chatgpt_token_url
            and self.chatgpt_userinfo_url
            and self.chatgpt_redirect_uri
        )

    @staticmethod
    def _oauth_json(raw: bytes) -> dict[str, Any]:
        import json
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("invalid oauth response")
        return parsed

    def _external_login(self, provider: str, subject: str, email: str, display_name: str) -> AccountSession:
        now = utc_now()
        with self.database.transaction() as connection:
            identity = connection.execute(
                "SELECT user_id FROM oauth_identities WHERE provider=? AND provider_subject=?", (provider, subject),
            ).fetchone()
            if identity is not None:
                user_id = str(identity["user_id"])
            else:
                user = connection.execute("SELECT id FROM users WHERE normalized_email=?", (email,)).fetchone()
                if user is None:
                    user_id = make_id("usr")
                    tenant_id = make_id("ten")
                    slug_base = re.sub(r"[^a-z0-9]+", "-", email.split("@", 1)[0].lower()).strip("-") or "member"
                    salt = secrets.token_bytes(16)
                    connection.execute(
                        "INSERT INTO tenants(id, name, slug, plan, status, created_at, updated_at) VALUES (?, ?, ?, 'personal', 'active', ?, ?)",
                        (tenant_id, f"{display_name} Workspace", f"{slug_base}-{user_id[-6:]}", now, now),
                    )
                    connection.execute(
                        "INSERT INTO users(id, email, normalized_email, display_name, password_salt, password_hash, email_verified, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (user_id, email, email, display_name, base64.b64encode(salt).decode("ascii"), base64.b64encode(secrets.token_bytes(32)).decode("ascii"), now, now),
                    )
                    connection.execute(
                        "INSERT INTO tenant_memberships(tenant_id, user_id, roles_json, created_at) VALUES (?, ?, ?, ?)",
                        (tenant_id, user_id, json_dumps(["creator", "buyer", "finance"]), now),
                    )
                else:
                    user_id = str(user["id"])
                connection.execute(
                    "INSERT INTO oauth_identities(id, user_id, provider, provider_subject, email_at_link, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (make_id("oid"), user_id, provider, subject, email, now, now),
                )
            membership = connection.execute(
                "SELECT tenant_id FROM tenant_memberships WHERE user_id=? AND status='active' ORDER BY created_at LIMIT 1", (user_id,),
            ).fetchone()
            account = connection.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
        if account is None or account["status"] != "active":
            raise AppError("account_unavailable", "账户暂时不可用。", 403)
        if membership is None:
            raise AppError("membership_missing", "账户没有可用工作区。", 403)
        return self._new_session(user_id, str(membership["tenant_id"]))

    def _new_session(self, user_id: str, tenant_id: str) -> AccountSession:
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="milliseconds")
        expires_at = (now_dt + timedelta(days=self.session_days)).isoformat(timespec="milliseconds")
        session_id = make_id("ses")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO user_sessions(id, user_id, tenant_id, token_hash, csrf_hash, expires_at, last_seen_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_id, tenant_id, self._token_hash(session_token), self._token_hash(csrf_token), expires_at, now, now),
            )
            user_row = connection.execute("SELECT id, email, display_name, email_verified FROM users WHERE id=?", (user_id,)).fetchone()
            membership = connection.execute(
                "SELECT roles_json FROM tenant_memberships WHERE tenant_id=? AND user_id=?",
                (tenant_id, user_id),
            ).fetchone()
        roles = frozenset(self._roles(str(membership["roles_json"])))
        principal = Principal(tenant_id, user_id, roles, "session")
        user = {
            "id": user_row["id"], "email": user_row["email"], "display_name": user_row["display_name"],
            "email_verified": bool(user_row["email_verified"]),
            "tenant_id": tenant_id, "roles": sorted(roles),
        }
        return AccountSession(principal, user, session_token, csrf_token, expires_at)

    def _derive(self, password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8") + self._pepper,
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _roles(value: str) -> list[str]:
        import json

        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _email(value: Any) -> str:
        email = str(value or "").strip().casefold()
        if len(email) > 254 or not EMAIL_RE.fullmatch(email):
            raise AppError("invalid_email", "请输入有效邮箱地址。")
        return email

    @staticmethod
    def _password(value: Any) -> str:
        password = str(value or "")
        if len(password) < 10 or len(password) > 128:
            raise AppError("invalid_password", "密码必须为 10–128 个字符。")
        if password.casefold() in COMMON_PASSWORDS or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise AppError("weak_password", "密码需同时包含字母和数字，且不能使用常见密码。")
        return password
