"""Tests for `scripts/audit_caption_dataset.py`.

Pure filesystem tests: no ComfyUI, no model, no network.

    python -B scripts/test_audit_caption_dataset.py
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "audit_caption_dataset.py"
spec = importlib.util.spec_from_file_location("audit_caption_dataset", str(SCRIPT))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditDirTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, stem, caption, image=True):
        if image:
            (self.base / (stem + ".png")).write_bytes(b"")
        (self.base / (stem + ".txt")).write_text(caption, encoding="utf-8")

    def messages(self, issues):
        return [message for _path, message in issues]

    def test_clean_caption_has_no_issues(self):
        self.add("a", "a cat sits on a wooden mat")
        count, issues = audit.audit_dir(self.base, "", 3, 20)
        self.assertEqual(count, 1)
        self.assertEqual(issues, [])

    def test_short_and_long_captions_are_reported(self):
        self.add("short", "cat")
        self.add("long", " ".join(["word"] * 25))
        _count, issues = audit.audit_dir(self.base, "", 3, 20)
        messages = self.messages(issues)
        self.assertTrue(any("too short" in message for message in messages))
        self.assertTrue(any("too long" in message for message in messages))

    def test_missing_trigger_prefix(self):
        self.add("a", "a cat sits on a wooden mat")
        _count, issues = audit.audit_dir(self.base, "@artist", 3, 20)
        self.assertTrue(any("missing trigger prefix" in message for message in self.messages(issues)))

    def test_trigger_prefix_accepts_matching_caption(self):
        self.add("a", "@artist a cat sits on a wooden mat")
        _count, issues = audit.audit_dir(self.base, "@artist", 3, 20)
        self.assertEqual(issues, [])

    def test_style_words_are_reported(self):
        self.add("a", "anime style portrait of a cat")
        _count, issues = audit.audit_dir(self.base, "", 3, 20)
        self.assertTrue(any("style word" in message for message in self.messages(issues)))

    def test_interpretive_sentence_is_reported(self):
        self.add("a", "A cat sits on a mat. The overall composition evokes themes of loneliness.")
        _count, issues = audit.audit_dir(self.base, "", 3, 20)
        self.assertTrue(any("interpretive" in message for message in self.messages(issues)))

    def test_content_sentence_with_conveying_is_not_interpretive(self):
        text = "A girl conveying excitement holds a sign above her head."
        self.assertEqual(audit.find_interpretive(text), "")

    def test_banned_terms_are_reported(self):
        self.add("a", "genshin impact heroine standing in a field")
        _count, issues = audit.audit_dir(self.base, "", 3, 20)
        self.assertTrue(any("banned term" in message for message in self.messages(issues)))

    def test_empty_caption_is_reported(self):
        self.add("a", "")
        _count, issues = audit.audit_dir(self.base, "", 3, 20)
        self.assertTrue(any("empty caption" in message for message in self.messages(issues)))

    def test_orphan_caption_is_reported(self):
        self.add("a", "a cat sits on a wooden mat", image=False)
        _count, issues = audit.audit_dir(self.base, "", 3, 20)
        self.assertTrue(any("orphan" in message for message in self.messages(issues)))

    def test_duplicate_captions_are_reported(self):
        caption = "a cat sits on a wooden mat"
        self.add("a", caption)
        self.add("b", caption)
        _count, issues = audit.audit_dir(self.base, "", 3, 20)
        self.assertTrue(any("duplicate" in message for message in self.messages(issues)))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, folder, *extra):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT),
                str(folder),
                "--min-words",
                "3",
                "--max-words",
                "20",
                *extra,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_exit_zero_for_clean_dataset(self):
        (self.base / "a.png").write_bytes(b"")
        (self.base / "a.txt").write_text("a cat sits on a wooden mat", encoding="utf-8")
        result = self.run_cli(self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exit_one_when_issues_found(self):
        (self.base / "a.png").write_bytes(b"")
        (self.base / "a.txt").write_text("cat", encoding="utf-8")
        result = self.run_cli(self.base)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("too short", result.stdout)

    def test_missing_folder_is_skipped_without_crash(self):
        result = self.run_cli(self.base / "missing")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip missing folder", result.stdout)


if __name__ == "__main__":
    unittest.main()
