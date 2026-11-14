"""Tests for the backend-agnostic translation core and the in-process runtime.

Run with any Python that has the repo on disk:

    python -B scripts/test_translation_core.py

The runtime test is skipped unless a standard-quant HY-MT GGUF is configured.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))


def load_core():
    path = PACKAGE_DIR / "prompt_studio" / "translation_core.py"
    spec = importlib.util.spec_from_file_location("studio_suite_translation_core", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_core()


class FakeBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def __call__(self, prompt, max_tokens, temperature):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected extra completion request")
        return self.responses.pop(0), {"prompt_n": 10, "predicted_n": 5}


class TagSplittingTests(unittest.TestCase):
    def test_weighted_tags_stay_together(self):
        parts = CORE.split_tag_parts("(masterpiece:1.2), 单人, (长发:1.3)")
        self.assertEqual(parts, ["(masterpiece:1.2)", "单人", "(长发:1.3)"])

    def test_escaped_comma_does_not_split(self):
        parts = CORE.split_tag_parts("long hair, artist\\, name, smile")
        self.assertEqual(parts, ["long hair", "artist\\, name", "smile"])

    def test_chinese_and_newline_separators(self):
        self.assertEqual(CORE.split_tag_parts("长发，微笑；站姿"), ["长发", "微笑", "站姿"])
        self.assertEqual(CORE.split_tag_parts("a\nb\r\nc"), ["a", "b", "c"])

    def test_normalize_uses_english_separators(self):
        self.assertEqual(CORE.normalize_tag_separators("a，b; (c, d)"), "a, b, (c, d)")

    def test_text_units_split_on_sentence_endings(self):
        units = CORE.split_translation_units("中文自然语言.中文自然语言", "zh_to_en_text")
        self.assertEqual(units, [("中文自然语言", "."), ("中文自然语言", "")])

    def test_text_units_keep_commas_inside_sentences(self):
        units = CORE.split_translation_units("一个女孩，站在雨中。回头看向镜头。", "zh_to_en_text")
        self.assertEqual(
            units,
            [("一个女孩，站在雨中", "。"), ("回头看向镜头", "。")],
        )

    def test_text_units_do_not_split_decimals_or_weights(self):
        units = CORE.split_translation_units("(微笑:1.2) 站姿 1.5 倍", "zh_to_en_text")
        self.assertEqual(len(units), 1)
        self.assertIn("1.2", units[0][0])

    def test_text_units_fall_back_to_tags_for_tag_lines(self):
        units = CORE.split_translation_units("1girl, 长发", "zh_to_en_text")
        self.assertEqual(units, [("1girl", ", "), ("长发", "")])

    def test_fullwidth_parentheses_are_atomic(self):
        parts = CORE.split_tag_parts("（微笑，长发）, 1girl")
        self.assertEqual(parts, ["（微笑，长发）", "1girl"])
        units = CORE.split_translation_units("（微笑，长发）, 1girl", "zh_to_en_tags")
        self.assertEqual(units[0][0], "（微笑，长发）")

    def test_lora_tag_with_comma_stays_atomic(self):
        parts = CORE.split_tag_parts("1girl, <lora:A Qu, the Shadow:1>, 长发")
        self.assertEqual(parts, ["1girl", "<lora:A Qu, the Shadow:1>", "长发"])

    def test_lora_tag_is_one_translation_unit(self):
        units = CORE.split_translation_units("<lora:A Qu, the Shadow:1>, 长发", "zh_to_en_tags")
        self.assertEqual(units, [("<lora:A Qu, the Shadow:1>", ", "), ("长发", "")])

    def test_normalize_collapses_weight_spaces(self):
        self.assertEqual(CORE.normalize_tag_separators("(Smile: 1.1), long hair"), "(Smile:1.1), long hair")

    def test_translation_needed(self):
        self.assertTrue(CORE.translation_needed("长发", "zh_to_en_tags"))
        self.assertFalse(CORE.translation_needed("long hair", "zh_to_en_tags"))
        self.assertTrue(CORE.translation_needed("long hair", "en_to_zh_tags"))
        self.assertFalse(CORE.translation_needed("长发", "en_to_zh_tags"))
        self.assertFalse(CORE.translation_needed("   ", "zh_to_en_text"))


class TagTranslatorTests(unittest.TestCase):
    def test_skips_text_already_in_target_language(self):
        backend = FakeBackend([])
        translator = CORE.TagTranslator(backend)
        result = translator.translate("long hair, smile", "zh_to_en_tags")
        self.assertTrue(result["skipped_already_target_language"])
        self.assertEqual(result["translated_text"], "long hair, smile")
        self.assertEqual(backend.prompts, [])

    def test_only_untranslated_tags_are_sent(self):
        backend = FakeBackend(["long hair"])
        translator = CORE.TagTranslator(backend)
        result = translator.translate("长发, smile", "zh_to_en_tags")
        self.assertEqual(len(backend.prompts), 1)
        self.assertIn("长发", backend.prompts[0])
        self.assertNotIn("smile", backend.prompts[0])
        self.assertEqual(result["translated_text"], "long hair, smile")
        self.assertEqual(result["backend_timings"]["tag_items"], 2)
        self.assertEqual(result["backend_timings"]["translated_tag_items"], 1)

    def test_per_item_retry_when_tag_count_mismatches(self):
        backend = FakeBackend(["long hair", "long hair", "smile"])
        translator = CORE.TagTranslator(backend)
        result = translator.translate("长发, 微笑", "zh_to_en_tags")
        self.assertEqual(result["translated_text"], "long hair, smile")
        self.assertEqual(result["backend_timings"]["fallback_requests"], 2)
        self.assertEqual(len(backend.prompts), 3)

    def test_cache_returns_without_calling_backend(self):
        backend = FakeBackend(["long hair"])
        translator = CORE.TagTranslator(backend)
        first = translator.translate("长发", "zh_to_en_tags")
        second = translator.translate("长发", "zh_to_en_tags")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["translated_text"], second["translated_text"])
        self.assertEqual(len(backend.prompts), 1)
        self.assertEqual(len(translator.cache), 1)

    def test_external_empty_cache_is_reused(self):
        backend = FakeBackend(["long hair"])
        cache = CORE.TranslationCache()
        translator = CORE.TagTranslator(backend, cache)
        translator.translate("长发", "zh_to_en_tags")
        self.assertEqual(len(cache), 1)
        self.assertIs(translator.cache, cache)

    def test_cache_namespace_separates_models(self):
        backend = FakeBackend(["long hair", "long hair"])
        translator = CORE.TagTranslator(backend)
        translator.translate("长发", "zh_to_en_tags", cache_namespace="model-a")
        result = translator.translate("长发", "zh_to_en_tags", cache_namespace="model-b")
        self.assertFalse(result["cache_hit"])
        self.assertEqual(len(backend.prompts), 2)

    def test_prompt_mentions_target_language_and_tag_rules(self):
        backend = FakeBackend(["long hair"])
        translator = CORE.TagTranslator(backend)
        translator.translate("长发", "zh_to_en_tags")
        prompt = backend.prompts[0]
        self.assertIn("英语", prompt)
        self.assertIn("标签", prompt)
        self.assertIn("只输出翻译结果", prompt)
        self.assertNotIn("逐项翻译", prompt)


class TextTranslatorTests(unittest.TestCase):
    def test_sentences_are_translated_separately_and_punctuation_kept(self):
        backend = FakeBackend(["A girl.", "She looks back."])
        translator = CORE.TagTranslator(backend)
        result = translator.translate("一个女孩.她回头看.", "zh_to_en_text")
        self.assertEqual(result["translated_text"], "A girl. She looks back.")
        self.assertEqual(result["backend_timings"]["translated_text_units"], 2)
        self.assertEqual(len(backend.prompts), 2)
        self.assertNotIn("一个女孩.她回头看", backend.prompts[0])

    def test_english_units_are_kept_verbatim_in_text_mode(self):
        backend = FakeBackend(["长发"])
        translator = CORE.TagTranslator(backend)
        result = translator.translate("1girl, 长发", "zh_to_en_text")
        self.assertEqual(result["translated_text"], "1girl, 长发")
        self.assertEqual(len(backend.prompts), 1)
        self.assertIn("长发", backend.prompts[0])

    def test_translated_unit_drops_added_period_when_separator_has_one(self):
        backend = FakeBackend(["A girl."])
        translator = CORE.TagTranslator(backend)
        result = translator.translate("一个女孩.", "zh_to_en_text")
        self.assertEqual(result["translated_text"], "A girl.")

    def test_chinese_sentence_mark_converted_for_english_output(self):
        backend = FakeBackend(["A girl"])
        translator = CORE.TagTranslator(backend)
        result = translator.translate("一个女孩。", "zh_to_en_text")
        self.assertEqual(result["translated_text"], "A girl.")

    def test_text_units_are_cached_individually(self):
        backend = FakeBackend(["A girl.", "Next sentence."])
        translator = CORE.TagTranslator(backend)
        first = translator.translate("一个女孩.下一句", "zh_to_en_text")
        second = translator.translate("一个女孩.下一句", "zh_to_en_text")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(len(backend.prompts), 2)
        self.assertEqual(len(translator.cache), 2)


class InProcessConfigTests(unittest.TestCase):
    def test_runtime_reports_missing_or_available_without_loading(self):
        import types

        package_name = "studio_suite_test_pkg"
        package = types.ModuleType(package_name)
        package.__path__ = [str(PACKAGE_DIR / "prompt_studio")]
        sys.modules[package_name] = package
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.inprocess_translation_runtime",
            PACKAGE_DIR / "prompt_studio" / "inprocess_translation_runtime.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        InProcessTranslationRuntime = module.InProcessTranslationRuntime

        runtime = InProcessTranslationRuntime(PACKAGE_DIR)
        state = runtime.status()
        self.assertIn("available", state)
        self.assertIn("model_path", state)
        self.assertFalse(state["alive"])
        if os.environ.get("STUDIO_SUITE_TEST_LOAD_Q4") == "1" and state["available"]:
            runtime.ensure_started()
            try:
                result = runtime.translate("长发, 白色连衣裙", "zh_to_en_tags")
                self.assertTrue(result["translated_text"])
            finally:
                runtime.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
