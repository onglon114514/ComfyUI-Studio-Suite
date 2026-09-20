"""Read-only real-corpus checks; optionally exercise ComfyUI in a separate browser.

Set STUDIO_SUITE_BROWSER_NODE to node.exe to enable the browser integration.
All API mutations except glossary lookup are blocked by the browser harness.
"""
import asyncio
import importlib.util
import json
import os
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("real_library_harness", ROOT / "scripts/test_prompt_glossary_api.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class RealLibraryTests(harness.GlossaryApiTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.api._TAG_LIBRARY = self.api.TagLibrary(ROOT)
        self.assertTrue(self.api._TAG_LIBRARY.status()["ready"], "Build the local dictionary first")

    async def test_lookup_both_directions_and_missing_term(self):
        r = await self.client.post('/studio-suite/prompt-studio/glossary_lookup', json={"terms": ["blue_eyes"], "language": "zh"})
        self.assertEqual(r.status, 200)
        data = await r.json()
        self.assertEqual(len(data['matches']), 1)
        self.assertTrue(data['matches'][0]['local'])
        translated = data['matches'][0]['local']
        r = await self.client.post('/studio-suite/prompt-studio/glossary_lookup', json={"terms": [translated], "language": "en"})
        data = await r.json()
        self.assertTrue(data['matches'] or data['ambiguous'])

    async def test_real_search_paging_detail_and_related(self):
        base = '/studio-suite/prompt-studio/library/'
        timings = {}
        for label, params in [('browse', {}), ('english', {'q': 'blue'}), ('chinese', {'q': '蓝'}),
                              ('character', {'kind': 'character'}), ('artist', {'kind': 'artist'})]:
            elapsed = []
            for _ in range(5):
                start = time.perf_counter()
                r = await self.client.get(base + 'search', params=params)
                data = await r.json()
                elapsed.append(round((time.perf_counter() - start) * 1000, 2))
                self.assertEqual(r.status, 200)
                self.assertGreater(len(data['items']), 0)
                self.assertLessEqual(len(data['items']), 40)
            timings[label] = elapsed
        r = await self.client.get(base + 'search')
        page1 = await r.json()
        r = await self.client.get(base + 'search', params={'cursor': page1['next_cursor']})
        page2 = await r.json()
        self.assertFalse({x['canonical'] for x in page1['items']} & {x['canonical'] for x in page2['items']})
        r = await self.client.get(base + 'detail', params={'canonical': 'blue_eyes'})
        self.assertEqual((await r.json())['item']['canonical'], 'blue_eyes')
        start = time.perf_counter()
        r = await self.client.get(base + 'related', params={'canonical': 'blue_eyes', 'limit': 20})
        data = await r.json()
        timings['related_ms'] = round((time.perf_counter() - start) * 1000, 2)
        self.assertEqual(len(data['items']), 20)
        counts = [x['count'] for x in data['items']]
        self.assertEqual(counts, sorted(counts, reverse=True))
        print(json.dumps({'counts': self.api._TAG_LIBRARY.status()['counts'], 'http_ms': timings}))

    async def test_real_browser(self):
        node = os.environ.get('STUDIO_SUITE_BROWSER_NODE')
        if not node:
            self.skipTest('STUDIO_SUITE_BROWSER_NODE is not set')
        env = dict(os.environ, STUDIO_SUITE_LIBRARY_API=str(self.client.make_url('/')))
        proc = await asyncio.create_subprocess_exec(node, str(ROOT / 'scripts/test_tag_library_real_frontend.cjs'),
                                                    env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), 180)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        print(output.decode('utf-8', errors='replace'))
        self.assertEqual(proc.returncode, 0)


if __name__ == '__main__':
    unittest.main()
