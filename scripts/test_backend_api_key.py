"""Checks the OpenAI-compatible backend auth plumbing.

`backend.api_key_env` names an environment variable; the key itself never lives
in the node package. These tests prove the helper resolves it and that the three
HTTP helpers actually put the `Authorization` header on the wire.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent

RECORDED: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the test output clean
        return

    def _remember(self):
        RECORDED.append({key.lower(): value for key, value in self.headers.items()})

    def do_GET(self):
        self._remember()
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self._remember()
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.endswith("/stream"):
            body = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def load_gateway():
    spec = importlib.util.spec_from_file_location("task_agent_gateway", PACKAGE_DIR / "task_agent_gateway.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["task_agent_gateway"] = module
    spec.loader.exec_module(module)
    return module


class AuthHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gateway = load_gateway()

    def test_missing_configuration_yields_no_header(self):
        self.assertEqual(self.gateway.backend_auth_headers({}), {})
        self.assertEqual(self.gateway.backend_auth_headers({"api_key_env": ""}), {})
        self.assertEqual(self.gateway.backend_auth_headers({"api_key_env": "   "}), {})
        self.assertEqual(self.gateway.backend_auth_headers(None), {})

    def test_unset_or_empty_env_var_yields_no_header(self):
        os.environ.pop("STUDIO_SUITE_TEST_KEY", None)
        self.assertEqual(self.gateway.backend_auth_headers({"api_key_env": "STUDIO_SUITE_TEST_KEY"}), {})
        os.environ["STUDIO_SUITE_TEST_KEY"] = "   "
        try:
            self.assertEqual(self.gateway.backend_auth_headers({"api_key_env": "STUDIO_SUITE_TEST_KEY"}), {})
        finally:
            os.environ.pop("STUDIO_SUITE_TEST_KEY", None)

    def test_env_var_becomes_bearer_header(self):
        os.environ["STUDIO_SUITE_TEST_KEY"] = "sk-unit-test"
        try:
            headers = self.gateway.backend_auth_headers({"api_key_env": "STUDIO_SUITE_TEST_KEY"})
        finally:
            os.environ.pop("STUDIO_SUITE_TEST_KEY", None)
        self.assertEqual(headers, {"Authorization": "Bearer sk-unit-test"})

    def test_key_is_never_read_from_config_values(self):
        # A stray raw key in the config must be ignored: only env names are honoured.
        self.assertEqual(self.gateway.backend_auth_headers({"api_key": "sk-leaked"}), {})


class WireHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gateway = load_gateway()
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RECORDED.clear()
        os.environ["STUDIO_SUITE_TEST_KEY"] = "sk-wire-test"
        self.headers = self.gateway.backend_auth_headers({"api_key_env": "STUDIO_SUITE_TEST_KEY"})

    def tearDown(self):
        os.environ.pop("STUDIO_SUITE_TEST_KEY", None)

    def test_post_json_sends_authorization(self):
        self.gateway.http_post_json(f"{self.base}/chat/completions", {"model": "x"}, timeout=10, headers=self.headers)
        self.assertEqual(RECORDED[-1].get("authorization"), "Bearer sk-wire-test")

    def test_get_json_sends_authorization(self):
        self.gateway.http_get_json(f"{self.base}/models", timeout=10, headers=self.headers)
        self.assertEqual(RECORDED[-1].get("authorization"), "Bearer sk-wire-test")

    def test_stream_sends_authorization_and_keeps_sse_headers(self):
        text = self.gateway.http_post_openai_stream(
            f"{self.base}/stream",
            {"model": "x"},
            timeout=10,
            stream_callback=lambda _delta: None,
            headers=self.headers,
        )
        self.assertEqual(RECORDED[-1].get("authorization"), "Bearer sk-wire-test")
        self.assertEqual(RECORDED[-1].get("accept"), "text/event-stream")
        self.assertEqual(text, "ok")

    def test_without_a_key_no_authorization_header_is_sent(self):
        self.gateway.http_post_json(f"{self.base}/chat/completions", {"model": "x"}, timeout=10)
        self.assertNotIn("authorization", RECORDED[-1])

    def test_local_backends_without_env_name_stay_unchanged(self):
        self.gateway.http_get_json(f"{self.base}/models", timeout=10, headers=self.gateway.backend_auth_headers({}))
        self.assertNotIn("authorization", RECORDED[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
