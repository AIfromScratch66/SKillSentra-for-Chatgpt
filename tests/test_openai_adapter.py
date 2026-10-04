from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.ai_gateway import OpenAIResponsesAdapter


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_GET(self) -> None:  # noqa: N802
        self.calls.append({"method": "GET", "path": self.path, "authorization": self.headers.get("Authorization")})
        self._json({"data": [{"id": "gpt-test"}]})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.calls.append(
            {
                "method": "POST",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            }
        )
        self._json(
            {
                "id": "resp_test_123",
                "status": "completed",
                "model": "gpt-test",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "已生成结构化建议。"}]}],
                "usage": {"input_tokens": 21, "output_tokens": 8, "total_tokens": 29},
            }
        )

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class OpenAIAdapterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        FakeOpenAIHandler.calls = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self) -> None:
        os.environ["SKILLSENTRA_TEST_API_KEY"] = "local-contract-secret"

    def tearDown(self) -> None:
        os.environ.pop("SKILLSENTRA_TEST_API_KEY", None)

    def test_models_and_responses_contract(self) -> None:
        adapter = OpenAIResponsesAdapter(self.base_url, "SKILLSENTRA_TEST_API_KEY", timeout=3)

        models = adapter.test()
        result = adapter.run("gpt-test", "只输出建议", "测试输入")

        self.assertEqual(models[0]["id"], "gpt-test")
        self.assertEqual(result.output["summary"], "已生成结构化建议。")
        self.assertEqual(result.input_tokens, 21)
        self.assertEqual(result.output_tokens, 8)
        self.assertEqual(result.provider_request_id, "resp_test_123")
        self.assertEqual(FakeOpenAIHandler.calls[0]["authorization"], "Bearer local-contract-secret")
        self.assertEqual(FakeOpenAIHandler.calls[1]["path"], "/responses")
        self.assertEqual(FakeOpenAIHandler.calls[1]["payload"]["model"], "gpt-test")
        self.assertEqual(FakeOpenAIHandler.calls[1]["payload"]["input"], "测试输入")


if __name__ == "__main__":
    unittest.main()
