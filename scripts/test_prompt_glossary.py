"""Offline regression tests; no ComfyUI import, model, or network required."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("studio_glossary", ROOT / "prompt_studio/local_dictionary.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GlossaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = self.root / "storage"
        self.csvs = self.storage / "local_complete_tags"
        self.csvs.mkdir(parents=True)
        (self.root / "resources").mkdir()
        self.dictionary = module.LocalDanbooruDictionary(self.root, self.storage)

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_dictionary_is_an_empty_result(self):
        self.assertEqual(self.dictionary.glossary_lookup(["unknown"]), {"matches": [], "available_entries": 0})

    def test_both_directions_and_normalized_tags(self):
        (self.csvs / "tags.csv").write_text("blue_eyes,蓝眼睛\nfoo_(bar),角色名\n", encoding="utf-8-sig")
        matches = self.dictionary.glossary_lookup(["blue eyes", r"foo_\(bar\)"])["matches"]
        self.assertEqual([m["local"] for m in matches], ["蓝眼睛", "角色名"])
        self.assertEqual(self.dictionary.glossary_lookup(["蓝眼睛"], "en")["matches"][0]["local"], "blue_eyes")

    def test_counts_are_not_translations(self):
        (self.csvs / "cooccurrence.csv").write_text("tag_a,tag_b,count\n1girl,solo,500\nblue_eyes,300\n", encoding="utf-8")
        self.assertEqual(self.dictionary.glossary_lookup(["1girl", "blue_eyes"])["matches"], [])

    def test_character_names_take_priority(self):
        (self.csvs / "tags.csv").write_text("character,普通释义\n", encoding="utf-8")
        (self.root / "resources/danbooru_character_aliases.json").write_text(json.dumps({"character": {"aliases": ["角色专名"], "display_zh": "角色专名"}}), encoding="utf-8")
        self.assertEqual(self.dictionary.glossary_lookup(["character"])["matches"][0]["local"], "角色专名")

    def test_resource_changes_invalidate_cache(self):
        path = self.csvs / "tags.csv"
        path.write_text("blue_eyes,蓝眼睛\n", encoding="utf-8")
        self.dictionary.glossary_lookup(["blue eyes"])
        path.write_text("blue_eyes,蓝色眼睛\n", encoding="utf-8")
        self.assertEqual(self.dictionary.glossary_lookup(["blue eyes"])["matches"][0]["local"], "蓝色眼睛")
        path.unlink()
        self.assertEqual(self.dictionary.glossary_lookup(["blue eyes"])["matches"], [])

    def test_damaged_optional_resources_do_not_break_lookup(self):
        (self.csvs / "bad.csv").write_text('x' * 200000 + ',错误\n', encoding="utf-8")
        (self.csvs / "good.csv").write_text("blue_eyes,蓝眼睛\n", encoding="utf-8")
        (self.root / "resources/danbooru_character_aliases.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.dictionary.glossary_lookup(["blue eyes"])["matches"][0]["local"], "蓝眼睛")


if __name__ == "__main__":
    unittest.main()
