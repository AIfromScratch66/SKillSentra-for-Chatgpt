from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from app.errors import AppError
from app.evaluation import (
    EVAL_SCHEMA,
    build_template_eval_pack,
    case_input_digest,
    compute_dataset_digest,
    evaluate_static_contract,
    load_eval_pack,
    stable_case_id,
    validate_eval_pack,
)


class EvalPackTests(unittest.TestCase):
    @staticmethod
    def pack() -> dict:
        return build_template_eval_pack(
            skill_name="research-brief-builder",
            trigger="用户需要比较材料并形成决策简报时",
            success="生成结论、来源台账和风险项",
            non_goal="简单翻译和单句润色",
            failure="资料不足时停止并说明缺口。",
            must_not="不得编造来源、案例或数字。",
        )

    @staticmethod
    def skill_text(pack: dict) -> str:
        fragments = [
            fragment
            for case in pack["cases"]
            for fragment in case["contract_fragments"]
        ]
        return "\n\n".join(fragments)

    def test_template_pack_has_stable_ids_digest_splits_and_slices(self) -> None:
        first = self.pack()
        second = self.pack()
        validated = validate_eval_pack(first)

        self.assertEqual(first, second)
        self.assertEqual(validated["schema"], EVAL_SCHEMA)
        self.assertEqual(validated["dataset_digest"], compute_dataset_digest(validated))
        self.assertEqual({item["split"] for item in validated["cases"]}, {"golden", "challenge", "incident"})
        self.assertEqual(len({item["case_id"] for item in validated["cases"]}), 3)
        for case in validated["cases"]:
            self.assertEqual(case["input_digest"], case_input_digest(case["input"]))
            self.assertEqual(case["case_id"], stable_case_id(case))
            self.assertEqual(set(case["slice"]), {"language", "risk", "task_kind"})
            self.assertIn("kind", case["source"])
            self.assertIn("origin", case["provenance"])

    def test_load_rejects_digest_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "cases.json"
            pack = self.pack()
            pack["cases"][0]["input"] = "被篡改但没有重新冻结的输入"
            path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(AppError) as context:
                load_eval_pack(path)

        self.assertEqual(context.exception.code, "eval_pack_tampered")

    def test_missing_split_fails_closed_even_with_recomputed_digest(self) -> None:
        pack = self.pack()
        pack["cases"] = [item for item in pack["cases"] if item["split"] != "incident"]
        pack["dataset_digest"] = compute_dataset_digest(pack)

        with self.assertRaises(AppError) as context:
            validate_eval_pack(pack)

        self.assertEqual(context.exception.code, "eval_split_missing")
        self.assertEqual(context.exception.details["missing"], ["incident"])

    def test_cross_split_normalized_duplicate_is_contamination(self) -> None:
        pack = self.pack()
        golden = next(item for item in pack["cases"] if item["split"] == "golden")
        challenge = next(item for item in pack["cases"] if item["split"] == "challenge")
        challenge["input"] = f"  {golden['input']}  "
        challenge["input_digest"] = case_input_digest(challenge["input"])
        challenge["case_id"] = stable_case_id(challenge)
        pack["dataset_digest"] = compute_dataset_digest(pack)

        with self.assertRaises(AppError) as context:
            validate_eval_pack(pack)

        self.assertEqual(context.exception.code, "eval_contamination_detected")
        self.assertEqual(context.exception.details["splits"], ["challenge", "golden"])

    def test_results_are_deterministic_sliced_and_not_length_scored(self) -> None:
        pack = self.pack()
        text = self.skill_text(pack)
        first = evaluate_static_contract(
            pack,
            skill_text=text,
            artifact_digest="sha256:" + "a" * 64,
            stage="evaluation",
        )
        repeated = evaluate_static_contract(
            pack,
            skill_text=text,
            artifact_digest="sha256:" + "a" * 64,
            stage="evaluation",
        )
        padded = evaluate_static_contract(
            pack,
            skill_text=text + "\n" + ("无关正文。" * 500),
            artifact_digest="sha256:" + "b" * 64,
            stage="evaluation",
        )

        self.assertEqual(first, repeated)
        self.assertEqual(first["score"], padded["score"])
        self.assertEqual(first["score"], 100.0)
        self.assertEqual(first["evidence"]["evidence_kind"], "synthetic_static_contract")
        self.assertEqual(first["evidence"]["evidence_level"], "E2")
        self.assertEqual(first["evidence"]["real_task_performance"], "unknown")
        self.assertEqual(first["evidence"]["dataset"]["selected_splits"], ["golden"])
        self.assertEqual(first["evidence"]["slice_counts"]["task_kind"]["positive_trigger"]["total"], 1)
        self.assertNotEqual(first["evidence"]["result_digest"], padded["evidence"]["result_digest"])

    def test_challenge_contract_failure_blocks_regression(self) -> None:
        pack = self.pack()
        challenge = next(item for item in pack["cases"] if item["split"] == "challenge")
        text = self.skill_text(pack).replace(challenge["contract_fragments"][0], "")

        result = evaluate_static_contract(
            pack,
            skill_text=text,
            artifact_digest="sha256:" + "c" * 64,
            stage="regression",
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["score"], 50.0)
        self.assertEqual(result["evidence"]["dataset"]["selected_splits"], ["challenge", "incident"])
        self.assertEqual([item["case_id"] for item in result["evidence"]["case_results"] if item["status"] == "failed"], [challenge["case_id"]])
        self.assertEqual(result["findings"][0]["code"], "EVAL_CONTRACT_CASE_FAILED")

    def test_a_recomputed_case_change_is_a_new_stable_dataset(self) -> None:
        original = self.pack()
        changed = deepcopy(original)
        incident = next(item for item in changed["cases"] if item["split"] == "incident")
        incident["contract_fragments"] = ["新的失败处理契约"]
        incident["case_id"] = stable_case_id(incident)
        changed["dataset_digest"] = compute_dataset_digest(changed)

        validate_eval_pack(changed)

        self.assertNotEqual(original["dataset_digest"], changed["dataset_digest"])
        self.assertNotEqual(
            next(item for item in original["cases"] if item["split"] == "incident")["case_id"],
            incident["case_id"],
        )


if __name__ == "__main__":
    unittest.main()
