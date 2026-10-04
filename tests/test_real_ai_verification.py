"""All execution-path tests stub transport; they never use a real API key."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai_gateway import OpenAIResponsesAdapter
from scripts.verify_real_ai import CREDENTIAL_ENV, main, verify


class RealAIVerificationSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "report.json"

    def args(self, execute: bool = False, limit: int = 1) -> argparse.Namespace:
        return argparse.Namespace(execute_real=execute, model="fixture-model", step_limit=limit, output=self.output)

    def test_missing_key_defaults_to_blocked_without_network_or_database(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("scripts.verify_real_ai.Database") as database, patch.object(OpenAIResponsesAdapter, "_request") as transport:
            report, code = verify(self.args())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["real_calls"], 0)
        self.assertIsNone(report["tokens"])
        transport.assert_not_called()
        database.assert_not_called()
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["status"], "blocked")

    def test_present_key_without_execute_is_not_used_or_exposed(self) -> None:
        secret = "never-print-this-fixture-secret"
        with patch.dict(os.environ, {CREDENTIAL_ENV: secret}), patch.object(OpenAIResponsesAdapter, "_request") as transport, contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["--output", str(self.output)])
        self.assertEqual(code, 0)
        transport.assert_not_called()
        self.assertNotIn(secret, stdout.getvalue() + self.output.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["status"], "ready_not_executed")

    def test_execute_requires_explicit_model_before_network(self) -> None:
        with patch.object(OpenAIResponsesAdapter, "_request") as transport, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--execute-real", "--output", str(self.output)])
        self.assertEqual(caught.exception.code, 2)
        transport.assert_not_called()

    @staticmethod
    def fixture_response(index: int, **changes: object) -> dict:
        response = {"id": f"resp_fixture_{index}", "model": "fixture-model", "status": "completed",
                    "output_text": f"Synthetic fixture output {index}",
                    "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}}
        response.update(changes)
        return response

    def test_opt_in_probe_makes_three_stubbed_calls_and_preserves_receipts(self) -> None:
        replies = [{"data": [{"id": "fixture-model"}]}] + [self.fixture_response(i) for i in range(1, 4)]
        with patch.dict(os.environ, {CREDENTIAL_ENV: "fixture-only"}), patch.object(OpenAIResponsesAdapter, "_request", side_effect=replies) as transport:
            report, code = verify(self.args(execute=True))
        self.assertEqual(code, 0)
        self.assertEqual(transport.call_count, 4)  # One GET model probe + three POST calls.
        self.assertEqual(report["real_calls"], 3)
        self.assertEqual(report["completed_steps"], 1)
        self.assertEqual(report["tokens"]["total_tokens"], 39)
        self.assertTrue(all(run["run_id"] and run["response_id"] for run in report["runs"]))
        self.assertTrue(all(run["actual_model"] == "fixture-model" for run in report["runs"]))
        self.assertFalse(report["chatgpt_host_verified"])
        second_input = json.loads(transport.call_args_list[2].args[2]["input"])
        self.assertEqual(second_input["context"]["prior_role_outputs"], [{"role": "creator", "summary": "Synthetic fixture output 1"}])

    def test_failure_stops_before_later_roles_and_keeps_failure_usage(self) -> None:
        replies = [{"data": [{"id": "fixture-model"}]} , self.fixture_response(1, status="incomplete")]
        with patch.dict(os.environ, {CREDENTIAL_ENV: "fixture-only"}), patch.object(OpenAIResponsesAdapter, "_request", side_effect=replies) as transport:
            report, code = verify(self.args(execute=True))
        self.assertEqual(code, 1)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["real_calls"], 1)
        self.assertEqual(report["tokens"]["total_tokens"], 13)
        self.assertEqual(report["completed_steps"], 0)
        self.assertEqual(report["runs"][0]["status"], "failed")

    def test_missing_usage_stops_before_later_roles_without_inventing_zero(self) -> None:
        replies = [{"data": [{"id": "fixture-model"}]} , self.fixture_response(1, usage=None)]
        with patch.dict(os.environ, {CREDENTIAL_ENV: "fixture-only"}), patch.object(OpenAIResponsesAdapter, "_request", side_effect=replies) as transport:
            report, code = verify(self.args(execute=True))
        self.assertEqual(code, 1)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(report["reason"], "verification_usage_incomplete")
        self.assertIsNone(report["tokens"])
        self.assertIsNone(report["estimated_cost"])


if __name__ == "__main__":
    unittest.main()
