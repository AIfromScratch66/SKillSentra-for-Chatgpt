from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.errors import AppError
from app.host_collaboration import HostCollaboration
from app.repository import Repository


class HostCollaborationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Database(Path(self.temp.name) / 'host.db')
        database.migrate()
        self.repo = Repository(database)
        self.host = HostCollaboration(self.repo)
        self.project = self.repo.create_project('Host roundtrip fixture', 'template')

    def request(self, index: int = 0) -> dict:
        return self.host.create(self.project['id'], {'route': 'template', 'step_index': index, 'instruction': 'Review this step; do not authorize release'})

    @staticmethod
    def result(request: dict, **extra: object) -> dict:
        return {'context_hash': request['context_hash'], 'output': {'summary': 'Fixture proposal, not a real ChatGPT call'}, 'host_label': 'test fixture', **extra}

    def assert_error(self, code: str, fn, *args) -> None:
        with self.assertRaises(AppError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)

    def test_all_sixteen_steps_roundtrip_no_gate_or_content_mutation(self) -> None:
        for route, length in [('template', 9), ('existing', 7)]:
            project = self.repo.create_project('Full route fixture', route)
            before = self.repo.get_project(project['id'])
            for index in range(length):
                with self.subTest(route=route, index=index):
                    request = self.host.create(project['id'], {'route': route, 'step_index': index, 'instruction': f'Fixture review step {index}'})
                    self.assertEqual(request['status'], 'pending')
                    self.assertFalse(request['automatically_invokes_host'])
                    result = self.host.submit(project['id'], request['id'], self.result(request))
                    self.assertEqual(result['status'], 'proposed')
                    self.assertEqual(result['usage_status'], 'unknown')
                    self.assertIsNone(result['total_tokens'])
                    self.assertFalse(result['provider_verified'])
                    self.assertFalse(result['applied'])
            self.assertEqual(before, self.repo.get_project(project['id']))
            self.assertEqual(len(self.host.list(project['id'])), length)

    def test_repeated_same_response_is_idempotent_and_different_response_conflicts(self) -> None:
        request = self.request()
        payload = self.result(request)
        first = self.host.submit(self.project['id'], request['id'], payload)
        second = self.host.submit(self.project['id'], request['id'], payload)
        self.assertEqual(first, second)
        payload['output']['summary'] = 'different proposal'
        self.assert_error('host_result_conflict', self.host.submit, self.project['id'], request['id'], payload)
        events = [e for e in self.repo.list_audit_events(self.project['id']) if e['action'] == 'host.result.proposed']
        self.assertEqual(len(events), 1)

    def test_changed_step_invalidates_original_context(self) -> None:
        request = self.request()
        self.repo.save_step(self.project['id'], 'template', 0, {'goal': 'changed'}, 'draft')
        self.assert_error('host_context_stale', self.host.submit, self.project['id'], request['id'], self.result(request))
        self.assertEqual(self.host.get(self.project['id'], request['id'])['status'], 'pending')

    def test_wrong_context_and_other_project_fail_closed(self) -> None:
        request = self.request()
        self.assert_error('host_context_conflict', self.host.submit, self.project['id'], request['id'], self.result(request, context_hash='sha256:wrong'))
        other = self.repo.create_project('Other', 'template')
        self.assert_error('host_request_not_found', self.host.get, other['id'], request['id'])
        self.assert_error('host_request_not_found', self.host.submit, other['id'], request['id'], self.result(request))

    def test_completed_proposal_becomes_stale_after_edit(self) -> None:
        request = self.request()
        result = self.host.submit(self.project['id'], request['id'], self.result(request))
        self.assertTrue(result['context_current'])
        self.repo.save_step(self.project['id'], 'template', 0, {'goal': 'new goal'}, 'draft')
        self.assertTrue(self.host.get(self.project['id'], request['id'])['is_stale'])
        self.assertTrue(self.host.list(self.project['id'])[0]['is_stale'])
        self.assertTrue(self.host.submit(self.project['id'], request['id'], self.result(request))['is_stale'])

    def test_only_proposal_acknowledgement_does_not_invalidate_content(self) -> None:
        self.repo.save_step(self.project['id'], 'template', 0, {'goal': 'same goal'}, 'draft')
        request = self.request()
        self.repo.save_step(self.project['id'], 'template', 0, {'goal': 'same goal', '_ai': {'decision': 'accept'}}, 'draft')
        self.assertTrue(self.host.get(self.project['id'], request['id'])['context_current'])

    def test_host_reported_usage_cannot_claim_provider_verification(self) -> None:
        request = self.request()
        result = self.host.submit(self.project['id'], request['id'], self.result(request, usage={'input_tokens': 12, 'output_tokens': 4, 'total_tokens': 16}, provider_verified=True, usage_source='provider_response'))
        self.assertEqual(result['usage_status'], 'host_reported')
        self.assertEqual(result['usage_source'], 'host_reported')
        self.assertFalse(result['provider_verified'])
        self.assertEqual(result['total_tokens'], 16)
        self.assertEqual(self.repo.list_ai_runs(self.project['id']), [])

    def test_invalid_token_counts_rejected(self) -> None:
        for usage in [{'input_tokens': -1}, {'input_tokens': True}, {'output_tokens': 2.5}, {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 4}, []]:
            request = self.request()
            self.assert_error('invalid_host_usage', self.host.submit, self.project['id'], request['id'], self.result(request, usage=usage))

    def test_secrets_are_redacted_and_empty_output_not_success(self) -> None:
        self.repo.save_step(self.project['id'], 'template', 0, {'password': 'fixture-password', 'goal': 'sk-abcdefghijklmnopqrstuv'}, 'draft')
        request = self.request()
        self.assertNotIn('fixture-password', str(request))
        self.assertNotIn('sk-abcdefghijklmnopqrstuv', str(request))
        self.assert_error('invalid_host_output', self.host.submit, self.project['id'], request['id'], self.result(request, output={'summary': ''}))

    def test_invalid_scope_is_rejected(self) -> None:
        for route, index in [('template', -1), ('template', 9), ('existing', 7), ('template', True), ('unknown', 0)]:
            self.assert_error('invalid_run_scope', self.host.create, self.project['id'], {'route': route, 'step_index': index, 'instruction': 'test'})


if __name__ == '__main__':
    unittest.main()
