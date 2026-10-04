from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.accounts import AccountEmailMessage, AccountRegistrationPending, AccountService, SMTPEmailDelivery
from app.config import AppConfig
from app.database import Database
from app.errors import AppError
from app.production import evaluate_production_readiness
from app.repository import Repository


class RecordingEmailDelivery:
    def __init__(self) -> None:
        self.messages: list[AccountEmailMessage] = []

    def send(self, message: AccountEmailMessage) -> None:
        self.messages.append(message)


class AccountLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "accounts.db")
        self.database.migrate()
        self.mailer = RecordingEmailDelivery()
        self.accounts = AccountService(
            self.database,
            session_days=2,
            require_email_verification=True,
            public_origin="https://skillsentra.example.test",
            email_delivery=self.mailer,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _token(body: str) -> str:
        match = re.search(r"token=([A-Za-z0-9_-]+)", body)
        assert match is not None
        return match.group(1)

    def test_email_verification_blocks_login_then_allows_it(self) -> None:
        pending = self.accounts.register({
            "email": "new-user@example.com", "display_name": "New User", "password": "SecurePass123",
        })
        self.assertIsInstance(pending, AccountRegistrationPending)
        self.assertFalse(pending.user["email_verified"])
        self.assertEqual(len(self.mailer.messages), 1)
        token = self._token(self.mailer.messages[-1].body)
        with self.assertRaises(AppError) as context:
            self.accounts.login({"email": "new-user@example.com", "password": "SecurePass123"})
        self.assertEqual(context.exception.code, "email_unverified")
        with self.database.session() as connection:
            row = connection.execute("SELECT token_hash FROM account_action_tokens").fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row["token_hash"], token)
        verified = self.accounts.verify_email(token)
        self.assertTrue(verified["verified"])
        session = self.accounts.login({"email": "new-user@example.com", "password": "SecurePass123"})
        self.assertTrue(session.user["email_verified"])

    def test_password_reset_is_generic_and_revokes_sessions(self) -> None:
        pending = self.accounts.register({
            "email": "reset-user@example.com", "display_name": "Reset User", "password": "SecurePass123",
        })
        self.accounts.verify_email(self._token(self.mailer.messages[-1].body))
        old_session = self.accounts.login({"email": "reset-user@example.com", "password": "SecurePass123"})
        before = len(self.mailer.messages)
        self.assertEqual(self.accounts.request_password_reset({"email": "missing@example.com"}), {"accepted": True})
        self.assertEqual(len(self.mailer.messages), before)
        self.assertEqual(self.accounts.request_password_reset({"email": "reset-user@example.com"}), {"accepted": True})
        self.assertEqual(len(self.mailer.messages), before + 1)
        token = self._token(self.mailer.messages[-1].body)
        self.assertEqual(self.accounts.reset_password({"token": token, "password": "NewSecure456"}), {"reset": True})
        self.assertIsNone(self.accounts.authenticate(old_session.session_token))
        fresh = self.accounts.login({"email": "reset-user@example.com", "password": "NewSecure456"})
        self.assertEqual(fresh.user["email"], "reset-user@example.com")
        with self.assertRaises(AppError) as context:
            self.accounts.reset_password({"token": token, "password": "AnotherSecure789"})
        self.assertEqual(context.exception.code, "password_reset_invalid")

    def test_verification_can_be_resent_without_account_enumeration(self) -> None:
        self.accounts.register({
            "email": "resend-user@example.com", "display_name": "Resend User", "password": "SecurePass123",
        })
        first_token = self._token(self.mailer.messages[-1].body)
        self.assertEqual(self.accounts.resend_email_verification({"email": "unknown@example.com"}), {"accepted": True})
        self.assertEqual(len(self.mailer.messages), 1)
        self.assertEqual(self.accounts.resend_email_verification({"email": "resend-user@example.com"}), {"accepted": True})
        self.assertEqual(len(self.mailer.messages), 2)
        with self.assertRaises(AppError) as context:
            self.accounts.verify_email(first_token)
        self.assertEqual(context.exception.code, "email_verification_invalid")

    def test_chatgpt_begin_uses_pkce_and_does_not_persist_secret(self) -> None:
        service = AccountService(
            self.database,
            chatgpt_enabled=True,
            chatgpt_client_id="client-123",
            chatgpt_client_secret="secret-value",
            chatgpt_authorization_url="https://auth.example.test/authorize",
            chatgpt_token_url="https://auth.example.test/token",
            chatgpt_userinfo_url="https://auth.example.test/userinfo",
            chatgpt_redirect_uri="https://skillsentra.example.test/api/v1/auth/chatgpt/callback",
        )
        providers = service.providers()
        self.assertTrue(providers["chatgpt"]["available"])
        authorization_url, state, verifier = service.begin_chatgpt_login()
        query = parse_qs(urlparse(authorization_url).query)
        self.assertEqual(query["state"], [state])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotEqual(query["code_challenge"][0], verifier)
        self.assertNotIn("secret-value", authorization_url)
        with self.database.session() as connection:
            row = connection.execute("SELECT provider, state_hash FROM oauth_login_states WHERE provider='chatgpt'").fetchone()
        self.assertEqual(row["provider"], "chatgpt")
        self.assertNotEqual(row["state_hash"], state)

    def test_smtp_requires_starttls(self) -> None:
        with self.assertRaises(ValueError):
            SMTPEmailDelivery("smtp.example.test", 25, "user", "password", "noreply@example.test", starttls=False)


class ProductionReadinessTests(unittest.TestCase):
    def test_default_local_profile_is_no_go_without_leaking_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            config = AppConfig(root_dir=Path(tempdir), static_dir=Path(tempdir), database_path=Path(tempdir) / "db")
            previous = os.environ.pop("SKILLSENTRA_PASSWORD_PEPPER", None)
            try:
                result = evaluate_production_readiness(config, email_delivery_configured=False)
            finally:
                if previous is not None:
                    os.environ["SKILLSENTRA_PASSWORD_PEPPER"] = previous
            self.assertEqual(result["status"], "no_go")
            self.assertFalse(result["release_authorized"])
            self.assertIn("environment", result["blocking_checks"])
            self.assertIn("database_backend", result["blocking_checks"])
            self.assertNotIn("replace-with", json.dumps(result))
