from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.accounts import AccountService
from app.control_plane import ControlPlane
from app.database import Database
from app.errors import AppError
from app.marketplace import MarketplaceService
from app.repository import Repository
from app.skill_engine import SkillEngine


class AccountsMarketplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.database = Database(root / "marketplace.db")
        self.database.migrate()
        self.repository = Repository(self.database)
        self.engine = SkillEngine(self.repository, root / "artifacts", ())
        self.control = ControlPlane(self.database, self.repository, self.engine)
        self.accounts = AccountService(self.database, session_days=2)
        self.marketplace = MarketplaceService(self.database, self.repository, self.control)

        self.creator = self.accounts.register({
            "email": "creator@example.com", "display_name": "Creator One", "password": "SecurePass123",
        })
        self.buyer = self.accounts.register({
            "email": "buyer@example.com", "display_name": "Buyer One", "password": "AnotherPass456",
        })

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _publish(self, pricing_type: str = "paid", price_minor: int = 1299) -> dict:
        project = self.repository.create_project(
            "Traceable Research", "template", self.creator.principal.tenant_id, self.creator.principal.actor_id,
        )
        version = self.repository.create_skill_version(
            project["id"], "1.0.0", f"sha256:{'a' * 64}", {"name": "traceable-research"},
            str(Path(self.tempdir.name) / "artifact"), status="delivered",
        )
        return self.marketplace.publish(self.creator.principal, {
            "version_id": version["id"], "name": "Traceable Research", "slug": "traceable-research",
            "summary": "A test Skill with an immutable delivery digest.", "category": "research",
            "pricing_type": pricing_type, "price_minor": price_minor, "currency": "USD",
            "license_id": "MIT", "compatibility": ["Codex", "Markdown Skill hosts"],
            "permissions": ["Read selected files"], "data_policy": "local_only",
            "support_policy": "maintained",
        })

    def test_registration_login_session_csrf_and_logout(self) -> None:
        authenticated = self.accounts.authenticate(self.creator.session_token)
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.user["email"], "creator@example.com")
        self.accounts.verify_csrf(self.creator.session_token, self.creator.csrf_token)
        with self.assertRaises(AppError) as context:
            self.accounts.verify_csrf(self.creator.session_token, "wrong-csrf-token")
        self.assertEqual(context.exception.code, "csrf_invalid")

        login = self.accounts.login({"email": "CREATOR@example.com", "password": "SecurePass123"})
        self.assertEqual(login.principal.actor_id, self.creator.principal.actor_id)
        with self.assertRaises(AppError) as context:
            self.accounts.login({"email": "creator@example.com", "password": "x" * 5000})
        self.assertEqual(context.exception.code, "login_failed")
        self.accounts.logout(login.session_token)
        self.assertIsNone(self.accounts.authenticate(login.session_token))

    def test_verified_external_identity_creates_then_reuses_one_local_account(self) -> None:
        first = self.accounts._external_login("google", "google-subject-123", "google-user@example.com", "Google User")
        second = self.accounts._external_login("google", "google-subject-123", "google-user@example.com", "Changed Name")
        self.assertEqual(first.user["id"], second.user["id"])
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT provider, email_at_link FROM oauth_identities WHERE provider_subject=?", ("google-subject-123",),
            ).fetchone()
        self.assertEqual(dict(row), {"provider": "google", "email_at_link": "google-user@example.com"})

    def test_provider_diagnostics_name_missing_inputs_without_exposing_values(self) -> None:
        providers = self.accounts.providers()
        google = providers["google"]
        chatgpt = providers["chatgpt"]

        self.assertFalse(google["available"])
        self.assertEqual(google["configuration_status"], "missing_configuration")
        self.assertEqual(
            google["missing_configuration"],
            [
                "SKILLSENTRA_GOOGLE_CLIENT_ID",
                "SKILLSENTRA_GOOGLE_CLIENT_SECRET",
                "SKILLSENTRA_GOOGLE_REDIRECT_URI",
            ],
        )
        self.assertTrue(google["next_steps"])
        secret_field = next(item for item in google["required_configuration"] if item["secret"])
        self.assertEqual(secret_field["field"], "SKILLSENTRA_GOOGLE_CLIENT_SECRET")
        self.assertNotIn("value", secret_field)

        self.assertFalse(chatgpt["available"])
        self.assertEqual(chatgpt["configuration_status"], "external_access_required")
        self.assertIn("external_application_partner_access", chatgpt["missing_configuration"])
        self.assertIn("server_callback_implementation", chatgpt["missing_configuration"])
        self.assertTrue(any("do not simulate" in step for step in chatgpt["next_steps"]))

        configured = AccountService(
            self.database,
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
            google_redirect_uri="https://skillsentra.example.test/api/v1/auth/google/callback",
        ).providers()
        self.assertTrue(configured["google"]["available"])
        self.assertEqual(configured["google"]["configuration_status"], "ready")
        self.assertEqual(configured["google"]["missing_configuration"], [])
        self.assertEqual(configured["google"]["start_url"], "/api/v1/auth/google/start")
        self.assertFalse(configured["chatgpt"]["available"])

    def test_paid_publish_purchase_review_ranking_and_settlement(self) -> None:
        publication = self._publish()
        with self.assertRaises(AppError) as context:
            self.marketplace.publish(self.creator.principal, {
                "version_id": publication["skill_version_id"], "name": "Duplicate Release",
                "slug": "duplicate-release", "summary": "The same immutable version must not be listed twice.",
            })
        self.assertEqual(context.exception.code, "version_already_published")
        purchase = self.marketplace.purchase(
            self.buyer.principal, publication["id"], {"accept_sandbox_charge": True}, "purchase-test-key-0001",
        )
        replay = self.marketplace.purchase(
            self.buyer.principal, publication["id"], {"accept_sandbox_charge": True}, "purchase-test-key-0001",
        )
        review = self.marketplace.review(
            self.buyer.principal, publication["id"], {"rating": 5, "title": "Clear", "body": "Easy to verify."},
        )
        detail = self.marketplace.get_publication(publication["id"])
        ranking = self.marketplace.leaderboard()
        ledger = self.control.ledger(self.creator.principal)

        self.assertFalse(purchase["already_owned"])
        self.assertTrue(replay["already_owned"])
        self.assertEqual(replay["order"]["id"], purchase["order"]["id"])
        self.assertEqual(review["rating"], 5)
        self.assertEqual(detail["review_count"], 1)
        self.assertEqual(detail["average_rating"], 5)
        self.assertEqual(detail["trust_passport"]["status"], "declared")
        self.assertEqual(detail["trust_passport"]["license_id"], "MIT")
        self.assertEqual(detail["trust_passport"]["compatibility"], ["Codex", "Markdown Skill hosts"])
        self.assertEqual(detail["trust_passport"]["permissions"], ["Read selected files"])
        self.assertEqual(detail["trust_passport"]["data_policy"], "local_only")
        self.assertEqual(detail["trust_passport"]["dynamic_safety"], "unknown")
        self.assertEqual(ranking[0]["id"], publication["id"])
        self.assertEqual(ranking[0]["ranking_window_days"], 7)
        self.assertEqual(ranking[0]["review_count"], 1)
        self.assertEqual(ranking[0]["purchase_count"], 1)
        self.assertEqual(ranking[0]["average_rating"], 5)
        self.assertEqual(ranking[0]["lifetime_review_count"], 1)
        self.assertEqual(ranking[0]["lifetime_purchase_count"], 1)
        self.assertTrue(ledger["balanced"])
        self.assertEqual(purchase["order"]["amount_minor"], 1299)
        self.assertEqual(purchase["order"]["publisher_amount_minor"], 1105)

        statement = self.control.create_statement(self.creator.principal, {
            "publisher_id": self.creator.principal.actor_id,
            "cycle_start": "2000-01-01T00:00:00+00:00", "cycle_end": "2100-01-01T00:00:00+00:00",
        })
        replay_statement = self.control.create_statement(self.creator.principal, {
            "publisher_id": self.creator.principal.actor_id,
            "cycle_start": "2000-01-01T00:00:00+00:00", "cycle_end": "2100-01-01T00:00:00+00:00",
        })
        self.assertEqual(statement["id"], replay_statement["id"])
        self.assertEqual(statement["total_gross_minor"], 1299)
        self.assertEqual(statement["total_payable_minor"], 1105)
        self.assertEqual(statement["line_items"][0]["source_type"], "marketplace_order")
        with self.assertRaises(AppError) as context:
            self.control.create_statement(self.creator.principal, {
                "publisher_id": self.creator.principal.actor_id,
                "cycle_start": "1999-01-01T00:00:00+00:00", "cycle_end": "2101-01-01T00:00:00+00:00",
            })
        self.assertEqual(context.exception.code, "statement_empty")

    def test_free_skill_needs_no_charge_and_only_owner_can_publish(self) -> None:
        publication = self._publish("free", 999)
        purchase = self.marketplace.purchase(
            self.buyer.principal, publication["id"], {}, "free-purchase-key-0001",
        )
        self.assertEqual(publication["price_minor"], 0)
        self.assertEqual(purchase["order"]["amount_minor"], 0)
        self.assertEqual(len(self.marketplace.library(self.buyer.principal)), 1)

        project = self.repository.create_project(
            "Buyer Skill", "template", self.buyer.principal.tenant_id, self.buyer.principal.actor_id,
        )
        version = self.repository.create_skill_version(
            project["id"], "1.0.0", f"sha256:{'b' * 64}", {}, "artifact", status="delivered",
        )
        with self.assertRaises(AppError) as context:
            self.marketplace.publish(self.creator.principal, {
                "version_id": version["id"], "summary": "Cross tenant publication must not be possible.",
            })
        self.assertEqual(context.exception.code, "version_not_found")

    def test_curated_seed_exposes_static_only_trust_passport_without_duplicates(self) -> None:
        root = Path(__file__).resolve().parents[1] / "sample-skills" / "professional-report-writing"
        scan = self.engine.scan_path(root)
        entry = {"root": str(root), "scan": scan}

        self.marketplace.seed_curated_catalog([entry])
        self.marketplace.seed_curated_catalog([entry])

        publications = [item for item in self.marketplace.list_publications() if item["publisher_name"] == "SkillSentra Curated"]
        self.assertEqual(len(publications), 1)
        detail = self.marketplace.get_publication(publications[0]["id"])
        passport = detail["trust_passport"]
        self.assertEqual(passport["status"], "static_verified")
        self.assertEqual(passport["curation_status"], "curated")
        self.assertEqual(passport["gate_status"], "passed")
        self.assertEqual(passport["dynamic_safety"], "unknown")
        self.assertEqual(passport["evidence"][0]["stage"], "static")
        self.assertEqual(passport["evidence"][0]["status"], "passed")
        self.assertEqual(self.marketplace.leaderboard(), [])

    def test_leaderboard_excludes_signals_older_than_seven_days(self) -> None:
        publication = self._publish()
        self.marketplace.purchase(
            self.buyer.principal, publication["id"], {"accept_sandbox_charge": True}, "old-purchase-key-0001",
        )
        self.marketplace.review(
            self.buyer.principal, publication["id"], {"rating": 5, "title": "Historical", "body": "Old signal."},
        )
        stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="milliseconds")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE marketplace_orders SET created_at=? WHERE publication_id=?",
                (stale, publication["id"]),
            )
            connection.execute(
                "UPDATE marketplace_reviews SET created_at=?, updated_at=? WHERE publication_id=?",
                (stale, stale, publication["id"]),
            )
            connection.execute(
                "UPDATE marketplace_publications SET curation_status='curated' WHERE id=?",
                (publication["id"],),
            )

        detail = self.marketplace.get_publication(publication["id"])
        self.assertEqual(detail["review_count"], 1)
        self.assertEqual(detail["purchase_count"], 1)
        self.assertEqual(self.marketplace.leaderboard(), [])

    def test_editing_an_old_review_does_not_refresh_its_weekly_ranking_signal(self) -> None:
        publication = self._publish()
        self.marketplace.purchase(
            self.buyer.principal, publication["id"], {"accept_sandbox_charge": True}, "old-edit-key-0001",
        )
        self.marketplace.review(
            self.buyer.principal, publication["id"], {"rating": 2, "title": "Historical", "body": "Old signal."},
        )
        stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="milliseconds")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE marketplace_orders SET created_at=? WHERE publication_id=?",
                (stale, publication["id"]),
            )
            connection.execute(
                "UPDATE marketplace_reviews SET created_at=?, updated_at=? WHERE publication_id=?",
                (stale, stale, publication["id"]),
            )

        updated = self.marketplace.review(
            self.buyer.principal, publication["id"], {"rating": 5, "title": "Edited", "body": "Updated text."},
        )
        self.assertEqual(updated["created_at"], stale)
        self.assertNotEqual(updated["updated_at"], stale)
        self.assertEqual(self.marketplace.leaderboard(), [])


if __name__ == "__main__":
    unittest.main()
