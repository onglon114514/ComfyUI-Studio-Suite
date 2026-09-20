"""Exercise the actual aiohttp route without ComfyUI, model loading or user data."""
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]


class GlossaryApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        package = types.ModuleType("studio_glossary_api_test")
        package.__path__ = [str(ROOT / "prompt_studio")]
        server = types.ModuleType("server")
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(
            routes=web.RouteTableDef(), add_on_prompt_handler=lambda handler: None,
        ))
        with patch.dict(sys.modules, {package.__name__: package, "server": server, "folder_paths": types.ModuleType("folder_paths")}):
            api = importlib.import_module(package.__name__ + ".api")
        api.ROUTES_REGISTERED = False
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        csvs = root / "storage/local_complete_tags"
        csvs.mkdir(parents=True)
        (csvs / "tags.csv").write_text("blue_eyes,蓝眼睛\n", encoding="utf-8")
        api._LOCAL_DICTIONARY = api.LocalDanbooruDictionary(root, root / "storage")
        api._TAG_LIBRARY = api.TagLibrary(root)
        self.api = api
        api.PromptServer = server.PromptServer
        with patch.object(api, "_ensure_dirs"), patch.object(api, "_HistoryStore"):
            api.register_prompt_studio_routes()
        app = web.Application()
        app.add_routes(server.PromptServer.instance.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temporary.cleanup()

    async def test_lookup_both_directions_and_missing_term(self):
        for terms, lang, expected in [(["blue eyes"], "zh", "蓝眼睛"), (["蓝眼睛"], "en", "blue_eyes"), (["unknown"], "zh", None)]:
            response = await self.client.post("/studio-suite/prompt-studio/glossary_lookup", json={"terms": terms, "language": lang})
            self.assertEqual(response.status, 200)
            result = await response.json()
            self.assertTrue(result["success"])
            self.assertEqual([m["local"] for m in result["matches"]], [expected] if expected else [])

    async def test_reject_invalid_and_unbounded_input(self):
        for payload in [{"terms": "blue eyes"}, {"terms": [None]}, {"terms": ["a\x00b"]}, {"terms": ["a"] * 513}, {"terms": ["a" * 8193]}, {"terms": ["a" * 4000] * 31}, {"terms": [], "language": "bad"}]:
            response = await self.client.post("/studio-suite/prompt-studio/glossary_lookup", json=payload)
            self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
