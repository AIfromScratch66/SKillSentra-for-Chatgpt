from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import Database, json_dumps, utc_now
from app.errors import AppError
from app.expert_matrix import DIMENSIONS, FRAMEWORK_VERSION, GATES, expert_framework, validate_expert_review
from app.repository import Repository


def review_payload() -> dict:
    return {
        "framework_version": FRAMEWORK_VERSION,
        "review_kind": "internal_simulation",
        "reviewer_label": "Codex internal ten-seat matrix",
        "artifact_digest": "sha256:" + "a" * 64,
        "evaluation_world": {
            "skill_version": "0.6.0-rc.1",
            "host_version": "Codex desktop 2026-08-28",
            "adapter_version": "skillsentra-mcp/0.6.0",
            "model": "gpt-test",
            "installed_skill_set": ["skillsentra@sha256:fixture"],
            "tool_permissions": ["local-http-read", "project-review-write"],
            "dataset_id": "skillsentra-regression",
            "dataset_version": "fixture-v1",
            "grader_version": "expert-matrix-v1",
            "executed_at": "2026-08-28T00:00:00Z",
            "external_dependencies": [],
        },
        "dimensions": {
            item["id"]: {
                "score": 10,
                "evidence_level": "E2",
                "evidence_refs": [f"tests::{item['id']}"],
                "note": "自动化证据不能代替真实宿主或真实用户结果。",
            }
            for item in DIMENSIONS
        },
        "gates": {
            item["id"]: {
                "status": "pass" if item["id"] in {"critical_safety", "provenance_license"} else "unknown",
                "evidence_level": "E2" if item["id"] in {"critical_safety", "provenance_license"} else "E0",
                "evidence_refs": [f"tests::gate::{item['id']}"] if item["id"] in {"critical_safety", "provenance_license"} else [],
                "note": "fixture",
            }
            for item in GATES
        },
        "findings": ["真实 ChatGPT 宿主联通仍为 Unknown。"],
        "proposed_changes": ["完成非 Mock 宿主调用并回读关联 ID。"],
    }


class ExpertMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "expert.db")
        self.database.migrate()
        self.repository = Repository(self.database)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_version(self, project_id: str, digest: str = "sha256:" + "a" * 64) -> dict:
        return self.repository.create_skill_version(
            project_id,
            "0.6.0-rc.1",
            digest,
            {"files": [{"path": "SKILL.md"}]},
            str(Path(self.tempdir.name) / "artifact"),
        )

    @staticmethod
    def direct_insert_review(connection, project_id: str, version_id: str, review: dict, review_id: str) -> None:
        connection.execute(
            """
            INSERT INTO expert_reviews(
                id, project_id, framework_version, review_kind, reviewer_label,
                artifact_digest, artifact_version_id, raw_score, capped_score, decision, evidence_ceiling,
                evaluation_world_json, dimensions_json, gates_json, findings_json,
                proposed_changes_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                project_id,
                review["framework_version"],
                review["review_kind"],
                review["reviewer_label"],
                review["artifact_digest"],
                version_id,
                review["raw_score"],
                review["capped_score"],
                review["decision"],
                review["evidence_ceiling"],
                json_dumps(review["evaluation_world"]),
                json_dumps(review["dimensions"]),
                json_dumps(review["gates"]),
                json_dumps(review["findings"]),
                json_dumps(review["proposed_changes"]),
                utc_now(),
            ),
        )

    def test_framework_is_versioned_weighted_and_explicitly_simulated(self) -> None:
        framework = expert_framework()

        self.assertEqual(framework["version"], FRAMEWORK_VERSION)
        self.assertEqual(sum(item["weight"] for item in framework["dimensions"]), 100)
        self.assertEqual(len(framework["roles"]), 10)
        self.assertIn("内部模拟", framework["disclaimer"])
        self.assertEqual(framework["external_evidence_registry"]["status"], "unavailable")
        self.assertFalse(framework["external_evidence_registry"]["accepts_client_attestations"])

    def test_unknown_real_host_gate_caps_perfect_raw_score(self) -> None:
        normalized = validate_expert_review(review_payload())

        self.assertEqual(normalized["raw_score"], 10.0)
        self.assertEqual(normalized["capped_score"], 7.9)
        self.assertEqual(normalized["decision"], "NO_GO")
        self.assertEqual(normalized["evidence_ceiling"], "E2")

    def test_review_is_bound_to_digest_and_round_trips_with_audit(self) -> None:
        project = self.repository.create_project("专家矩阵", "template")
        version = self.create_version(project["id"])
        stored = self.repository.create_expert_review(project["id"], validate_expert_review(review_payload()))
        restored = self.repository.list_expert_reviews(project["id"])
        actions = {item["action"] for item in self.repository.list_audit_events(project["id"])}

        self.assertEqual(stored["artifact_digest"], "sha256:" + "a" * 64)
        self.assertEqual(stored["artifact_version_id"], version["id"])
        self.assertEqual(restored[0]["dimensions"]["product_strategy"]["evidence_level"], "E2")
        self.assertIn("expert_review.recorded", actions)

    def test_invalid_digest_is_rejected(self) -> None:
        payload = review_payload()
        payload["artifact_digest"] = "latest"

        with self.assertRaises(AppError) as context:
            validate_expert_review(payload)

        self.assertEqual(context.exception.code, "invalid_artifact_digest")

    def test_review_without_project_version_is_rejected(self) -> None:
        project = self.repository.create_project("无版本项目", "template")

        with self.assertRaises(AppError) as context:
            self.repository.create_expert_review(project["id"], validate_expert_review(review_payload()))

        self.assertEqual(context.exception.code, "expert_artifact_version_required")

    def test_arbitrary_digest_not_owned_by_project_is_rejected(self) -> None:
        project = self.repository.create_project("摘要错配项目", "template")
        self.create_version(project["id"], "sha256:" + "b" * 64)

        with self.assertRaises(AppError) as context:
            self.repository.create_expert_review(project["id"], validate_expert_review(review_payload()))

        self.assertEqual(context.exception.code, "expert_artifact_digest_mismatch")

    def test_real_host_gate_rejects_fabricated_client_e4_evidence(self) -> None:
        payload = review_payload()
        payload["gates"]["real_host_connection"] = {
            "status": "pass",
            "evidence_level": "E4",
            "evidence_refs": ["claim://fabricated-host-connection"],
        }

        with self.assertRaises(AppError) as context:
            validate_expert_review(payload)

        self.assertEqual(context.exception.code, "external_evidence_unverified")

    def test_local_e2_gate_pass_requires_nonempty_reference(self) -> None:
        payload = review_payload()
        payload["gates"]["critical_safety"]["evidence_refs"] = []

        with self.assertRaises(AppError) as context:
            validate_expert_review(payload)

        self.assertEqual(context.exception.code, "evidence_required")

    def test_repository_cannot_store_ready_decision_without_external_registry(self) -> None:
        project = self.repository.create_project("伪造 READY 项目", "template")
        self.create_version(project["id"])
        normalized = validate_expert_review(review_payload())
        normalized["decision"] = "READY_FOR_HUMAN_DECISION"

        with self.assertRaises(AppError) as context:
            self.repository.create_expert_review(project["id"], normalized)

        self.assertEqual(context.exception.code, "external_evidence_unverified")

    def test_database_rejects_direct_insert_ready_and_fabricated_external_pass(self) -> None:
        project = self.repository.create_project("数据库直写攻击", "template")
        version = self.create_version(project["id"])
        normalized = validate_expert_review(review_payload())

        ready = dict(normalized)
        ready["decision"] = "READY_FOR_HUMAN_DECISION"
        with self.assertRaisesRegex(sqlite3.IntegrityError, "external_evidence_registry_unavailable"):
            with self.database.transaction() as connection:
                self.direct_insert_review(connection, project["id"], version["id"], ready, "xrv_direct_ready")

        external_gates = (
            "real_host_connection",
            "real_task_evidence",
            "manual_accessibility",
            "human_release_authorization",
        )
        for index, gate_id in enumerate(external_gates):
            with self.subTest(gate_id=gate_id):
                fabricated = dict(normalized)
                fabricated["gates"] = {key: dict(value) for key, value in normalized["gates"].items()}
                fabricated["gates"][gate_id] = {
                    "status": "pass",
                    "evidence_level": "E5",
                    "evidence_refs": [f"claim://fabricated-{gate_id}"],
                    "note": "forged",
                    "score_cap": None,
                }
                with self.assertRaisesRegex(sqlite3.IntegrityError, "external_evidence_registry_unavailable"):
                    with self.database.transaction() as connection:
                        self.direct_insert_review(
                            connection,
                            project["id"],
                            version["id"],
                            fabricated,
                            f"xrv_direct_e5_{index}",
                        )

    def test_database_rejects_direct_update_but_allows_no_go_and_iterate(self) -> None:
        project = self.repository.create_project("数据库更新攻击", "template")
        version = self.create_version(project["id"])
        normalized = validate_expert_review(review_payload())
        stored = self.repository.create_expert_review(project["id"], normalized)

        iterate = dict(normalized)
        iterate["decision"] = "ITERATE"
        with self.database.transaction() as connection:
            self.direct_insert_review(connection, project["id"], version["id"], iterate, "xrv_direct_iterate")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "external_evidence_registry_unavailable"):
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE expert_reviews SET decision = 'READY_FOR_HUMAN_DECISION' WHERE id = ?",
                    (stored["id"],),
                )

        forged_gates = {key: dict(value) for key, value in normalized["gates"].items()}
        forged_gates["human_release_authorization"] = {
            "status": "pass",
            "evidence_level": "E5",
            "evidence_refs": ["claim://fabricated-human-release"],
        }
        with self.assertRaisesRegex(sqlite3.IntegrityError, "external_evidence_registry_unavailable"):
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE expert_reviews SET gates_json = ? WHERE id = ?",
                    (json_dumps(forged_gates), stored["id"]),
                )

        restored = self.repository.get_expert_review(stored["id"])
        iterate_stored = self.repository.get_expert_review("xrv_direct_iterate")
        self.assertEqual(restored["decision"], "NO_GO")
        self.assertEqual(restored["gates"]["human_release_authorization"]["status"], "unknown")
        self.assertEqual(iterate_stored["decision"], "ITERATE")


if __name__ == "__main__":
    unittest.main()
