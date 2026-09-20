"""Focused parser tests for the maintainer-only Wiki taxonomy importer."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wiki = load("test_wiki_taxonomy", "scripts/augment_tag_library_wiki_taxonomy.py")


class WikiTaxonomyParserTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE tags(canonical TEXT PRIMARY KEY,count INTEGER);
            CREATE TABLE terms(term TEXT,canonical TEXT);
            CREATE TABLE danbooru_tags(canonical TEXT PRIMARY KEY,category INTEGER);
        """)
        for canonical, count in (("high_heels", 100), ("stiletto_heels", 80),
                                 ("pumps", 70), ("black_shoes", 60),
                                 ("fringe_trim", 10), ("boots", 90)):
            self.db.execute("INSERT INTO tags VALUES(?,?)", (canonical, count))
            self.db.execute("INSERT INTO terms VALUES(?,?)", (canonical.replace("_", " "), canonical))
            self.db.execute("INSERT INTO danbooru_tags VALUES(?,0)", (canonical,))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_headings_expands_nested_bullets_and_first_link_only(self):
        body = """
h4. Types
[expand=High heels]
* [[High heels]]
** [[Stiletto heels]] [tn]See [[fringe trim]].[/tn]
* pumps
** [[Pumps]]
[/expand]
[expand=Colors]
* [[Black shoes]]
[/expand]
h4. See also
* [[Boots]]
"""
        result = wiki.parse_page(self.db, "shoes", body, "wiki:attire:footwear", "鞋类", False)
        labels = {group.label for group in result.groups}
        self.assertIn("高跟鞋", labels)
        self.assertIn("颜色", labels)
        self.assertNotIn("See also", labels)
        tags = {tag for _, tag in result.memberships}
        self.assertEqual(tags, {"high_heels", "stiletto_heels", "pumps", "black_shoes"})
        self.assertNotIn("fringe_trim", tags)
        stiletto_groups = {group for group, tag in result.memberships if tag == "stiletto_heels"}
        self.assertTrue(any("high_heels" in group for group in stiletto_groups))

    def test_excluded_and_navigation_links_do_not_become_prompt_tags(self):
        body = """
[expand=Table of Contents]
* [[Boots]]
[/expand]
h4. Not shoes
* [[Boots]]
h4. Types
* [[Tag group:Attire]]
* [[Missing shoe]]
"""
        result = wiki.parse_page(self.db, "shoes", body, "wiki:root", "鞋类", False)
        self.assertEqual(result.memberships, set())
        self.assertEqual(result.unresolved, {"Missing shoe"})


if __name__ == "__main__":
    unittest.main()
