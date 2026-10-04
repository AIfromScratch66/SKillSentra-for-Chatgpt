from __future__ import annotations

import tempfile
import json
import os
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.ai_gateway import AIGateway, OpenAIResponsesAdapter
from app.database import Database
from app.errors import AppError
from app.repository import Repository


def response(**changes: object) -> dict:
    result = {
        "id": "resp_contract_1", "model": "gpt-a-2026-01-01", "status": "completed",
        "output": [{"type": "message", "status": "completed", "content": [{"type": "output_text", "text": "Evidence-bound suggestion"}]}],
        "usage": {"input_tokens": 21, "output_tokens": 8, "total_tokens": 29,
                  "input_tokens_details": {"cached_tokens": 5}, "output_tokens_details": {"reasoning_tokens": 3}},
    }
    result.update(changes)
    return result


class ResponsesTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = OpenAIResponsesAdapter("https://api.openai.com/v1", "SKILLSENTRA_TEST_API_KEY")

    def run_response(self, payload: dict):
        with patch.object(self.adapter, "_request", return_value=payload):
            return self.adapter.run("gpt-a", "instructions", "input")

    def test_completed_response_retains_actual_model_and_usage_breakdown(self) -> None:
        result = self.run_response(response())
        self.assertEqual(result.output["model"], "gpt-a-2026-01-01")
        self.assertEqual(result.usage_receipt["total_tokens"], 29)
        self.assertEqual(result.usage_receipt["cached_input_tokens"], 5)
        self.assertEqual(result.usage_receipt["reasoning_output_tokens"], 3)
        self.assertEqual(result.usage_receipt["usage_status"], "reported")
        self.assertEqual(result.usage_receipt["usage_source"], "provider_response")
        self.assertEqual(result.usage_receipt["cost_status"], "estimated_local_pricebook")

    def test_unknown_usage_is_not_zero_or_free(self) -> None:
        result = self.run_response(response(usage=None))
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.output_tokens)
        self.assertIsNone(result.estimated_cost)
        self.assertEqual(result.usage_receipt["usage_status"], "unknown")
        self.assertTrue(result.output["warnings"])

    def test_partial_usage_retains_only_reported_components(self) -> None:
        result = self.run_response(response(usage={"input_tokens": 21}))
        self.assertEqual(result.input_tokens, 21)
        self.assertIsNone(result.output_tokens)
        self.assertIsNone(result.usage_receipt["total_tokens"])
        self.assertEqual(result.usage_receipt["usage_status"], "partial")

    def test_true_zero_usage_is_reported_zero(self) -> None:
        result = self.run_response(response(usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}))
        self.assertEqual(result.input_tokens, 0)
        self.assertEqual(result.usage_receipt["usage_status"], "reported")

    def test_invalid_and_inconsistent_usage_cannot_be_counted(self) -> None:
        for usage in ({"input_tokens": -1}, {"input_tokens": 1.5}, {"input_tokens": True},
                      {"input_tokens": "21"}, {"input_tokens": 21, "output_tokens": 8, "total_tokens": 10},
                      {"input_tokens": 21, "input_tokens_details": {"cached_tokens": 22}}):
            with self.subTest(usage=usage):
                result = self.run_response(response(usage=usage))
                self.assertEqual(result.usage_receipt["usage_status"], "invalid")
                self.assertIsNone(result.input_tokens)
                self.assertIsNone(result.estimated_cost)

    def test_noncompleted_response_keeps_usage_but_cannot_succeed(self) -> None:
        for status in ("incomplete", "failed", "cancelled", "in_progress", None):
            with self.subTest(status=status), self.assertRaises(AppError) as caught:
                self.run_response(response(status=status))
            self.assertEqual(caught.exception.code, "provider_response_not_completed")
            self.assertEqual(caught.exception.details["ai_receipt"]["total_tokens"], 29)

    def test_empty_text_and_missing_response_identity_cannot_succeed(self) -> None:
        for changes, expected in (({"output_text": "", "output": []}, "provider_empty_output"),
                                  ({"id": ""}, "provider_missing_receipt"),
                                  ({"model": ""}, "provider_missing_receipt"),
                                  ({"error": {"code": "server_error"}}, "provider_response_failed")):
            with self.subTest(changes=changes), self.assertRaises(AppError) as caught:
                self.run_response(response(**changes))
            self.assertEqual(caught.exception.code, expected)

    def test_ambiguous_post_failure_is_not_retried(self) -> None:
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError("response lost")
        with patch.object(self.adapter, "_api_key", return_value="fixture"), \
             patch("app.ai_gateway.ensure_safe_destination"), \
             patch("app.ai_gateway.urllib.request.build_opener", return_value=opener), \
             self.assertRaises(AppError):
            self.adapter.run("gpt-a", "instructions", "input")
        self.assertEqual(opener.open.call_count, 1)


class GatewayReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "receipts.db")
        self.database.migrate()
        self.repository = Repository(self.database)
        self.gateway = AIGateway(self.repository)
        self.project = self.repository.create_project("Receipt verification", "template")
        self.connection = self.gateway.create_connection({"provider": "openai"})
        self.repository.update_connection_test(self.connection["id"], "healthy", [{"id": "gpt-a"}, {"id": "gpt-b"}])
        self.repository.update_policy(self.project["id"], {"connection_id": self.connection["id"], "creator_model": "gpt-a", "evaluator_model": "gpt-b", "safety_model": "gpt-b"})

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_missing_connection_does_not_create_mock(self) -> None:
        project = self.repository.create_project("Unconfigured", "template")
        with self.assertRaises(AppError) as caught:
            self.gateway.run(project["id"], {"require_real": True})
        self.assertEqual(caught.exception.code, "ai_connection_required")
        self.assertFalse(any(item["provider"] == "mock" for item in self.repository.list_connections()))

    def test_real_requirement_rejects_explicit_mock(self) -> None:
        connection = self.gateway.ensure_mock_connection()
        self.repository.update_policy(self.project["id"], {"connection_id": connection["id"]})
        with self.assertRaises(AppError) as caught:
            self.gateway.run(self.project["id"], {"require_real": True})
        self.assertEqual(caught.exception.code, "real_ai_required")
        self.assertEqual(self.repository.list_ai_runs(self.project["id"]), [])

    def test_custom_endpoint_cannot_claim_official_openai_provider(self) -> None:
        with self.assertRaises(AppError) as caught:
            self.gateway.create_connection({"provider": "openai", "base_url": "http://127.0.0.1:9999/v1"})
        self.assertEqual(caught.exception.code, "provider_identity_mismatch")

    def test_incomplete_response_persists_real_usage_and_request_id(self) -> None:
        with patch.object(OpenAIResponsesAdapter, "_request", return_value=response(status="incomplete")), self.assertRaises(AppError) as caught:
            self.gateway.run(self.project["id"], {"require_real": True})
        run = self.repository.get_ai_run(caught.exception.details["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["total_tokens"], 29)
        self.assertEqual(run["provider_request_id"], "resp_contract_1")
        self.assertEqual(run["error_code"], "provider_response_not_completed")
        self.assertGreater(self.repository.daily_spend(self.project["id"]), 0)
        summary = self.repository.ai_contribution_summary(self.project["id"])
        self.assertEqual(summary["total_tokens"], 29)
        self.assertEqual(summary["failed_run_count"], 1)
        self.assertFalse(summary["real_chatgpt_confirmed"])

    def test_missing_usage_keeps_budget_reservation_and_unknown_summary(self) -> None:
        with patch.object(OpenAIResponsesAdapter, "_request", return_value=response(usage=None)):
            run = self.gateway.run(self.project["id"], {"require_real": True})
        self.assertIsNone(run["input_tokens"])
        self.assertIsNone(run["estimated_cost"])
        self.assertGreater(self.repository.daily_spend(self.project["id"]), 0)
        summary = self.repository.ai_contribution_summary(self.project["id"])
        self.assertEqual(summary["unknown_usage_run_count"], 1)
        self.assertFalse(summary["usage_complete"])

    def test_fallback_totals_include_failed_attempt_and_preserve_provenance(self) -> None:
        with patch.object(OpenAIResponsesAdapter, "_request", side_effect=[response(output=[]), response(id="resp_contract_2", model="gpt-b")]):
            result = self.gateway.orchestrate(self.project["id"], {"roles": ["creator"], "require_real": True})
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(result["total_tokens"], 58)
        self.assertEqual(result["total_cost"], round(sum(item["estimated_cost"] for item in result["attempts"]), 8))
        self.assertEqual([item["status"] for item in result["attempts"]], ["failed", "completed"])
        stored = self.repository.get_ai_run(result["runs"][0]["id"])
        self.assertTrue(stored["fallback_used"])
        self.repository.delete_connection(self.connection["id"])
        self.assertEqual(self.repository.get_ai_run(stored["id"])["connection_provider"], "openai")

    def test_budget_reservation_is_rechecked_in_transaction(self) -> None:
        self.repository.start_ai_run(self.project["id"], self.connection["id"], "template", 0, "creator", "gpt-a", {}, reserved_cost=1, budget_limit=1)
        with self.assertRaises(AppError) as caught:
            self.repository.start_ai_run(self.project["id"], self.connection["id"], "template", 0, "creator", "gpt-a", {}, reserved_cost=1, budget_limit=1)
        self.assertEqual(caught.exception.code, "daily_budget_exceeded")


class WorkflowFixtureHandler(BaseHTTPRequestHandler):
    """Local HTTP contract fixture only; never evidence of a live OpenAI call."""

    def do_POST(self) -> None:  # noqa: N802
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        self.server.calls.append(request)
        index = len(self.server.calls)
        payload = response(id=f"resp_fixture_{index}", model=request["model"],
                           output_text=f"Fixture output {index}",
                           usage={"input_tokens": 21 + index, "output_tokens": 8, "total_tokens": 29 + index})
        if index == self.server.fail_at:
            payload["status"] = self.server.failure_status
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("x-request-id", f"req_fixture_{index}")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        pass


class GatewayFullWorkflowFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        database = Database(Path(self.tempdir.name) / "workflow.db")
        database.migrate()
        self.repository = Repository(database)
        self.gateway = AIGateway(self.repository)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), WorkflowFixtureHandler)
        self.server.calls = []
        self.server.fail_at = -1
        self.server.failure_status = "incomplete"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.environment = patch.dict(os.environ, {"SKILLSENTRA_WORKFLOW_FIXTURE_KEY": "local-fixture-only"})
        self.environment.start()
        self.connection = self.gateway.create_connection({
            "provider": "openai-compatible", "base_url": f"http://127.0.0.1:{self.server.server_address[1]}",
            "credential_env": "SKILLSENTRA_WORKFLOW_FIXTURE_KEY",
        })
        self.repository.update_connection_test(self.connection["id"], "healthy", [{"id": f"gpt-{role}"} for role in ("creator", "evaluator", "safety")])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.environment.stop()
        self.tempdir.cleanup()

    def project(self, route: str) -> dict:
        project = self.repository.create_project(f"HTTP fixture {route}", route)
        self.repository.update_policy(project["id"], {
            "connection_id": self.connection["id"], "creator_model": "gpt-creator",
            "evaluator_model": "gpt-evaluator", "safety_model": "gpt-safety",
        })
        return project

    def test_all_16_steps_and_48_role_calls_use_http_results_and_provider_receipts(self) -> None:
        expected_tokens = 0
        for route, step_count in (("template", 9), ("existing", 7)):
            project = self.project(route)
            route_tokens = 0
            for step_index in range(step_count):
                start = len(self.server.calls)
                task = f"{route} step {step_index}"
                result = self.gateway.orchestrate(project["id"], {
                    "route": route, "step_index": step_index, "require_real": True,
                    "task": task, "context": {"step_input": task},
                })
                self.assertEqual(result["status"], "completed")
                self.assertTrue(result["usage_complete"])
                self.assertEqual(len(result["attempts"]), 3)
                prior_outputs = []
                for offset, run in enumerate(result["runs"]):
                    sent = json.loads(self.server.calls[start + offset]["input"])
                    self.assertEqual(sent["task"], task)
                    self.assertEqual(sent["context"]["prior_role_outputs"], prior_outputs)
                    self.assertEqual(run["output"]["summary"], f"Fixture output {start + offset + 1}")
                    self.assertEqual(run["provider_request_id"], f"resp_fixture_{start + offset + 1}")
                    self.assertEqual(run["transport_request_id"], f"req_fixture_{start + offset + 1}")
                    self.assertEqual(run["usage_source"], "provider_response")
                    self.assertEqual(run["usage_status"], "reported")
                    self.assertTrue(run["output_sha256"])
                    prior_outputs.append({"role": run["role"], "summary": run["output"]["summary"]})
                route_tokens += result["total_tokens"]
            summary = self.repository.ai_contribution_summary(project["id"])
            self.assertEqual(summary["completed_run_count"], step_count * 3)
            self.assertEqual(summary["total_tokens"], route_tokens)
            self.assertFalse(summary["real_chatgpt_confirmed"])
            expected_tokens += route_tokens
        self.assertEqual(len(self.server.calls), 48)
        self.assertEqual(expected_tokens, sum(29 + index for index in range(1, 49)))

    def test_failed_or_missing_status_stops_the_route_before_later_roles(self) -> None:
        for status in ("incomplete", None):
            with self.subTest(status=status):
                self.server.calls = []
                self.server.fail_at = 13
                self.server.failure_status = status
                project = self.project("template")
                for step_index in range(4):
                    self.gateway.orchestrate(project["id"], {"step_index": step_index, "require_real": True})
                with self.assertRaises(AppError) as caught:
                    self.gateway.orchestrate(project["id"], {"step_index": 4, "require_real": True})
                self.assertEqual(caught.exception.code, "provider_response_not_completed")
                self.assertEqual(len(self.server.calls), 13)
                summary = self.repository.ai_contribution_summary(project["id"])
                self.assertEqual(summary["completed_run_count"], 12)
                self.assertEqual(summary["failed_run_count"], 1)
                self.assertEqual(summary["total_tokens"], sum(29 + index for index in range(1, 14)))


if __name__ == "__main__":
    unittest.main()
