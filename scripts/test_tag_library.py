"""Offline regression suite. Fixtures and SQLite scratch stay in this project.

Run: python -B scripts/test_tag_library.py
No installed node resources, user dictionaries, model, or network are touched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


library_module = load("test_public_library", "prompt_studio/tag_library.py")
builder = load("test_public_builder", "scripts/build_tag_library.py")
PATHS = {key: path for key, path, _ in builder.SOURCES}


class TagLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".tag-library-test-", dir=ROOT)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.package = self.root / "package"
        self.output = self.package / "resources/danbooru_library"
        self.library = library_module.TagLibrary(self.package)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, source_id, content):
        path = self.source / PATHS[source_id]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def jsonl(self, source_id, rows):
        return self.write(source_id, "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows))

    def fixture(self):
        self.write("autocomplete", "blue_eyes,100\nred_eyes,80\nshared_hat,70\n" +
                   "".join(f"filler_{i:03d},{i}\n" for i in range(130)))
        self.write("bilingual", "blue_eyes,\u84dd\u773c\u775b/\u84dd\u77b3\nred_eyes,\u7ea2\u773c\n")
        self.output.mkdir(parents=True)
        (self.output / "categories.v1.json").write_text(json.dumps({"semantic_groups": [
            {"id": "group:root", "label_zh": "root", "parent_id": None},
            {"id": "group:a", "label_zh": "A", "parent_id": "group:root"},
            {"id": "group:b", "label_zh": "B", "parent_id": "group:root"},
            {"id": "studio:proposed", "label_zh": "proposal", "review_status": "proposed"},
        ]}), encoding="utf-8")
        self.jsonl("clothing", [
            {"node_type": "tag", "tag": "shared_hat", "name_zh": "\u5171\u7528\u5e3d", "parent_ids": ["group:a"], "definition": "clothing definition"},
            {"node_type": "tag", "tag": "shared_hat", "name_zh": "\u5171\u7528\u5e3d", "parent_ids": ["group:b"]},
            {"node_type": "tag", "tag": "red_eyes", "parent_ids": ["group:b"]},
        ])
        self.jsonl("characters", [
            {"character\u89d2\u8272": "alice_(one)", "character\u89d2\u8272_2": "\u7231\u4e3d\u4e1d \u2013 first work",
             "aliases": ["\u7231\u4e3d\u4e1d", "Alice", "one_alice"], "count": 7, "copyright\u4f5c\u54c1": "one two"},
            {"canonical": "alice_(two)", "display_zh": "\u7231\u4e3d\u4e1d \u2013 second work", "aliases": ["\u7231\u4e3d\u4e1d", "Alice"]},
            {"canonical": "hero_(only)", "display_zh": "\u72ec\u7279\u82f1\u96c4", "aliases": [f"hero_alias_{i:02d}" for i in range(60)]},
            {"canonical": 123, "display_zh": "invalid numeric key"},
        ])
        self.write("manual_aliases", json.dumps({"alice_(one)": {"aliases": ["\u4e00\u53f7\u7231\u4e3d\u4e1d", "manual_alice"],
                                                                  "blocked_tags": ["wrong_alice"]}}))
        self.write("artists", "artist, trigger, count, url\nsome_artist,artist trigger\nanother_artist\n")
        self.write("cooccurrence", "tag_a,tag_b,count\nblue_eyes,z_tail,1\nblue_eyes,a_front,20.0\n"
                   "a_front,blue_eyes,10\nblue_eyes,a_front,19\nblue_eyes,red_eyes,99\n"
                   "strongest,blue_eyes,500.0\nblue_eyes,blue_eyes,40\nblue_eyes,invalid,-1\n"
                   "blue_eyes,nan,nan\nblue_eyes,fraction,1.5\n,empty,3\nblue_eyes,zero,0\n")

    def build(self, large=True):
        return builder.build_library(self.source, self.output, include_large=large)

    def test_missing_and_damaged_library_never_build(self):
        self.assertFalse(self.library.status()["ready"])
        self.assertEqual(self.library.search(), {"items": [], "next_cursor": ""})
        self.assertEqual(self.library.categories(), {"items": []})
        self.assertEqual(self.library.related("x"), {"items": []})
        self.assertEqual(self.library.detail("x"), {})
        self.assertEqual(self.library.lookup(["x"])["unmatched"], ["x"])
        self.assertFalse(self.output.exists())
        self.output.mkdir(parents=True)
        for invalid in ("{", "[]", json.dumps({"schema_version": 1, "database": "../../outside.sqlite3"})):
            (self.output / "manifest.json").write_text(invalid, encoding="utf-8")
            self.assertFalse(self.library.status()["ready"])
        self.assertEqual([p.name for p in self.output.iterdir()], ["manifest.json"])

    def test_coverage_regression_requires_explicit_override(self):
        self.fixture()
        manifest = self.build()
        manifest["counts"]["tag_count"] += 1
        manifest_path = self.output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        preserved = manifest_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "coverage regression"):
            self.build()
        self.assertEqual(manifest_path.read_bytes(), preserved)

        replacement = builder.build_library(
            self.source,
            self.output,
            allow_coverage_regression=True,
        )
        self.assertLess(replacement["counts"]["tag_count"], manifest["counts"]["tag_count"])

    def test_chinese_english_alias_prefix_and_exact_lookup(self):
        self.fixture()
        self.build()
        for q in ("blue_", "BLUE E", "\u84dd\u773c", "\u84dd\u77b3"):
            self.assertEqual(self.library.search(q=q)["items"][0]["canonical"], "blue_eyes")
        self.assertEqual(self.library.search("manual_al")["items"][0]["canonical"], "alice_(one)")
        self.assertEqual(self.library.search("artist tri")["items"][0]["kind"], "artist")
        self.assertEqual(self.library.lookup(["\u84dd\u77b3"], "en")["matches"][0]["local"], "blue_eyes")
        self.assertEqual(self.library.lookup([r"hero_\(only\)"])["matches"][0]["local"], "\u72ec\u7279\u82f1\u96c4")
        self.assertEqual(self.library.lookup(["\u4e00\u53f7\u7231\u4e3d\u4e1d"], "en")["matches"][0]["source"], "manual_aliases")
        self.assertEqual(self.library.search("eyes")["items"], [])

    def test_page_sort_initial_and_work_navigation(self):
        self.fixture()
        self.build()
        first = self.library.search(limit=10, page=1)
        third = self.library.search(limit=10, page=3)
        self.assertGreater(first["total_count"], 100)
        self.assertEqual(first["total_pages"], (first["total_count"] + 9) // 10)
        self.assertEqual(third["page"], 3)
        self.assertFalse(
            {item["canonical"] for item in first["items"]}
            & {item["canonical"] for item in third["items"]}
        )
        popular = self.library.search(limit=10, sort="popular")["items"]
        counts = [item["count"] for item in popular]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertTrue(all(item["canonical"].startswith("a") for item in self.library.search(initial="a")["items"]))

        works = self.library.works("one")
        self.assertEqual(works["items"][0]["name"], "one two")
        self.assertEqual(
            [item["canonical"] for item in self.library.search(work="one")["items"]],
            ["alice_(one)"],
        )

        for options in ({"page": 0}, {"page": 100001}, {"sort": "random"}, {"initial": "aa"}):
            with self.assertRaises(ValueError):
                self.library.search(**options)

    def test_alias_ambiguity_preserved_in_both_languages(self):
        self.fixture()
        self.build()
        for language in ("zh", "en"):
            result = self.library.lookup(["\u7231\u4e3d\u4e1d", "Alice", "unknown"], language)
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["ambiguous"], ["\u7231\u4e3d\u4e1d", "Alice"])
            self.assertEqual(result["unmatched"], ["unknown"])
        self.assertEqual(len(self.library.search("\u7231\u4e3d\u4e1d")["items"]), 2)
        self.assertEqual(self.library.lookup(["wrong_alice"])["matches"], [])

    def test_multiple_groups_ancestors_proposals_and_kind(self):
        self.fixture()
        self.build()
        groups = {g["id"]: g for g in self.library.categories()["items"]}
        self.assertEqual(groups["group:root"]["count"], 2)
        self.assertEqual(groups["studio:proposed"]["count"], 0)
        self.assertEqual(groups["studio:proposed"]["review_status"], "proposed")
        detail = self.library.detail("shared_hat")
        self.assertEqual({g["id"] for g in detail["groups"]}, {"group:a", "group:b", "group:root"})
        self.assertEqual(detail["kind"], "unknown")
        self.assertIsNone(detail["official_category"])
        self.assertEqual(detail["description"], "clothing definition")
        self.assertEqual(len(self.library.search(category="group:b")["items"]), 2)
        self.assertEqual(len(self.library.search(kind="artist")["items"]), 2)
        self.assertEqual(self.library.search(kind="general")["items"], [])
        self.assertEqual(self.library.detail("one two"), {})

    def test_stable_keyset_pagination_no_duplicate_alias_hits(self):
        self.fixture()
        self.build()
        for options in ({}, {"q": "filler_"}, {"category": "group:root"}, {"kind": "character"}, {"q": "alice"}):
            cursor, seen = "", []
            while True:
                page = self.library.search(**options, cursor=cursor, limit=2)
                seen.extend(item["canonical"] for item in page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            self.assertEqual(seen, sorted(set(seen)))
            expected = self.library.search(**options, limit=100)["items"]
            self.assertEqual(seen[:100], [item["canonical"] for item in expected])
        cursor = self.library.search(limit=1)["next_cursor"]
        with self.assertRaises(ValueError):
            self.library.search(q="blue", cursor=cursor)
        with self.assertRaises(ValueError):
            self.library.search(cursor="not-base64!")
        self.build()
        with self.assertRaises(ValueError):
            self.library.search(cursor=cursor)

    def test_limits_and_input_validation(self):
        self.fixture()
        self.build()
        self.assertEqual(len(self.library.search(limit=99999)["items"]), 100)
        self.assertEqual(len(self.library.detail("hero_(only)")["aliases"]), 40)
        self.assertTrue(self.library.detail("hero_(only)")["aliases_truncated"])
        for limit in (0, -1, True, "40", 1.5):
            with self.assertRaises(ValueError):
                self.library.search(limit=limit)
            with self.assertRaises(ValueError):
                self.library.related("blue_eyes", limit=limit)
        for bad in (["x"] * 513, "x", [123], ["x" * 8193], ["x" * 8192] * 15):
            with self.assertRaises(ValueError):
                self.library.lookup(bad)
        with self.assertRaises(ValueError):
            self.library.lookup([], "fr")
        with self.assertRaises(ValueError):
            self.library.search(kind="guessed")

    def test_lookup_separate_ab_limits_and_long_sentences(self):
        self.fixture()
        self.build()
        self.assertEqual(len(self.library.lookup(["blue_eyes"] * 512)["matches"]), 512)
        long_text = "sentence " * 900
        result = self.library.lookup(["blue_eyes", long_text, "x" * 8192])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["unmatched"], [long_text, "x" * 8192])

    def test_non_chinese_display_is_not_reported_as_chinese_translation(self):
        self.jsonl("characters", [{"canonical": "hero", "display_zh": "Hero from a work"}])
        self.build()
        self.assertEqual(self.library.lookup(["hero"], 'zh')['unmatched'], ['hero'])
        self.assertEqual(self.library.lookup(["hero"], 'en')['matches'][0]['local'], 'hero')

    def test_existing_build_lock_is_never_deleted(self):
        self.write("autocomplete", "tag,1\n")
        self.output.mkdir(parents=True)
        lock = self.output / "build.lock"
        lock.write_text("another process", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "concurrent builds"):
            self.build()
        self.assertEqual(lock.read_text(encoding="utf-8"), "another process")
        lock.unlink()
        self.build()
        self.assertFalse(lock.exists())

    def test_bidirectional_true_top_and_max_dedup(self):
        self.fixture()
        manifest = self.build()
        items = self.library.related("blue_eyes", limit=3)["items"]
        self.assertEqual([(r["canonical"], r["count"]) for r in items],
                         [("strongest", 500), ("red_eyes", 99), ("a_front", 20)])
        self.assertEqual(self.library.related("strongest")["items"][0]["count"], 500)
        self.assertEqual(self.library.related("a_front")["items"][0]["count"], 20)
        source = next(s for s in manifest["sources"] if s["id"] == "cooccurrence")
        self.assertEqual((source["imported"], source["rejected"], source["duplicate_or_reverse_records"]), (6, 6, 2))
        self.assertEqual(manifest["counts"]["cooccurrence_count"], 4)

    def test_manifest_provenance_and_unverified_counts(self):
        self.fixture()
        manifest = self.build()
        for source in manifest["sources"]:
            if source["status"] != "imported":
                continue
            root = self.output if source["location"] == "output_dir" else self.source
            path = root / source["path"]
            self.assertFalse(Path(source["path"]).is_absolute())
            self.assertEqual(source["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(source["size"], path.stat().st_size)
            self.assertEqual(source["mtime_ns"], path.stat().st_mtime_ns)
        self.assertIn("not_imported", manifest["coverage"]["generated_aliases"])
        detail = self.library.detail("blue_eyes")
        self.assertEqual((detail["count"], detail["count_source"]), (100, "autocomplete"))
        self.assertEqual(detail["count_scope"], "source_snapshot_unverified")
        self.assertIsNone(self.library.detail("some_artist")["count"])
        self.assertEqual(next(s for s in manifest["sources"] if s["id"] == "characters")["rejected"], 1)

    def test_small_build_keeps_manual_aliases_and_missing_resources_degrade(self):
        self.fixture()
        manifest = self.build(large=False)
        self.assertTrue(self.library.status()["ready"])
        self.assertEqual(self.library.related("blue_eyes")["items"], [])
        self.assertEqual(self.library.detail("hero_(only)"), {})
        self.assertEqual(self.library.lookup(["manual_alice"])["matches"][0]["canonical"], "alice_(one)")
        self.assertEqual(next(s for s in manifest["sources"] if s["id"] == "artists")["status"], "skipped")
        (self.source / PATHS["bilingual"]).unlink()
        self.build(large=False)
        self.assertEqual(self.library.detail("blue_eyes")["label_zh"], "")

    def test_failed_input_and_failed_publication_keep_old_library(self):
        self.fixture()
        old = self.build()
        pointer = (self.output / "manifest.json").read_bytes()
        old_names = {p.name for p in self.output.iterdir()}
        real_replace = os.replace

        def fail_pointer(source, destination):
            if Path(destination).name == "manifest.json":
                raise OSError("simulated publish failure")
            return real_replace(source, destination)

        with patch.object(builder.os, "replace", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                self.build()
        self.assertEqual((self.output / "manifest.json").read_bytes(), pointer)
        self.assertEqual({p.name for p in self.output.iterdir()}, old_names)
        self.write("manual_aliases", "{")
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(self.library.status()["version"], old["version"])
        self.assertEqual((self.output / "manifest.json").read_bytes(), pointer)
        self.assertEqual({p.name for p in self.output.iterdir()}, old_names)

    def test_source_mutation_and_empty_build_do_not_publish(self):
        self.write("autocomplete", "blue_eyes,3\n")
        old = self.build()
        original = builder._Importer.autocomplete

        def mutate(importer, lines, source):
            original(importer, lines, source)
            self.write("autocomplete", "new_tag,1000\n")

        with patch.object(builder._Importer, "autocomplete", mutate):
            with self.assertRaisesRegex(ValueError, "source changed"):
                self.build()
        self.assertEqual(self.library.status()["version"], old["version"])
        self.write("autocomplete", "broken,-1\n")
        with self.assertRaisesRegex(ValueError, "no valid tags"):
            self.build()
        self.assertEqual(self.library.status()["version"], old["version"])

    def test_connections_are_read_only_closed_and_old_versions_retained(self):
        self.write("autocomplete", "blue_eyes,3\n")
        old = self.build()
        with self.library._snapshot() as (db, _, __):
            self.assertEqual(db.execute("PRAGMA temp_store").fetchone()[0], 2)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM tags")
            self.write("autocomplete", "new_tag,10\n")
            new = self.build()
            self.assertEqual(db.execute("SELECT canonical FROM tags").fetchone()[0], "blue_eyes")
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        self.assertNotEqual(new["version"], old["version"])
        self.assertTrue((self.output / old["database"]).exists())
        self.assertEqual(self.library.search()["items"][0]["canonical"], "new_tag")

    def test_indexed_prefix_browse_and_both_edge_directions(self):
        self.fixture()
        self.build()
        with self.library._snapshot() as (db, _, __):
            queries = [
                ("SELECT canonical FROM terms WHERE term>=? AND term<?", ("blue", "bluf")),
                ("SELECT canonical FROM tags WHERE canonical>? ORDER BY canonical LIMIT 4", ("blue",)),
                ("SELECT canonical FROM tag_groups WHERE group_id=? AND canonical>? ORDER BY canonical LIMIT 4", ("group:a", "")),
                ("SELECT b,count FROM edges WHERE a=? ORDER BY count DESC,b LIMIT 2", ("blue_eyes",)),
                ("SELECT a,count FROM edges WHERE b=? ORDER BY count DESC,a LIMIT 2", ("blue_eyes",)),
            ]
            for sql, values in queries:
                plan = " ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql, values))
                self.assertIn("SEARCH", plan)
                self.assertNotIn("SCAN ", plan)
                self.assertNotIn("TEMP B-TREE", plan)

    def test_sql_and_like_metacharacters_are_only_data(self):
        self.write("autocomplete", "x'); DROP TABLE tags;--,1\npercent%tag,2\npercentXtag,3\n")
        self.build()
        self.assertEqual(len(self.library.search("x'); DROP")["items"]), 1)
        self.assertEqual([i["canonical"] for i in self.library.search("percent%")["items"]], ["percent%tag"])
        self.assertEqual(self.library.status()["counts"]["tag_count"], 3)

    def test_malformed_jsonl_and_counts_are_rejected(self):
        self.write("autocomplete", "valid,10\ninvalid,nan\noverflow,999999999999999999999\n")
        self.write("characters", '{bad}\n[]\n{"canonical": 123}\n{"canonical":"good","aliases":[null,3,"valid_alias"]}\n')
        self.build()
        self.assertEqual(self.library.search("valid_alias")["items"][0]["canonical"], "good")
        sources = {s["id"]: s for s in self.library.status()["sources"]}
        self.assertEqual(sources["characters"]["rejected"], 3)
        self.assertEqual(sources["autocomplete"]["rejected"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
