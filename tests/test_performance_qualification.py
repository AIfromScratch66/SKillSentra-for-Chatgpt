from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.control_plane import ControlPlane, Principal
from app.database import Database, utc_now
from app.repository import Repository
from app.skill_engine import SkillEngine
from scripts.benchmark_control_plane import run as run_benchmark


class QualificationPerformanceTests(unittest.TestCase):
    def make_service(self, root: Path) -> tuple[Database, ControlPlane, Principal]:
        database = Database(root / "qualification.db")
        database.migrate()
        repository = Repository(database)
        engine = SkillEngine(repository, root / "artifacts", ())
        service = ControlPlane(database, repository, engine)
        principal = Principal(
            "ten_perf",
            "performance-test",
            frozenset({"admin", "operator", "security", "finance"}),
        )
        service.ensure_tenant(principal)
        return database, service, principal

    def test_qualification_reuses_provided_connection(self) -> None:
        digest = "sha256:" + "a" * 64
        deployment = {"status": "observed", "observed_digest": digest}
        with tempfile.TemporaryDirectory() as temp_name:
            database, service, principal = self.make_service(Path(temp_name))
            with database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO revocations(
                        id, tenant_id, artifact_digest, reason, severity, status,
                        actor_id, created_at
                    ) VALUES ('rev_perf', ?, ?, 'performance fixture', 'high', 'active', ?, ?)
                    """,
                    (principal.tenant_id, digest, principal.actor_id, utc_now()),
                )
            with database.session() as connection:
                with patch.object(
                    database,
                    "session",
                    side_effect=AssertionError("qualification opened a second database session"),
                ):
                    reason = service._qualification_reason(
                        principal.tenant_id,
                        deployment,
                        digest,
                        "E3",
                        utc_now(),
                        connection=connection,
                    )

        self.assertEqual(reason, "artifact_revoked")

    def test_receipt_qualification_uses_receipt_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database = Database(root / "receipt.db")
            database.migrate()
            repository = Repository(database)
            engine = SkillEngine(
                repository,
                root / "artifacts",
                (Path(__file__).resolve().parents[1] / "sample-skills",),
            )
            service = ControlPlane(database, repository, engine)
            principal = Principal(
                "ten_demo",
                "performance-test",
                frozenset({"admin", "operator", "security", "finance"}),
            )
            fixture = service.run_demo_path(principal)
            observed_connections: list[object | None] = []
            original = service._qualification_reason

            def track_connection(*args: object, **kwargs: object) -> str:
                observed_connections.append(kwargs.get("connection"))
                return original(*args, **kwargs)  # type: ignore[arg-type]

            with patch.object(service, "_qualification_reason", side_effect=track_connection):
                receipt = service.ingest_receipt(
                    principal,
                    {
                        "receipt_id": "performance-receipt-002",
                        "publisher_id": fixture["entitlement"]["publisher_id"],
                        "agent_id": fixture["agent"]["id"],
                        "deployment_id": fixture["deployment"]["id"],
                        "run_id": "performance-run-002",
                        "artifact_digest": fixture["snapshot"]["artifact_digest"],
                        "metric": "verified_run",
                        "unit": "run",
                        "quantity": 1,
                        "policy_version": fixture["deployment"]["policy_version"],
                        "evidence_level": "E3",
                        "signature": "performance-fixture-signature",
                        "occurred_at": utc_now(),
                    },
                )

        self.assertEqual(receipt["status"], "qualified")
        self.assertEqual(len(observed_connections), 1)
        self.assertIsNotNone(observed_connections[0])

    def test_receipt_qualification_refreshes_deployment_inside_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            database = Database(root / "stale-deployment.db")
            database.migrate()
            repository = Repository(database)
            engine = SkillEngine(
                repository,
                root / "artifacts",
                (Path(__file__).resolve().parents[1] / "sample-skills",),
            )
            service = ControlPlane(database, repository, engine)
            principal = Principal(
                "ten_demo",
                "performance-test",
                frozenset({"admin", "operator", "security", "finance"}),
            )
            fixture = service.run_demo_path(principal)
            original = service._tenant_record
            injected = False

            def return_stale_deployment(table: str, record_id: str, tenant_id: str) -> dict[str, object]:
                nonlocal injected
                record = original(table, record_id, tenant_id)
                if table == "deployments" and not injected:
                    injected = True
                    with database.transaction() as connection:
                        connection.execute(
                            "UPDATE deployments SET status='drifted', observed_digest=? "
                            "WHERE id=? AND tenant_id=?",
                            ("sha256:" + "f" * 64, record_id, tenant_id),
                        )
                return record

            with patch.object(service, "_tenant_record", side_effect=return_stale_deployment):
                receipt = service.ingest_receipt(
                    principal,
                    {
                        "receipt_id": "stale-deployment-race",
                        "publisher_id": fixture["entitlement"]["publisher_id"],
                        "agent_id": fixture["agent"]["id"],
                        "deployment_id": fixture["deployment"]["id"],
                        "run_id": "stale-deployment-run",
                        "artifact_digest": fixture["snapshot"]["artifact_digest"],
                        "metric": "verified_run",
                        "unit": "run",
                        "quantity": 1,
                        "policy_version": fixture["deployment"]["policy_version"],
                        "evidence_level": "E3",
                        "signature": "performance-fixture-signature",
                        "occurred_at": utc_now(),
                    },
                )

        self.assertTrue(injected)
        self.assertEqual(receipt["status"], "excluded")
        self.assertEqual(receipt["exclusion_reason"], "deployment_not_observed")

    def test_benchmark_separates_setup_and_session_open_cost(self) -> None:
        result = run_benchmark(20)

        self.assertFalse(result["setup"]["included_in_operation_samples"])
        self.assertIn("database_session_open", result)
        self.assertEqual(
            result["policy_qualification_scope"],
            "decision inside an already-open receipt transaction",
        )
        self.assertEqual(result["policy_qualification_transaction"], "BEGIN IMMEDIATE")
        self.assertIn("p95_ms", result["policy_qualification"])
        self.assertEqual(result["receipt_ingest_iterations"], 20)
        self.assertIn("journal posting", result["receipt_ingest_scope"])
        self.assertIn("p95_ms", result["receipt_ingest"])


if __name__ == "__main__":
    unittest.main()
