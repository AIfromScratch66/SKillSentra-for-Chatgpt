"""Regression tests for acknowledgement, provenance and template domain boundaries."""
import json
import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.repository import Repository
from app.skill_engine import SkillEngine


class CandidateBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        db = Database(self.root / 'test.db')
        db.migrate()
        self.repository = Repository(db)
        self.engine = SkillEngine(self.repository, self.root / 'artifacts', (self.root,))

    def generate(self, ai=None, extra=None):
        project = self.repository.create_project('report-builder', 'template')
        payload = dict(skillName='report-builder', goal='Prepare an evidence based report',
                       trigger='When a report is requested', success='A report with sources',
                       nonGoal='Unrelated translation', mustDo='Verify sources',
                       mustNot='Never invent facts', failure='Stop on missing evidence')
        if ai is not None:
            payload['_ai'] = ai
        payload.update(extra or {})
        self.repository.save_step(project['id'], 'template', 0, payload, 'draft')
        result = self.engine.generate_candidate(project['id'])
        return Path(result['version']['artifact_path'])

    def test_pending_ignored_and_stale_advice_never_enters_artifact(self):
        for decision, stale in [('pending', False), ('ignored', False), ('accepted', True)]:
            with self.subTest(decision=decision, stale=stale):
                root = self.generate(dict(text='UNAPPROVED-CONTENT-752', decision=decision, stale=stale))
                content = '\n'.join(p.read_text(encoding='utf-8') for p in root.rglob('*.md'))
                self.assertNotIn('UNAPPROVED-CONTENT-752', content)

    def test_acknowledged_advice_is_reference_not_execution_instruction(self):
        root = self.generate(dict(text='ACKNOWLEDGED-CONTEXT-752', decision='accepted', source='host'))
        self.assertNotIn('ACKNOWLEDGED-CONTEXT-752', (root / 'SKILL.md').read_text(encoding='utf-8'))
        brief = (root / 'references/interaction-brief.md').read_text(encoding='utf-8')
        self.assertIn('ACKNOWLEDGED-CONTEXT-752', brief)
        self.assertIn('尚未应用', brief)

    def test_client_cannot_inject_internal_interaction_summary(self):
        root = self.generate(extra={'_interaction_insights': [dict(summary='FORGED-CONTEXT-752')]})
        self.assertNotIn('FORGED-CONTEXT-752', (root / 'references/interaction-brief.md').read_text(encoding='utf-8'))

    def test_generic_template_has_no_patent_only_rubric(self):
        root = self.generate()
        rubric = (root / 'references/evaluation-rubric.md').read_text(encoding='utf-8')
        self.assertNotIn('专利硬门', rubric)
        self.assertNotIn('权利要求', rubric)

    def test_generated_patent_cases_do_not_claim_host_provenance(self):
        root = self.generate(extra={'skillName': '专利草案生成', 'goal': '整理专利权利要求草案'})
        cases = json.loads((root / 'evals/cases.json').read_text(encoding='utf-8'))
        synthetic = [c for c in cases['cases'] if c.get('source', {}).get('kind') == 'skillsentra_interaction']
        self.assertEqual(len(synthetic), 3)
        self.assertTrue(all(c['provenance']['origin'] == 'template_generated' for c in synthetic))
