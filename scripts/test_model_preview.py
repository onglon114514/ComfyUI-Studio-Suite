"""Prove the model-preview path renders off the event loop and honours its caches.

Regression guard for the 2026-09-22 change: PIL work used to run inside the async
handler, so a LoRA grid opening (a burst of preview requests) stalled every other
request on the loop. These tests fail if the render is put back inline.

Also covers the follow-up 2026-09-22 change: default thumbnail bound is 192 (not
the old 320), JPEG quality is 75, a `?w=` override up to 512 is honoured and is
part of both the in-memory and on-disk cache keys, and the disk cache serves a
second hit without re-rendering (proved via a render-call counter).
"""
import asyncio
import io
import importlib
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def _import_api():
    """Import prompt_studio.api without ComfyUI, the way the other *_api tests do."""
    package = types.ModuleType("studio_preview_test")
    package.__path__ = [str(ROOT / "prompt_studio")]
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(
        routes=web.RouteTableDef(), add_on_prompt_handler=lambda handler: None,
    ))
    with patch.dict(sys.modules, {
        package.__name__: package,
        "server": server,
        "folder_paths": types.ModuleType("folder_paths"),
    }):
        return importlib.import_module(package.__name__ + ".api")


api = _import_api()


class PreviewRenderTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def _make(self, name, size, mode="RGB", fmt="PNG"):
        path = self.dir / name
        Image.new(mode, size, (12, 34, 56, 255) if mode == "RGBA" else (12, 34, 56)).save(path, format=fmt)
        return path

    def test_downscales_to_the_configured_bound(self):
        path = self._make("big.png", (900, 600))
        rendered = api._render_preview_thumb(path)
        self.assertIsNotNone(rendered)
        body, content_type = rendered
        self.assertEqual(content_type, "image/jpeg")  # opaque -> JPEG
        with Image.open(io.BytesIO(body)) as img:
            self.assertLessEqual(max(img.size), api._PREVIEW_THUMB_MAX_SIZE)

    def test_default_bound_is_192(self):
        self.assertEqual(api._PREVIEW_THUMB_MAX_SIZE, 192)

    def test_new_config_is_smaller_than_legacy(self):
        # Legacy config rendered at 320 @ quality 85; new config is 192 @ quality 75.
        path = self._make("compare.png", (900, 600))
        rendered = api._render_preview_thumb(path)
        self.assertIsNotNone(rendered)
        new_body, content_type = rendered
        self.assertEqual(content_type, "image/jpeg")
        with Image.open(path) as img:
            img.thumbnail((320, 320))
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
            legacy_body = buffer.getvalue()
        self.assertLess(len(new_body), len(legacy_body),
                        "192@75 must be smaller than the legacy 320@85 render")

    def test_width_override_512_is_honoured(self):
        path = self._make("wide.png", (900, 600))
        rendered = api._render_preview_thumb(path, target_size=512)
        self.assertIsNotNone(rendered)
        body, _ = rendered
        with Image.open(io.BytesIO(body)) as img:
            self.assertEqual(max(img.size), 512)

    def test_width_parser_default_and_clamp(self):
        self.assertEqual(api._parse_preview_width(None), 192)
        self.assertEqual(api._parse_preview_width("512"), 512)
        self.assertEqual(api._parse_preview_width("9999"), 512)  # clamped to ceiling
        self.assertEqual(api._parse_preview_width("0"), 192)     # invalid -> default
        self.assertEqual(api._parse_preview_width("abc"), 192)   # non-numeric -> default

    def test_cache_key_includes_width(self):
        path = self._make("key.png", (900, 600))
        self.assertNotEqual(
            api._preview_cache_digest(path, 192),
            api._preview_cache_digest(path, 512),
        )

    def test_alpha_images_stay_png(self):
        path = self._make("alpha.png", (400, 400), mode="RGBA")
        rendered = api._render_preview_thumb(path)
        self.assertIsNotNone(rendered)
        self.assertEqual(rendered[1], "image/png")

    def test_undecodable_file_yields_none(self):
        path = self.dir / "broken.png"
        path.write_bytes(b"definitely not an image")
        self.assertIsNone(api._render_preview_thumb(path))


class PreviewResponseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        api._PREVIEW_THUMB_CACHE.clear()
        # Redirect the real disk-cache dir to the temp dir so tests never write
        # into prompt_studio/storage/preview_thumbs/.
        self._thumbs_dir = self.dir / "preview_thumbs"
        self._orig_thumbs_dir = api.PREVIEW_THUMBS_DIR
        api.PREVIEW_THUMBS_DIR = self._thumbs_dir

    def tearDown(self):
        api.PREVIEW_THUMBS_DIR = self._orig_thumbs_dir
        self._temp.cleanup()

    def _make(self, name, size=(640, 480), mode="RGB", fmt="PNG"):
        path = self.dir / name
        Image.new(mode, size, (1, 2, 3, 255) if mode == "RGBA" else (1, 2, 3)).save(path, format=fmt)
        return path

    async def test_thumb_response_is_cacheable(self):
        path = self._make("a.png")
        response = await api._preview_response(path)
        self.assertIsInstance(response, web.Response)
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=3600")
        self.assertEqual(response.content_type, "image/jpeg")
        self.assertTrue(response.body)

    async def test_second_call_is_served_from_memory(self):
        path = self._make("b.png")
        calls = {"n": 0}
        original = api._render_preview_thumb

        def counting(preview_path, target_size=api._PREVIEW_THUMB_MAX_SIZE):
            calls["n"] += 1
            return original(preview_path, target_size)

        with patch.object(api, "_render_preview_thumb", counting):
            await api._preview_response(path)
            await api._preview_response(path)
        self.assertEqual(calls["n"], 1, "the disk/memory cache should prevent a second render")

    async def test_disk_cache_second_hit_skips_render(self):
        path = self._make("c.png", (900, 600))
        calls = {"n": 0}
        original = api._render_preview_thumb

        def counting(preview_path, target_size=api._PREVIEW_THUMB_MAX_SIZE):
            calls["n"] += 1
            return original(preview_path, target_size)

        with patch.object(api, "_render_preview_thumb", counting):
            first = await api._preview_response(path)
            api._PREVIEW_THUMB_CACHE.clear()  # drop memory so the next hit must use disk
            second = await api._preview_response(path)

        self.assertEqual(calls["n"], 1, "disk cache must serve the second hit without re-rendering")
        self.assertIsInstance(first, web.Response)
        self.assertIsInstance(second, web.FileResponse)
        self.assertEqual(second.headers["Cache-Control"], "public, max-age=3600")
        # The thumbnail was actually written under the temp disk-cache dir.
        jpg, png = api._preview_thumb_disk_candidates(path, api._PREVIEW_THUMB_MAX_SIZE)
        self.assertTrue((jpg.exists() or png.exists()), "no disk-cache file was written")

    async def test_width_is_part_of_cache_key(self):
        path = self._make("d.png", (900, 600))
        calls = {"n": 0}
        original = api._render_preview_thumb

        def counting(preview_path, target_size=api._PREVIEW_THUMB_MAX_SIZE):
            calls["n"] += 1
            return original(preview_path, target_size)

        with patch.object(api, "_render_preview_thumb", counting):
            await api._preview_response(path, target_size=192)
            await api._preview_response(path, target_size=512)
        self.assertEqual(calls["n"], 2, "different widths must not share a cache entry")

    async def test_gif_is_streamed_as_a_file(self):
        path = self._make("c.gif", fmt="GIF")
        response = await api._preview_response(path)
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=3600")

    async def test_render_does_not_block_the_event_loop(self):
        """The whole point: a slow render must not stop other coroutines progressing."""
        path = self._make("slow.png")

        def slow_render(preview_path, target_size=api._PREVIEW_THUMB_MAX_SIZE):
            time.sleep(0.3)
            return (b"thumb", "image/jpeg")

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        with patch.object(api, "_render_preview_thumb", slow_render):
            task = asyncio.create_task(ticker())
            await api._preview_response(path)
            task.cancel()

        self.assertGreaterEqual(ticks, 3, "the loop was blocked while rendering the thumbnail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
