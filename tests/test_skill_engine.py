from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from app.database import Database
from app.errors import AppError
from app.repository import Repository
from app.skill_engine import CANONICALIZATION_VERSION, SkillEngine, canonical_manifest


class SkillEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source_root = self.root / "allowed-skills"
        self.source_root.mkdir()
        self.artifact_root = self.root / "artifacts"
        database = Database(self.root / "engine.db")
        database.migrate()
        self.repository = Repository(database)
        self.engine = SkillEngine(self.repository, self.artifact_root, (self.source_root,))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_source(self, name: str = "existing-skill") -> Path:
        root = self.source_root / name
        root.mkdir()
        (root / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Use when a user needs a complete evidence-bound professional analysis and delivery review.\n"
            "---\n\n"
            f"# {name}\n\n"
            "## Method\n\n"
            "1. Confirm the goal and evidence boundary.\n"
            "2. Separate facts, analysis and recommendations.\n"
            "3. Verify the output before delivery.\n\n"
            "## Safety\n\n"
            "Stop when authority or evidence is missing. Never invent sources or numbers.\n",
            encoding="utf-8",
        )
        return root

    @staticmethod
    def _template_payload() -> dict:
        return {
            "skillName": "research-brief-builder",
            "audience": "研究员与决策支持人员",
            "goal": "把分散材料整理为有来源且可复核的决策简报。",
            "trigger": "用户需要比较材料并形成决策简报时",
            "success": "生成结论、来源台账和风险项",
            "nonGoal": "简单翻译和单句润色",
            "method": "确认目标和证据范围，提取事实，区分判断，复核后交付。",
            "flows": ["确认输入边界", "提取并核对证据", "形成结论并交付"],
            "failure": "资料不足时停止并说明缺口。",
            "mustDo": "事实必须对应来源。",
            "mustNot": "不得编造来源、案例或数字。",
            "dimensions": [
                {"name": "结构", "detail": "结构完整", "weight": 50, "on": True},
                {"name": "安全", "detail": "没有危险指令", "weight": 50, "on": True},
            ],
            "proposals": [{"id": "negative", "title": "补充负触发", "copy": "避免简单翻译误触发。", "decision": "accept"}],
        }

    def test_template_candidate_validation_and_delivery_are_real_artifacts(self) -> None:
        project = self.repository.create_project("research-brief-builder", "template")
        self.repository.save_step(project["id"], "template", 0, self._template_payload(), "draft")

        generated = self.engine.generate_candidate(project["id"])
        evaluation = self.engine.validate_project(project["id"], "evaluation")
        regression = self.engine.validate_project(project["id"], "regression")
        delivered = self.engine.deliver(project["id"], generated["version"]["id"])

        self.assertEqual(generated["validation"]["status"], "passed")
        self.assertEqual(evaluation["status"], "passed")
        self.assertEqual(regression["status"], "passed")
        self.assertEqual(delivered["status"], "delivered")
        self.assertEqual(delivered["canonicalization_version"], CANONICALIZATION_VERSION)
        package = Path(delivered["package_path"])
        self.assertTrue(package.is_file())
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                [
                    "SKILL.md",
                    "evals/cases.json",
                    "references/evaluation-rubric.md",
                    "references/interaction-brief.md",
                    "references/source-rules.md",
                ],
            )
            skill_text = archive.read("SKILL.md").decode("utf-8")
            source_rules = archive.read("references/source-rules.md").decode("utf-8")
            cases = archive.read("evals/cases.json").decode("utf-8")
            self.assertIn("已接受改进", skill_text)
            self.assertIn("SkillSentra 协作上下文", skill_text)
            self.assertIn("Mock token", source_rules)
            self.assertIn("SkillSentra 的结构化步骤", archive.read("references/interaction-brief.md").decode("utf-8"))
        self.assertFalse(any(path.suffix in {".py", ".js", ".ps1", ".sh"} for path in Path(delivered["artifact_path"]).rglob("*")))
    def test_template_candidate_includes_skillsentra_interaction_context(self) -> None:
        project = self.repository.create_project("interaction-rich-skill", "template")
        self.repository.save_step(project["id"], "template", 0, self._template_payload(), "draft")
        self.repository.save_step(
            project["id"],
            "template",
            1,
            {
                "_ai": {
                    "text": "同类 Skill 对比建议：保留来源支持、负触发、人工确认和回滚说明。",
                    "decision": "accepted",
                    "source": "host",
                }
            },
            "complete",
        )

        generated = self.engine.generate_candidate(project["id"])
        artifact_path = Path(generated["version"]["artifact_path"])
        skill_text = (artifact_path / "SKILL.md").read_text(encoding="utf-8")
        interaction = (artifact_path / "references" / "interaction-brief.md").read_text(encoding="utf-8")

        self.assertNotIn("本轮交互提炼的执行重点", skill_text)
        self.assertIn("尚未应用", interaction)
        self.assertIn("同类 Skill 对比建议", interaction)
        self.assertFalse((artifact_path / "references" / "patent-workflow.md").exists())

    def test_patent_template_candidate_includes_workflow_and_boundary_cases(self) -> None:
        project = self.repository.create_project("专利写作大师V1.0", "template")
        payload = self._template_payload()
        payload.update(
            {
                "skillName": "专利写作大师V1.0",
                "goal": "根据技术交底书生成中国专利申请草案、权利要求书、说明书和附图说明。",
                "trigger": "用户要求专利文件编写、权利要求书或说明书时",
                "success": "生成专利申请草案、权利要求书、说明书、来源支持表和人工核对清单",
                "nonGoal": "普通论文润色、简单翻译和商业计划书",
                "mustDo": "必须区分来源证据、模型推断和人工复核边界。",
                "mustNot": "不得编造来源、案例、数字或未披露技术效果。",
            }
        )
        self.repository.save_step(project["id"], "template", 0, payload, "draft")

        generated = self.engine.generate_candidate(project["id"])
        artifact_path = Path(generated["version"]["artifact_path"])
        skill_text = (artifact_path / "SKILL.md").read_text(encoding="utf-8")
        cases = (artifact_path / "evals" / "cases.json").read_text(encoding="utf-8")

        self.assertIn("专利硬边界", skill_text)
        self.assertIn("支持关系表", (artifact_path / "references" / "patent-workflow.md").read_text(encoding="utf-8"))
        self.assertIn("保证新颖性", cases)
        self.assertEqual(self.engine.validate_project(project["id"], "evaluation", generated["version"]["id"])["status"], "passed")
        self.assertEqual(self.engine.validate_project(project["id"], "regression", generated["version"]["id"])["status"], "passed")

    def test_revalidated_candidate_delivery_requires_same_version_evidence(self) -> None:
        project = self.repository.create_project("version-bound-delivery", "template")
        self.repository.save_step(project["id"], "template", 0, self._template_payload(), "draft")

        first = self.engine.generate_candidate(project["id"])
        first_evaluation = self.engine.validate_project(project["id"], "evaluation", first["version"]["id"])
        second = self.engine.generate_candidate(project["id"])
        second_regression = self.engine.validate_project(project["id"], "regression", second["version"]["id"])

        self.assertEqual(first_evaluation["version_id"], first["version"]["id"])
        self.assertEqual(second_regression["version_id"], second["version"]["id"])
        with self.assertRaises(AppError) as blocked:
            self.engine.deliver(project["id"], second["version"]["id"])
        self.assertEqual(blocked.exception.code, "delivery_gate_failed")
        self.assertEqual(blocked.exception.details["stage"], "evaluation")

        second_evaluation = self.engine.validate_project(project["id"], "evaluation", second["version"]["id"])
        delivered = self.engine.deliver(project["id"], second["version"]["id"])
        self.assertEqual(second_evaluation["version_id"], second["version"]["id"])
        self.assertEqual(delivered["status"], "delivered")

    def test_existing_skill_uses_frozen_baseline_and_detects_three_way_conflict(self) -> None:
        source_root = self._write_source()
        original = (source_root / "SKILL.md").read_text(encoding="utf-8")
        source = self.engine.register_source(source_root)
        project = self.repository.create_project("改进现有 Skill", "existing")
        attached = self.engine.attach_source(project["id"], source["id"])
        payload = {
            "proposals": [
                {"id": "scope", "title": "收紧触发范围", "copy": "只处理完整专业分析，不处理简单改写。", "decision": "accept"}
            ]
        }
        self.repository.save_step(project["id"], "existing", 2, payload, "draft")
        baseline = self.engine.validate_project(project["id"], "baseline")
        candidate = self.engine.generate_candidate(project["id"])
        regression = self.engine.validate_project(project["id"], "regression")

        self.assertEqual(attached["baseline"]["artifact_digest"], source["artifact_digest"])
        self.assertEqual(baseline["status"], "passed")
        self.assertEqual(candidate["validation"]["status"], "passed")
        self.assertEqual(regression["status"], "passed")
        self.assertEqual((source_root / "SKILL.md").read_text(encoding="utf-8"), original)
        candidate_text = (Path(candidate["version"]["artifact_path"]) / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("SkillSentra 已接受改进", candidate_text)

        (source_root / "SKILL.md").write_text(original + "\n## Upstream\n\nChanged upstream.\n", encoding="utf-8")
        update = self.engine.check_updates(project["id"])

        self.assertEqual(update["status"], "conflict")
        self.assertIn("SKILL.md", {item["path"] for item in update["diff"]["conflicts"]})

    def test_sensitive_files_and_source_changes_fail_closed(self) -> None:
        source_root = self._write_source("unsafe-skill")
        (source_root / ".env").write_text("SECRET=value\n", encoding="utf-8")

        with self.assertRaises(AppError) as context:
            self.engine.register_source(source_root)

        self.assertEqual(context.exception.code, "skill_sensitive_file")

    def test_due_update_jobs_run_once_and_advance_the_schedule(self) -> None:
        project = self.repository.create_project("定时更新", "template")
        self.repository.update_update_policy(project["id"], {"next_check_at": "2000-01-01T00:00:00.000+00:00"})

        results = self.engine.run_due_checks()
        policy = self.repository.get_update_policy(project["id"])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["project_id"], project["id"])
        self.assertEqual(results[0]["status"], "no_change")
        self.assertNotEqual(policy["next_check_at"], "2000-01-01T00:00:00.000+00:00")

    def test_canonical_manifest_is_stable_and_path_sensitive(self) -> None:
        source_root = self._write_source("stable-skill")
        first = canonical_manifest(source_root)
        second = canonical_manifest(source_root)
        (source_root / "notes.md").write_text("same bytes\n", encoding="utf-8")
        third = canonical_manifest(source_root)

        self.assertEqual(first["artifact_digest"], second["artifact_digest"])
        self.assertNotEqual(first["artifact_digest"], third["artifact_digest"])
        self.assertEqual(first["canonicalization_version"], CANONICALIZATION_VERSION)


if __name__ == "__main__":
    unittest.main()
