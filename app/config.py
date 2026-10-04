from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    static_dir: Path
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8766
    request_body_limit: int = 1_048_576
    artifact_dir: Path | None = None
    allowed_skill_roots: tuple[Path, ...] = ()
    auth_mode: str = "local"
    auth_tokens_json: str = ""
    trusted_origins: tuple[str, ...] = ()
    rate_limit_per_minute: int = 240
    max_workers: int = 64
    automation_enabled: bool = True
    automation_poll_seconds: int = 60
    session_days: int = 7
    demo_seed_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    oauth_state_minutes: int = 10
    environment: str = "development"
    public_origin: str = "http://127.0.0.1:8766"
    require_email_verification: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_starttls: bool = True
    chatgpt_enabled: bool = False
    chatgpt_client_id: str = ""
    chatgpt_client_secret: str = ""
    chatgpt_authorization_url: str = ""
    chatgpt_token_url: str = ""
    chatgpt_userinfo_url: str = ""
    chatgpt_redirect_uri: str = ""
    chatgpt_scope: str = "openid email profile"
    tls_termination_confirmed: bool = False
    backup_verified: bool = False
    observability_endpoint: str = ""

    @classmethod
    def from_env(cls) -> "AppConfig":
        root_dir = Path(__file__).resolve().parents[1]
        database_path = Path(
            os.environ.get(
                "SKILLSENTRA_DATABASE_PATH",
                root_dir / "data" / "skillsentra.db",
            )
        ).resolve()
        artifact_dir = Path(
            os.environ.get("SKILLSENTRA_ARTIFACT_DIR", root_dir / "data" / "artifacts")
        ).resolve()
        configured_roots = os.environ.get("SKILLSENTRA_ALLOWED_SKILL_ROOTS", "").strip()
        roots = [Path(item.strip()).expanduser().resolve() for item in configured_roots.split(os.pathsep) if item.strip()]
        sample_root = (root_dir / "sample-skills").resolve()
        if sample_root not in roots:
            roots.append(sample_root)
        host = os.environ.get("SKILLSENTRA_HOST", "127.0.0.1")
        environment = os.environ.get("SKILLSENTRA_ENV", "development").strip().lower()
        if environment not in {"development", "staging", "production"}:
            raise ValueError("SKILLSENTRA_ENV must be development, staging or production")
        auth_mode = os.environ.get("SKILLSENTRA_AUTH_MODE", "local").strip().lower()
        if auth_mode not in {"local", "token", "accounts", "hybrid"}:
            raise ValueError("SKILLSENTRA_AUTH_MODE must be local, token, accounts or hybrid")
        if host not in {"127.0.0.1", "localhost", "::1"} and auth_mode not in {"token", "accounts", "hybrid"}:
            raise ValueError("Non-loopback deployments require token, accounts or hybrid authentication")
        origins = tuple(
            item.strip().rstrip("/")
            for item in os.environ.get("SKILLSENTRA_TRUSTED_ORIGINS", "").split(",")
            if item.strip()
        )
        port = int(os.environ.get("SKILLSENTRA_PORT", "8766"))
        public_origin = os.environ.get("SKILLSENTRA_PUBLIC_ORIGIN", "").strip().rstrip("/")
        if not public_origin:
            public_origin = origins[0] if origins else f"http://{host}:{port}"
        return cls(
            root_dir=root_dir,
            static_dir=(root_dir / "demo-apple").resolve(),
            database_path=database_path,
            host=host,
            port=port,
            artifact_dir=artifact_dir,
            allowed_skill_roots=tuple(roots),
            auth_mode=auth_mode,
            auth_tokens_json=os.environ.get("SKILLSENTRA_AUTH_TOKENS_JSON", ""),
            trusted_origins=origins,
            rate_limit_per_minute=max(30, min(int(os.environ.get("SKILLSENTRA_RATE_LIMIT_PER_MINUTE", "240")), 10_000)),
            max_workers=max(8, min(int(os.environ.get("SKILLSENTRA_MAX_WORKERS", "64")), 512)),
            automation_enabled=os.environ.get("SKILLSENTRA_AUTOMATION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
            automation_poll_seconds=max(10, min(int(os.environ.get("SKILLSENTRA_AUTOMATION_POLL_SECONDS", "60")), 3600)),
            session_days=max(1, min(int(os.environ.get("SKILLSENTRA_SESSION_DAYS", "7")), 30)),
            demo_seed_enabled=os.environ.get("SKILLSENTRA_DEMO_SEED", "false").strip().lower() in {"1", "true", "yes", "on"},
            google_client_id=os.environ.get("SKILLSENTRA_GOOGLE_CLIENT_ID", "").strip(),
            google_client_secret=os.environ.get("SKILLSENTRA_GOOGLE_CLIENT_SECRET", "").strip(),
            google_redirect_uri=os.environ.get("SKILLSENTRA_GOOGLE_REDIRECT_URI", "").strip(),
            oauth_state_minutes=max(5, min(int(os.environ.get("SKILLSENTRA_OAUTH_STATE_MINUTES", "10")), 30)),
            environment=environment,
            public_origin=public_origin,
            require_email_verification=os.environ.get(
                "SKILLSENTRA_REQUIRE_EMAIL_VERIFICATION",
                "true" if environment == "production" else "false",
            ).strip().lower() in {"1", "true", "yes", "on"},
            smtp_host=os.environ.get("SKILLSENTRA_SMTP_HOST", "").strip(),
            smtp_port=max(1, min(int(os.environ.get("SKILLSENTRA_SMTP_PORT", "587")), 65535)),
            smtp_username=os.environ.get("SKILLSENTRA_SMTP_USERNAME", "").strip(),
            smtp_password=os.environ.get("SKILLSENTRA_SMTP_PASSWORD", ""),
            smtp_from_email=os.environ.get("SKILLSENTRA_SMTP_FROM", "").strip(),
            smtp_starttls=os.environ.get("SKILLSENTRA_SMTP_STARTTLS", "true").strip().lower() in {"1", "true", "yes", "on"},
            chatgpt_enabled=os.environ.get("SKILLSENTRA_CHATGPT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            chatgpt_client_id=os.environ.get("SKILLSENTRA_CHATGPT_CLIENT_ID", "").strip(),
            chatgpt_client_secret=os.environ.get("SKILLSENTRA_CHATGPT_CLIENT_SECRET", ""),
            chatgpt_authorization_url=os.environ.get("SKILLSENTRA_CHATGPT_AUTHORIZATION_URL", "").strip(),
            chatgpt_token_url=os.environ.get("SKILLSENTRA_CHATGPT_TOKEN_URL", "").strip(),
            chatgpt_userinfo_url=os.environ.get("SKILLSENTRA_CHATGPT_USERINFO_URL", "").strip(),
            chatgpt_redirect_uri=os.environ.get("SKILLSENTRA_CHATGPT_REDIRECT_URI", "").strip(),
            chatgpt_scope=os.environ.get("SKILLSENTRA_CHATGPT_SCOPE", "openid email profile").strip() or "openid email profile",
            tls_termination_confirmed=os.environ.get("SKILLSENTRA_TLS_TERMINATION_CONFIRMED", "false").strip().lower() in {"1", "true", "yes", "on"},
            backup_verified=os.environ.get("SKILLSENTRA_BACKUP_VERIFIED", "false").strip().lower() in {"1", "true", "yes", "on"},
            observability_endpoint=os.environ.get("SKILLSENTRA_OBSERVABILITY_ENDPOINT", "").strip(),
        )
