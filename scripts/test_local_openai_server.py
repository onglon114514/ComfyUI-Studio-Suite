"""Tests for the local OpenAI-compatible server (prompt_studio/openai_server.py).

The module is loaded standalone (no ComfyUI imports) and every HTTP test runs
against a real aiohttp app on 127.0.0.1 with an injected fake backend — no
model is ever loaded.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent


def load_openai_server():
    spec = importlib.util.spec_from_file_location(
        "prompt_studio_openai_server",
        PACKAGE_DIR / "prompt_studio" / "openai_server.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OPENAI = load_openai_server()
from aiohttp import web  # noqa: E402  (same interpreter that runs aiohttp in production)

TINY_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n-fake").decode("ascii")

TEXT_PROFILE = {
    "display_name": "Unit Test Text",
    "model_name": "unit-test-text-local",
    "model_path": "models/llm/unit-test-text.gguf",
    "default_context_size": 1024,
    "supports_vision": False,
}
VISION_PROFILE = {
    "display_name": "Unit Test Vision",
    "model_name": "unit-test-vision-local",
    "model_path": "models/llm/unit-test-vision.gguf",
    "mmproj_path": "models/llm/unit-test-vision-mmproj.gguf",
    "default_context_size": 2048,
    "supports_vision": True,
}
PROFILES = {
    "unit_text_q8": dict(TEXT_PROFILE),
    "unit_text_q8_vision": dict(VISION_PROFILE),
}

ENABLED_CONFIG = {
    "enabled": True,
    "max_request_bytes": 1024 * 1024,
    "max_image_bytes": 256 * 1024,
    "max_images_per_request": 2,
    "max_messages": 8,
    "request_timeout_sec": 30,
    "allow_remote_image_url": False,
    "default_backend_profile": "",
    "queue_guard": True,
}


def make_fake_factory(recorded, text="fake answer", chunks=("fake ", "answer"), started_event=None, release_event=None):
    def factory(profile_key):
        def complete(payload, stream_callback=None):
            recorded.append({"profile_key": profile_key, "payload": payload})
            if started_event is not None:
                started_event.set()
            if release_event is not None:
                release_event.wait(timeout=15)
            if stream_callback is not None:
                for chunk in chunks:
                    stream_callback(chunk)
                return "".join(chunks)
            return text

        return complete

    return factory


class ServerFixture:
    """Run a real aiohttp app with the OpenAICompatServer on 127.0.0.1:0."""

    def __init__(self, server):
        self.server = server
        self.port = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()

    def __enter__(self):
        # ComfyUI hands nodes a web.RouteTableDef; mirror that exactly.
        routes = web.RouteTableDef()
        self.server.attach(routes)
        app = web.Application()
        app.add_routes(routes)
        self._thread = threading.Thread(target=self._run, args=(app,), daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("test server did not start")
        return self

    def _run(self, app):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        runner = web.AppRunner(app)
        self._loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        self._loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]
        self._ready.set()
        self._loop.run_forever()
        self._loop.run_until_complete(runner.cleanup())
        self._loop.close()

    def __exit__(self, *exc):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        return False

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


def http_get_json(url, timeout=30):
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def http_post_json(url, payload, timeout=30):
    body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def make_server(recorded, *, config=None, chunks=("fake ", "answer"), started_event=None, release_event=None):
    merged = dict(ENABLED_CONFIG)
    if config:
        merged.update(config)
    return OPENAI.OpenAICompatServer(
        config=merged,
        profiles=dict(PROFILES),
        completer_factory=make_fake_factory(
            recorded,
            chunks=chunks,
            started_event=started_event,
            release_event=release_event,
        ),
        queue_state_fn=lambda: {"busy": False, "running": 0, "pending": 0},
    )


class HelperTests(unittest.TestCase):
    def test_remote_is_loopback(self):
        self.assertTrue(OPENAI.remote_is_loopback("127.0.0.1"))
        self.assertTrue(OPENAI.remote_is_loopback("127.0.0.99"))
        self.assertTrue(OPENAI.remote_is_loopback("::1"))
        self.assertTrue(OPENAI.remote_is_loopback("::ffff:127.0.0.1"))
        self.assertFalse(OPENAI.remote_is_loopback("192.168.1.20"))
        self.assertFalse(OPENAI.remote_is_loopback("8.8.8.8"))
        self.assertFalse(OPENAI.remote_is_loopback(None))
        self.assertFalse(OPENAI.remote_is_loopback("not-an-ip"))

    def test_is_vision_profile(self):
        self.assertTrue(OPENAI.is_vision_profile("unit_text_q8_vision", VISION_PROFILE))
        # Key-name convention alone is enough even without the flags.
        self.assertTrue(OPENAI.is_vision_profile("something_vision", {}))
        self.assertTrue(OPENAI.is_vision_profile("plain", {"supports_vision": True}))
        self.assertTrue(OPENAI.is_vision_profile("plain", {"mmproj_path": "models/mmproj.gguf"}))
        self.assertFalse(OPENAI.is_vision_profile("unit_text_q8", TEXT_PROFILE))

    def test_models_payload_marks_vision(self):
        payload = OPENAI.build_models_payload(PROFILES)
        self.assertEqual(payload["object"], "list")
        by_id = {entry["id"]: entry for entry in payload["data"]}
        self.assertEqual(set(by_id), set(PROFILES))
        self.assertTrue(by_id["unit_text_q8_vision"]["supports_vision"])
        self.assertFalse(by_id["unit_text_q8"]["supports_vision"])
        for entry in payload["data"]:
            self.assertEqual(entry["object"], "model")
            self.assertIn("owned_by", entry)

    def test_validate_image_url_accepts_base64(self):
        self.assertEqual(OPENAI.validate_image_url(TINY_PNG_DATA_URL, ENABLED_CONFIG), TINY_PNG_DATA_URL)

    def test_validate_image_url_rejects_remote_by_default(self):
        with self.assertRaises(OPENAI.RequestValidationError) as ctx:
            OPENAI.validate_image_url("https://example.com/x.png", ENABLED_CONFIG)
        self.assertEqual(ctx.exception.code, "remote_image_url_not_allowed")

    def test_validate_image_url_allows_remote_when_enabled(self):
        config = dict(ENABLED_CONFIG, allow_remote_image_url=True)
        url = "https://example.com/x.png"
        self.assertEqual(OPENAI.validate_image_url(url, config), url)

    def test_validate_image_url_rejects_non_base64_and_non_image(self):
        with self.assertRaises(OPENAI.RequestValidationError):
            OPENAI.validate_image_url("data:image/png;utf8,abc", ENABLED_CONFIG)
        with self.assertRaises(OPENAI.RequestValidationError):
            OPENAI.validate_image_url("data:text/plain;base64,aGk=", ENABLED_CONFIG)
        with self.assertRaises(OPENAI.RequestValidationError):
            OPENAI.validate_image_url("data:image/png;base64,!!!not-base64!!!", ENABLED_CONFIG)

    def test_validate_image_url_rejects_oversize(self):
        big = "data:image/png;base64," + base64.b64encode(b"x" * 4096).decode("ascii")
        config = dict(ENABLED_CONFIG, max_image_bytes=1024)
        with self.assertRaises(OPENAI.RequestValidationError) as ctx:
            OPENAI.validate_image_url(big, config)
        self.assertEqual(ctx.exception.status, 413)

    def test_resolve_profile_auto_prefers_vision_key_for_images(self):
        key = OPENAI.resolve_profile_key("", PROFILES, ENABLED_CONFIG, has_images=True)
        self.assertEqual(key, "unit_text_q8_vision")
        key = OPENAI.resolve_profile_key("auto", PROFILES, ENABLED_CONFIG, has_images=False)
        self.assertEqual(key, "unit_text_q8")

    def test_resolve_profile_rejects_unknown_and_non_vision(self):
        with self.assertRaises(OPENAI.RequestValidationError) as ctx:
            OPENAI.resolve_profile_key("no-such-model", PROFILES, ENABLED_CONFIG, has_images=False)
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(OPENAI.RequestValidationError) as ctx:
            OPENAI.resolve_profile_key("unit_text_q8", PROFILES, ENABLED_CONFIG, has_images=True)
        self.assertEqual(ctx.exception.code, "model_not_vision_capable")

    def test_resolve_profile_matches_model_name_alias(self):
        key = OPENAI.resolve_profile_key("unit-test-vision-local", PROFILES, ENABLED_CONFIG, has_images=True)
        self.assertEqual(key, "unit_text_q8_vision")


class DisabledServerTests(unittest.TestCase):
    def test_disabled_returns_403_on_both_endpoints(self):
        recorded = []
        server = make_server(recorded, config={"enabled": False})
        with ServerFixture(server) as fixture:
            status, payload = http_get_json(fixture.url("/studio-suite/llm/v1/models"))
            self.assertEqual(status, 403)
            self.assertEqual(payload["error"]["code"], "server_disabled")
            status, payload = http_post_json(
                fixture.url("/studio-suite/llm/v1/chat/completions"),
                {"model": "unit_text_q8", "messages": [{"role": "user", "content": "hi"}]},
            )
            self.assertEqual(status, 403)
            self.assertEqual(payload["error"]["code"], "server_disabled")
        self.assertEqual(recorded, [])


class HttpSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recorded = []
        cls.server = make_server(cls.recorded)
        cls.fixture = ServerFixture(cls.server)
        cls.fixture.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.__exit__()

    def setUp(self):
        self.recorded.clear()

    def test_models_shape_namespaced_and_root(self):
        for path in ("/studio-suite/llm/v1/models", "/v1/models"):
            status, payload = http_get_json(self.fixture.url(path))
            self.assertEqual(status, 200, path)
            self.assertEqual(payload["object"], "list")
            by_id = {entry["id"]: entry for entry in payload["data"]}
            self.assertEqual(set(by_id), set(PROFILES))
            self.assertTrue(by_id["unit_text_q8_vision"]["supports_vision"])
            self.assertFalse(by_id["unit_text_q8"]["supports_vision"])

    def test_non_stream_chat_shape(self):
        status, payload = http_post_json(
            self.fixture.url("/studio-suite/llm/v1/chat/completions"),
            {
                "model": "unit_text_q8",
                "messages": [
                    {"role": "system", "content": "be terse"},
                    {"role": "user", "content": "say hi"},
                ],
                "temperature": 0.2,
                "max_tokens": 64,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertTrue(payload["id"].startswith("chatcmpl-"))
        self.assertEqual(payload["model"], "unit_text_q8")
        choice = payload["choices"][0]
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertEqual(choice["message"]["content"], "fake answer")
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertIn("usage", payload)
        self.assertEqual(
            set(payload["usage"]),
            {"prompt_tokens", "completion_tokens", "total_tokens"},
        )
        # The fake backend received the normalized payload.
        forwarded = self.recorded[-1]
        self.assertEqual(forwarded["profile_key"], "unit_text_q8")
        self.assertEqual(forwarded["payload"]["messages"][1]["content"], "say hi")
        self.assertEqual(forwarded["payload"]["temperature"], 0.2)
        self.assertEqual(forwarded["payload"]["max_tokens"], 64)

    def test_base64_image_is_forwarded_and_auto_selects_vision(self):
        status, payload = http_post_json(
            self.fixture.url("/v1/chat/completions"),
            {
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this image"},
                        {"type": "image_url", "image_url": {"url": TINY_PNG_DATA_URL}},
                    ],
                }],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["model"], "unit_text_q8_vision")
        forwarded = self.recorded[-1]
        self.assertEqual(forwarded["profile_key"], "unit_text_q8_vision")
        parts = forwarded["payload"]["messages"][0]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "describe this image"})
        self.assertEqual(parts[1]["type"], "image_url")
        self.assertEqual(parts[1]["image_url"]["url"], TINY_PNG_DATA_URL)

    def test_remote_image_url_is_rejected(self):
        status, payload = http_post_json(
            self.fixture.url("/v1/chat/completions"),
            {
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
                    ],
                }],
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertEqual(payload["error"]["code"], "remote_image_url_not_allowed")
        self.assertEqual(self.recorded, [])

    def test_stream_chat_sends_sse_chunks(self):
        body = json.dumps({
            "model": "unit_text_q8",
            "stream": True,
            "messages": [{"role": "user", "content": "stream please"}],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.fixture.url("/v1/chat/completions"),
            data=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        events = []
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/event-stream", response.headers.get("Content-Type", ""))
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if line:
                    events.append(line)
        self.assertTrue(all(line.startswith("data:") for line in events))
        self.assertEqual(events[-1], "data: [DONE]")
        chunks = [json.loads(line[len("data:"):].strip()) for line in events[:-1]]
        self.assertEqual(chunks[0]["object"], "chat.completion.chunk")
        self.assertEqual(chunks[0]["choices"][0]["delta"]["role"], "assistant")
        contents = [
            chunk["choices"][0]["delta"].get("content")
            for chunk in chunks
            if chunk["choices"][0]["delta"].get("content")
        ]
        self.assertEqual("".join(contents), "fake answer")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_oversize_request_is_rejected(self):
        big_text = "x" * (ENABLED_CONFIG["max_request_bytes"] + 4096)
        status, payload = http_post_json(
            self.fixture.url("/v1/chat/completions"),
            {"model": "unit_text_q8", "messages": [{"role": "user", "content": big_text}]},
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "request_too_large")
        self.assertEqual(self.recorded, [])

    def test_invalid_json_is_rejected(self):
        status, payload = http_post_json(self.fixture.url("/v1/chat/completions"), b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_unknown_model_returns_openai_404(self):
        status, payload = http_post_json(
            self.fixture.url("/v1/chat/completions"),
            {"model": "gpt-9000", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "model_not_found")
        self.assertEqual(payload["error"]["param"], "model")

    def test_single_flight_returns_429_while_busy(self):
        # Dedicated fixture whose fake backend blocks until released.
        recorded = []
        started = threading.Event()
        release = threading.Event()
        server = make_server(recorded, started_event=started, release_event=release)
        with ServerFixture(server) as fixture:
            result = {}

            def first_request():
                result["first"] = http_post_json(
                    fixture.url("/v1/chat/completions"),
                    {"model": "unit_text_q8", "messages": [{"role": "user", "content": "slow"}]},
                    timeout=30,
                )

            thread = threading.Thread(target=first_request, daemon=True)
            thread.start()
            self.assertTrue(started.wait(timeout=10))
            status, payload = http_post_json(
                fixture.url("/v1/chat/completions"),
                {"model": "unit_text_q8", "messages": [{"role": "user", "content": "fast"}]},
            )
            self.assertEqual(status, 429)
            self.assertEqual(payload["error"]["code"], "server_busy")
            release.set()
            thread.join(timeout=20)
            self.assertEqual(result["first"][0], 200)
            self.assertEqual(result["first"][1]["choices"][0]["message"]["content"], "fake answer")

    def test_queue_busy_returns_409(self):
        recorded = []
        server = OPENAI.OpenAICompatServer(
            config=dict(ENABLED_CONFIG),
            profiles=dict(PROFILES),
            completer_factory=make_fake_factory(recorded),
            queue_state_fn=lambda: {"busy": True, "running": 1, "pending": 0},
        )
        with ServerFixture(server) as fixture:
            status, payload = http_post_json(
                fixture.url("/v1/chat/completions"),
                {"model": "unit_text_q8", "messages": [{"role": "user", "content": "hi"}]},
            )
            self.assertEqual(status, 409)
            self.assertEqual(payload["error"]["code"], "comfy_queue_busy")
        self.assertEqual(recorded, [])


class ConfigLoadingTests(unittest.TestCase):
    def test_example_config_disables_by_default(self):
        config = OPENAI.load_server_config(PACKAGE_DIR)
        # No local override file exists in the repo, so defaults + example win.
        self.assertFalse(OPENAI.resolve_enabled(config))

    def test_env_override_wins_over_config(self):
        import os

        config = dict(ENABLED_CONFIG, enabled=False)
        os.environ[OPENAI.ENABLE_ENV_VAR] = "1"
        try:
            self.assertTrue(OPENAI.resolve_enabled(config))
        finally:
            os.environ.pop(OPENAI.ENABLE_ENV_VAR, None)
        config = dict(ENABLED_CONFIG, enabled=True)
        os.environ[OPENAI.ENABLE_ENV_VAR] = "off"
        try:
            self.assertFalse(OPENAI.resolve_enabled(config))
        finally:
            os.environ.pop(OPENAI.ENABLE_ENV_VAR, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
