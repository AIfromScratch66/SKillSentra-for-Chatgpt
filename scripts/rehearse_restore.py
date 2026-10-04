from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.control_plane import ControlPlane, Principal
from app.database import Database
from app.repository import Repository
from app.skill_engine import SkillEngine


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def service_for(path: Path, artifact_root: Path) -> tuple[Database, ControlPlane]:
    database = Database(path)
    database.migrate()
    repository = Repository(database)
    engine = SkillEngine(repository, artifact_root, (PROJECT_ROOT / "sample-skills",))
    return database, ControlPlane(database, repository, engine)


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="skillsentra-restore-") as temp_name:
        root = Path(temp_name)
        source_path = root / "source.db"
        backup_path = root / "backup.db"
        source_db, source_service = service_for(source_path, root / "source-artifacts")
        # Synthetic release verification runs against the explicitly granted
        # local demo workspace. Non-demo tenants intentionally cannot inherit
        # server-local Skill roots after tenant isolation.
        principal = Principal("ten_demo", "restore-rehearsal", frozenset({"admin", "operator", "security", "finance"}))
        fixture = source_service.run_demo_path(principal)
        expected_digest = fixture["snapshot"]["artifact_digest"]

        target = sqlite3.connect(backup_path)
        try:
            with source_db.session() as source:
                source.backup(target)
            target.commit()
        finally:
            target.close()
        backup_digest = sha256(backup_path)

        restored_db, restored_service = service_for(backup_path, root / "restored-artifacts")
        with restored_db.session() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            stored_digest = connection.execute(
                "SELECT artifact_digest FROM repository_snapshots WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1",
                (principal.tenant_id,),
            ).fetchone()[0]
        ledger = restored_service.ledger(principal)
        audit = restored_service.list_audit(principal)
        overview = restored_service.overview(principal)
        checks = {
            "sqlite_integrity": integrity == "ok",
            "artifact_identity_preserved": stored_digest == expected_digest,
            "ledger_balanced": ledger["balanced"] is True,
            "audit_chain_valid": audit["integrity"]["valid"] is True,
            "qualified_receipt_preserved": overview["counts"]["qualified_receipts"] == 1,
        }
        return {
            "profile": "local-synthetic-single-node",
            "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "backup_sha256": backup_digest,
            "checks": checks,
            "passed": all(checks.values()),
            "note": "Synthetic SQLite recovery rehearsal; production encrypted-backup and operator RPO/RTO evidence is still required.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rehearse a local SkillSentra SQLite backup and restore")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
