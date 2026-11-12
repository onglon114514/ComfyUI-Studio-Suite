"""Tests for the pure helpers behind the Independent Prompt Folder Queue.

Run with the ComfyUI Python interpreter and ComfyUI on PYTHONPATH:

    set COMFYUI_ROOT=<ComfyUI>
    set PYTHONPATH=<ComfyUI>
    python -B scripts/test_queue_nodes.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
COMFY_ROOT = str(os.environ.get("COMFYUI_ROOT", "") or "").strip()
for candidate in (str(PACKAGE_DIR), COMFY_ROOT):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

import queue_nodes


class QueueFolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self._touch("top_b.PNG")
        self._touch("top_a.png")
        self._touch("artist_a/img1.png")
        self._touch("artist_a/img2.jpg")
        self._touch("artist_b/img1.webp")
        self._touch("artist_b/nested/deep.png")
        self._touch("artist_b/readme.txt")

    def tearDown(self):
        self._tmp.cleanup()

    def _touch(self, relative):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def names(self, files):
        return [str(path.relative_to(self.base)).replace("\\", "/") for path in files]

    def test_top_level_scan_ignores_subfolders_and_text(self):
        files = queue_nodes._sorted_image_files(self.base, "name_asc", False)
        self.assertEqual(self.names(files), ["top_a.png", "top_b.PNG"])

    def test_subfolder_scan_is_sorted_by_relative_path(self):
        files = queue_nodes._sorted_image_files(self.base, "name_asc", True)
        self.assertEqual(
            self.names(files),
            [
                "artist_a/img1.png",
                "artist_a/img2.jpg",
                "artist_b/img1.webp",
                "artist_b/nested/deep.png",
                "top_a.png",
                "top_b.PNG",
            ],
        )

    def test_name_desc_is_reverse_of_name_asc(self):
        asc = queue_nodes._sorted_image_files(self.base, "name_asc", True)
        desc = queue_nodes._sorted_image_files(self.base, "name_desc", True)
        self.assertEqual(self.names(desc), list(reversed(self.names(asc))))

    def test_mtime_sort_is_stable_and_respects_direction(self):
        early = self._touch("artist_a/img1.png")
        late = self._touch("artist_b/img1.webp")
        os.utime(early, (1000, 1000))
        os.utime(late, (2000, 2000))
        asc = queue_nodes._sorted_image_files(self.base, "mtime_asc", True)
        self.assertLess(asc.index(early), asc.index(late))
        desc = queue_nodes._sorted_image_files(self.base, "mtime_desc", True)
        self.assertLess(desc.index(late), desc.index(early))

    def test_missing_folder_raises(self):
        with self.assertRaises(ValueError):
            queue_nodes._sorted_image_files(self.base / "missing", "name_asc")


class DuplicateStemTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_duplicate_stems_across_folders_are_paired(self):
        first = self.base / "artist_a" / "same.png"
        second = self.base / "artist_b" / "same.png"
        other = self.base / "artist_b" / "other.png"
        pairs = queue_nodes._duplicate_stem_pairs([first, second, other])
        self.assertEqual(pairs, [(first, second)])

    def test_unique_stems_return_empty(self):
        files = [self.base / "a" / "one.png", self.base / "b" / "two.png"]
        self.assertEqual(queue_nodes._duplicate_stem_pairs(files), [])


class SubfolderHintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_hint_lists_first_subfolder(self):
        (self.base / "artist_a").mkdir()
        (self.base / "artist_b").mkdir()
        hint = queue_nodes._subfolder_hint(self.base)
        self.assertIn("found 2 subfolder(s)", hint)
        self.assertIn("artist_a", hint)
        self.assertIn("include_subfolders", hint)

    def test_no_hint_when_folder_has_no_subfolders(self):
        self.assertEqual(queue_nodes._subfolder_hint(self.base), "")

    def test_no_hint_for_missing_folder(self):
        self.assertEqual(queue_nodes._subfolder_hint(self.base / "missing"), "")


if __name__ == "__main__":
    unittest.main()
