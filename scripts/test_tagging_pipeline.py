"""Unit tests for the tagging pipeline helpers in `task_agent_gateway.py`.

These tests never load a model and never touch the network: they only exercise the
pure helpers behind the parse-repair layer, the user banned-term filters and the
training-caption trigger prefix. Import cost is about one second (the gateway loads
its config/resource files at import time), so the module is imported directly.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))
COMFY_ROOT = str(os.environ.get("COMFYUI_ROOT", "") or "").strip()
if COMFY_ROOT and COMFY_ROOT not in sys.path:
    sys.path.insert(0, COMFY_ROOT)

import task_agent_gateway as gateway

TAGGING_TERMS_PATH = PACKAGE_DIR / "config" / "tagging_terms.json"
BUNDLE_DIR = PACKAGE_DIR / "resources" / "task_bundles"
LONG_CAPTION = (
    "A solo anime girl stands by a rain-streaked window in a quiet classroom, "
    "her hands folded in front of a pleated navy skirt and a crisp white blouse. "
    "Soft afternoon light falls across her face and highlights the ribbons in her hair. "
    "The camera sits at eye level, framing her from the waist up with a shallow background."
)


class RepairJsonishOutputTests(unittest.TestCase):
    def assertRepaired(self, text):
        parsed, ok = gateway._repair_jsonish_output(text)
        self.assertTrue(ok, f"应可修复：{text!r}")
        self.assertIsInstance(parsed, dict)
        return parsed

    def test_valid_json_still_parses(self):
        self.assertEqual(self.assertRepaired('{"a": 1}'), {"a": 1})

    def test_fenced_json_block(self):
        parsed = self.assertRepaired('```json\n{"caption_long_en": "A girl stands"}\n```')
        self.assertEqual(parsed["caption_long_en"], "A girl stands")

    def test_fenced_and_truncated(self):
        parsed = self.assertRepaired('```json\n{"caption_long_en": "A girl stands", "notes_cn": "ok"\n```')
        self.assertEqual(parsed["caption_long_en"], "A girl stands")

    def test_truncated_missing_closing_brace(self):
        parsed = self.assertRepaired('{"caption_long_en": "A girl stands by the window"')
        self.assertEqual(parsed["caption_long_en"], "A girl stands by the window")

    def test_truncated_inside_string_value(self):
        parsed = self.assertRepaired('{"caption_long_en": "A girl stands by the')
        self.assertEqual(parsed["caption_long_en"], "A girl stands by the")

    def test_trailing_comma_before_brace(self):
        self.assertEqual(self.assertRepaired('{"a": 1, "b": "x",}'), {"a": 1, "b": "x"})

    def test_truncated_array_with_trailing_comma(self):
        self.assertEqual(self.assertRepaired('{"a": [1, 2,'), {"a": [1, 2]})

    def test_single_quoted_keys_and_values(self):
        parsed = self.assertRepaired("{'caption_long_en': 'A girl stands', 'notes_cn': 'ok'}")
        self.assertEqual(parsed["caption_long_en"], "A girl stands")

    def test_nested_object_truncated(self):
        parsed = self.assertRepaired('{"outer": {"inner": [1, 2')
        self.assertEqual(parsed, {"outer": {"inner": [1, 2]}})

    def test_narrative_before_json(self):
        parsed = self.assertRepaired('Sure, here is the JSON:\n{"a": 1, "b": "two"')
        self.assertEqual(parsed, {"a": 1, "b": "two"})

    def test_narrative_after_json(self):
        parsed = self.assertRepaired('{"a": 1}\nHope this helps!')
        self.assertEqual(parsed, {"a": 1})

    def test_cjk_quotes_are_normalized(self):
        parsed = self.assertRepaired('{\u201ccaption_long_en\u201d: \u201cA girl stands\u201d}')
        self.assertEqual(parsed["caption_long_en"], "A girl stands")

    def test_escaped_quotes_survive_repair(self):
        parsed = self.assertRepaired('{"a": "he said \\"hi\\"", "b": "x"')
        self.assertEqual(parsed, {"a": 'he said "hi"', "b": "x"})

    def test_plain_text_is_not_repaired(self):
        parsed, ok = gateway._repair_jsonish_output("just a plain caption, no json here")
        self.assertFalse(ok)
        self.assertIsNone(parsed)

    def test_never_raises_on_garbage(self):
        for text in ("", "   ", "{", "{{{{", "[]", "None"):
            gateway._repair_jsonish_output(text)


class BannedTagFilterTests(unittest.TestCase):
    def test_removes_exact_banned_tags_case_insensitively(self):
        tags = ["1girl", "MasterPiece", "best quality", "blue eyes", "HIGHRES"]
        self.assertEqual(gateway.filter_user_banned_terms(tags), ["1girl", "blue eyes"])

    def test_removes_banned_substrings(self):
        tags = ["1girl", "character from Azur Lane", "arknights operator", "long hair"]
        self.assertEqual(gateway.filter_user_banned_terms(tags), ["1girl", "long hair"])

    def test_removes_artifact_tags(self):
        tags = ["artist name", "signature", "watermark", "twitter username", "1girl"]
        self.assertEqual(gateway.filter_user_banned_terms(tags), ["1girl"])

    def test_keeps_legitimate_tags_in_order(self):
        tags = ["1girl", "long hair", "blue eyes", "school uniform"]
        self.assertEqual(gateway.filter_user_banned_terms(tags), tags)
        self.assertEqual(gateway.filter_user_banned_terms([]), [])

    def test_wd14_baseline_first_line_is_never_filtered(self):
        wd14_line = "masterpiece, best quality, 1girl, artist name, signature"
        json_result = {
            "wd14_raw_tags_text": wd14_line,
            "expanded_tags_en": ["masterpiece", "1girl", "artist name"],
            "caption_long_en": LONG_CAPTION,
        }
        formatted = gateway.profile_format_prompt("anima_train_v1", json_result)
        first_line = formatted["formatted_training_text"].split("\n\n")[0]
        self.assertEqual(first_line, wd14_line)

    def test_llm_tag_fields_are_filtered_without_wd14_text(self):
        json_result = {
            "expanded_tags_en": ["masterpiece", "1girl", "artist name"],
            "caption_long_en": LONG_CAPTION,
        }
        formatted = gateway.profile_format_prompt("anima_train_v1", json_result)
        first_line = formatted["formatted_training_text"].split("\n\n")[0]
        self.assertEqual(first_line, "1girl")


class CaptionBannedTermTests(unittest.TestCase):
    def test_removes_banned_words_case_insensitively(self):
        cleaned = gateway.sanitize_caption_banned_terms("A portrait, MASTERPIECE, of a girl.")
        self.assertNotIn("masterpiece", cleaned.lower())
        self.assertEqual(cleaned, "A portrait, of a girl.")

    def test_respects_word_boundaries(self):
        cleaned = gateway.sanitize_caption_banned_terms("detailed shading and fine detail")
        self.assertNotIn("detailed", cleaned.lower())
        self.assertIn("fine detail", cleaned)

    def test_cleans_punctuation_after_removal(self):
        cleaned = gateway.sanitize_caption_banned_terms("1girl, signature, blue hair.")
        self.assertEqual(cleaned, "1girl, blue hair.")

    def test_removes_ip_substring_in_prose(self):
        cleaned = gateway.sanitize_caption_banned_terms("A character from Azur Lane smiles warmly.")
        self.assertNotIn("azur lane", cleaned.lower())
        self.assertNotIn("  ", cleaned)

    def test_removes_artifact_words_in_prose(self):
        cleaned = gateway.sanitize_caption_banned_terms("watermark in the corner and artist name label")
        self.assertEqual(cleaned, "in the corner and label")

    def test_leaves_clean_caption_alone(self):
        caption = "A girl stands by the window in soft afternoon light."
        self.assertEqual(gateway.sanitize_caption_banned_terms(caption), caption)
        self.assertEqual(gateway.sanitize_caption_banned_terms(""), "")


class TriggerPrefixTests(unittest.TestCase):
    def test_empty_prefix_keeps_two_line_text(self):
        text = gateway.build_training_two_line_text(["1girl"], "A girl stands.", "")
        self.assertEqual(text, "1girl\n\nA girl stands.")

    def test_non_empty_prefix_is_prepended_to_caption_line(self):
        text = gateway.build_training_two_line_text(["1girl"], "A girl stands.", "mytag")
        self.assertEqual(text, "1girl\n\nmytag, A girl stands.")

    def test_prefix_is_not_added_without_caption(self):
        text = gateway.build_training_two_line_text(["1girl"], "", "mytag")
        self.assertEqual(text, "1girl")

    def test_inputs_trigger_prefix_beats_config_file(self):
        self.assertEqual(gateway.resolve_training_trigger_prefix({"trigger_prefix": "mytag"}), "mytag")
        config_default = str(gateway.TAGGING_TERMS.get("trigger_prefix", "") or "")
        self.assertEqual(gateway.resolve_training_trigger_prefix({}), config_default.strip())

    def test_training_profile_prepends_prefix_to_caption_line(self):
        json_result = {
            "wd14_raw_tags_text": "1girl, long hair",
            "expanded_tags_en": ["1girl", "long hair"],
            "caption_long_en": LONG_CAPTION,
        }
        formatted = gateway.profile_format_prompt("anima_train_v1", json_result, "mytag")
        second_line = formatted["formatted_training_text"].split("\n\n")[1]
        self.assertTrue(second_line.startswith("mytag, "), second_line[:60])


class NaturalCaptionModeTests(unittest.TestCase):
    def test_plain_prompt_asks_for_prose_only(self):
        system_prompt, user_prompt = gateway.build_natural_caption_prompts(
            {"caption_output_format": "plain", "raw_tags": "1girl, long hair"}
        )
        self.assertIn("Plain prose only", user_prompt)
        self.assertIn("no JSON", user_prompt)
        self.assertNotIn("字段至少包含", user_prompt)
        self.assertIn("plain English prose", system_prompt)

    def test_json_prompt_is_kept_by_default(self):
        system_prompt, user_prompt = gateway.build_natural_caption_prompts({"raw_tags": "1girl"})
        self.assertIn("字段至少包含", user_prompt)
        self.assertNotIn("Plain prose only", user_prompt)

    def test_plain_bundle_exists_and_requests_plain_format(self):
        path = BUNDLE_DIR / "chain_wd14_to_anima_train_caption_plain.json"
        with path.open(encoding="utf-8") as handle:
            bundle = json.load(handle)
        module = bundle["task_modules"][0]
        self.assertEqual(module["type"], "generate_natural_caption")
        self.assertEqual(module["fixed_inputs"]["caption_output_format"], "plain")


class TaggingTermsConfigTests(unittest.TestCase):
    def test_loaded_terms_match_the_shipped_config(self):
        with TAGGING_TERMS_PATH.open(encoding="utf-8") as handle:
            expected = json.load(handle)
        for key in ("banned_exact_tags", "banned_substrings", "banned_artifact_tags"):
            self.assertEqual(gateway.TAGGING_TERMS[key], expected[key], key)

    def test_banned_term_summary_is_bounded(self):
        summary = gateway.build_banned_terms_constraint_text()
        self.assertLessEqual(len(summary.split(", ")), 30)
        self.assertIn("signature", summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
