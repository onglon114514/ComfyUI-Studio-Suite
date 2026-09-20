"""HTTP boundary/quick-library tests. No real user files or models are used."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("glossary_harness", ROOT / "scripts/test_prompt_glossary_api.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class FakeLibrary:
    def status(self):
        return {"ready": True, "counts": {"tag_count": 3}}

    def detail(self, canonical):
        return {"canonical": canonical, "label_zh": "蓝眼睛", "aliases": [], "groups": [], "sources": []}

    def search(self, **kwargs):
        self.search_args = kwargs
        return {"items": [self.detail("blue_eyes")], "next_cursor": ""}

    def related(self, canonical, limit):
        return {"items": []}

    def categories(self):
        return {"items": []}

    def works(self, q, limit):
        self.works_args = {"q": q, "limit": limit}
        return {"items": [{"name": "arknights", "character_count": 3}]}

    def lookup(self, terms, language):
        return {"matches": [], "ambiguous": terms}


class LibraryApiTests(harness.GlossaryApiTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.library = FakeLibrary()
        self.patcher = patch.object(self.api, "_TAG_LIBRARY", self.library)
        self.patcher.start()
        self.groups = Path(self.temporary.name) / "group_tags"
        self.groups.mkdir()
        self.source = self.groups / "zh_CN.yaml"
        self.source.write_text('- name: 我的组\n  groups:\n    - name: 常用\n      tags:\n        original: 原标签\n', encoding="utf-8")
        self.original = self.source.read_bytes()
        self.group_patcher = patch.object(self.api, "GROUP_TAGS_DIR", self.groups)
        self.group_patcher.start()

    async def asyncTearDown(self):
        self.group_patcher.stop()
        self.patcher.stop()
        await super().asyncTearDown()

    async def test_lookup_both_directions_and_missing_term(self):
        # The library ambiguity must not fall back to the old one-alias-one-name index.
        r = await self.client.post('/studio-suite/prompt-studio/glossary_lookup', json={"terms": ["ambiguous"]})
        data = await r.json()
        self.assertEqual(data["ambiguous"], ["ambiguous"])
        self.assertEqual(data["matches"], [])

    async def test_search_bounds_and_read_only_groups(self):
        r = await self.client.get('/studio-suite/prompt-studio/library/search?limit=999&page=7&sort=popular&initial=a&work=ark')
        self.assertEqual(r.status, 200)
        self.assertEqual(self.library.search_args["limit"], 100)
        self.assertEqual(self.library.search_args["page"], 7)
        self.assertEqual(self.library.search_args["sort"], "popular")
        self.assertEqual(self.library.search_args["initial"], "a")
        self.assertEqual(self.library.search_args["work"], "ark")
        r = await self.client.get('/studio-suite/prompt-studio/library/search?q=' + 'x' * 161)
        self.assertEqual(r.status, 400)
        r = await self.client.get('/studio-suite/prompt-studio/library/works?q=ark&limit=999')
        self.assertEqual(r.status, 200)
        self.assertEqual(self.library.works_args, {"q": "ark", "limit": 100})
        r = await self.client.get('/studio-suite/prompt-studio/library/quick-groups')
        self.assertEqual(r.status, 200)
        self.assertFalse((self.groups / 'custom.yaml').exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    async def test_missing_public_term_keeps_local_csv_without_legacy_aliases(self):
        self.library.lookup = lambda terms, language: {"matches": [], "ambiguous": ["ambiguous"], "unmatched": ["blue eyes"]}
        with patch.object(self.api._LOCAL_DICTIONARY, 'lookup_many', side_effect=AssertionError('legacy alias fallback')):
            r = await self.client.post('/studio-suite/prompt-studio/glossary_lookup', json={"terms": ["blue eyes", "ambiguous"]})
        self.assertEqual(r.status, 200)
        data = await r.json()
        self.assertEqual(data['matches'][0]['local'], '蓝眼睛')
        self.assertEqual(data['ambiguous'], ['ambiguous'])
        self.assertEqual(data['unmatched'], [])

    async def test_explicit_add_preserves_base_and_rejects_conflicts(self):
        r = await self.client.get('/studio-suite/prompt-studio/library/quick-groups')
        info = await r.json()
        body = {"canonical": "blue_eyes", "key": "我的组", "group": "常用", "revision": info["revision"]}
        r = await self.client.post('/studio-suite/prompt-studio/library/quick-add', json=body)
        self.assertEqual(r.status, 200)
        custom = self.groups / 'custom.yaml'
        content = custom.read_bytes()
        self.assertIn('original', content.decode('utf-8'))
        self.assertIn('blue_eyes', content.decode('utf-8'))
        self.assertEqual(self.source.read_bytes(), self.original)
        r = await self.client.post('/studio-suite/prompt-studio/library/quick-add', json=body)
        self.assertEqual(r.status, 409)
        r = await self.client.get('/studio-suite/prompt-studio/library/quick-groups')
        body['revision'] = (await r.json())['revision']
        r = await self.client.post('/studio-suite/prompt-studio/library/quick-add', json=body)
        self.assertEqual(r.status, 409)
        self.assertEqual(custom.read_bytes(), content)


class LibraryPackagingTests(unittest.TestCase):
    def test_index_is_not_accidentally_bundled(self):
        spec = importlib.util.spec_from_file_location("package_test", ROOT / "scripts/build_release_preview.py")
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        for large in (False, True):
            for name in ('manifest.json', 'catalog-a.sqlite3', '.catalog-a.building.sqlite3', 'build.lock', '.manifest-a.tmp'):
                self.assertTrue(package.should_skip(ROOT / 'resources/danbooru_library' / name, large))
            for name in ('prompt_studio/tag_library.py', 'prompt_studio/frontend/js/tagLibrary.js',
                         'resources/danbooru_library/categories.v1.json', 'scripts/build_tag_library.py'):
                self.assertFalse(package.should_skip(ROOT / name, large))


if __name__ == '__main__':
    unittest.main()
