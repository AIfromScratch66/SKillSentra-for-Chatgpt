from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.control_plane import ControlPlane, Principal
from app.database import Database, utc_now
from app.repository import Repository
from app.skill_engine import SkillEngine


def distribution(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "min_ms": round(ordered[0], 4),
        "p50_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 4),
        "max_ms": round(ordered[-1], 4),
        "mean_ms": round(statistics.fmean(ordered), 4),
    }


def measure(iterations: int, operation: object) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()  # type: ignore[operator]
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return samples


def probe_database_session(database: Database) -> None:
    """Measure connection setup separately from an in-transaction decision."""

    with database.session() as connection:
        connection.execute("SELECT 1").fetchone()


def run(iterations: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="skillsentra-benchmark-") as temp_name:
        setup_started = time.perf_counter_ns()
        root = Path(temp_name)
        database = Database(root / "benchmark.db")
        database.migrate()
        repository = Repository(database)
        engine = SkillEngine(repository, root / "artifacts", (PROJECT_ROOT / "sample-skills",))
        service = ControlPlane(database, repository, engine)
        # Synthetic release verification runs against the explicitly granted
        # local demo workspace. Non-demo tenants intentionally cannot inherit
        # server-local Skill roots after tenant isolation.
        principal = Principal("ten_demo", "benchmark", frozenset({"admin", "operator", "security", "finance"}))
        fixture = service.run_demo_path(principal)
        deployment = fixture["deployment"]
        digest = fixture["snapshot"]["artifact_digest"]
        setup_ms = (time.perf_counter_ns() - setup_started) / 1_000_000
        overview = measure(iterations, lambda: service.overview(principal))
        database_session = measure(iterations, lambda: probe_database_session(database))
        # Receipt qualification executes inside an already-open BEGIN IMMEDIATE
        # transaction. Report SQLite session setup independently instead of
        # charging cold connection creation to every policy decision.
        with database.transaction() as qualification_connection:
            if not qualification_connection.in_transaction:
                raise RuntimeError("qualification benchmark requires an active SQLite transaction")
            qualification = measure(
                iterations * 4,
                lambda: service._qualification_reason(
                    principal.tenant_id,
                    deployment,
                    digest,
                    "E3",
                    utc_now(),
                    connection=qualification_connection,
                ),
            )
        receipt_sequence = 0

        def ingest_unique_receipt() -> None:
            nonlocal receipt_sequence
            receipt_sequence += 1
            service.ingest_receipt(
                principal,
                {
                    "receipt_id": f"benchmark-receipt-{receipt_sequence:06d}",
                    "publisher_id": fixture["entitlement"]["publisher_id"],
                    "agent_id": fixture["agent"]["id"],
                    "deployment_id": deployment["id"],
                    "run_id": f"benchmark-run-{receipt_sequence:06d}",
                    "artifact_digest": digest,
                    "metric": "verified_run",
                    "unit": "run",
                    "quantity": 1,
                    "policy_version": deployment["policy_version"],
                    "evidence_level": "E3",
                    "signature": "benchmark-signature-not-production",
                    "occurred_at": utc_now(),
                },
            )

        receipt_iterations = max(20, min(iterations, 100))
        receipt_ingest = measure(receipt_iterations, ingest_unique_receipt)
        ledger = measure(max(20, iterations // 4), lambda: service.ledger(principal))
        qualification_distribution = distribution(qualification)
        return {
            "profile": "single-node-private-beta",
            "iterations": iterations,
            "setup": {
                "database_migration_and_fixture_ms": round(setup_ms, 4),
                "included_in_operation_samples": False,
            },
            "overview": distribution(overview),
            "database_session_open": distribution(database_session),
            "policy_qualification": qualification_distribution,
            "policy_qualification_scope": "decision inside an already-open receipt transaction",
            "policy_qualification_transaction": "BEGIN IMMEDIATE",
            "receipt_ingest": distribution(receipt_ingest),
            "receipt_ingest_iterations": receipt_iterations,
            "receipt_ingest_scope": (
                "unique qualified receipt including validation, SQLite transaction, entitlement and pricing lookup, "
                "journal posting, audit append, and persisted response read"
            ),
            "ledger_read": distribution(ledger),
            "targets": {"policy_qualification_p95_ms": 20.0},
            "target_met": qualification_distribution["p95_ms"] < 20.0,
            "note": (
                "Local synthetic measurement; database migration and fixture setup are excluded, "
                "SQLite session-open cost and full receipt ingestion are reported separately. "
                "This is not a production capacity claim."
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure local SkillSentra control-plane latency")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(max(20, min(args.iterations, 5_000)))
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
