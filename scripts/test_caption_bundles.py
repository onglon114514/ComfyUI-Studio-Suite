"""Consistency tests for caption profiles and task bundles.

Checks that every bundle points at a real profile, that the new base-model
caption chains agree with `config/prompt_profiles.json`, and that the gateway
actually supports every caption-only profile.

    python -B scripts/test_caption_bundles.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
for candidate in (str(PACKAGE_DIR), str(os.environ.get("COMFYUI_ROOT", "") or "").strip()):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

import task_agent_gateway as gateway

BUNDLE_DIR = PACKAGE_DIR / "resources" / "task_bundles"
PROFILES_PATH = PACKAGE_DIR / "config" / "prompt_profiles.json"

CAPTION_ONLY_PROFILES = (
    "moss_train_nl_v1",
    "flux2_train_nl_v1",
    "qwen_image_train_nl_v1",
    "zimage_train_nl_v1",
    "hunyuan_image_train_nl_v1",
    "longcat_train_nl_v1",
)

# Training-only modules handled directly by the gateway, not listed in the
# inference-oriented prompt_profiles.json.
SPECIAL_FORMAT_MODULES = {"anima_train_v1"}

# bundle stem -> target profile
CAPTION_CHAINS = {
    "chain_wd14_to_moss_train_caption": "moss_train_nl_v1",
    "chain_wd14_to_flux2_train_caption": "flux2_train_nl_v1",
    "chain_wd14_to_qwen_image_train_caption": "qwen_image_train_nl_v1",
    "chain_wd14_to_zimage_train_caption": "zimage_train_nl_v1",
    "chain_wd14_to_longcat_train_caption": "longcat_train_nl_v1",
    "chain_image_to_moss_style_caption": "moss_train_nl_v1",
}

CAPTION = (
    "A solo woman in a charcoal coat stands at the edge of a rain-soaked pier. "
    "She holds a folded newspaper under one arm and looks toward a distant lighthouse. "
    "The camera is at eye level, framing her from the knees up. "
    "Wet planks reflect the pale morning sky, and a row of moored boats blurs behind her."
)


def load_profiles():
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))


def load_bundle(stem):
    return json.loads((BUNDLE_DIR / f"{stem}.json").read_text(encoding="utf-8"))


class BundleShapeTests(unittest.TestCase):
    def test_every_bundle_points_at_a_known_profile(self):
        profiles = load_profiles()
        known = {str(entry.get("type", "")).strip() for entry in profiles.values()}
        known |= SPECIAL_FORMAT_MODULES
        for path in sorted(BUNDLE_DIR.glob("*.json")):
            bundle = json.loads(path.read_text(encoding="utf-8"))
            target = str((bundle.get("format_module") or {}).get("type", "")).strip()
            self.assertTrue(target, f"{path.name} 没有 format_module.type")
            self.assertIn(target, known, f"{path.name} 指向未知 profile: {target}")
            modules = bundle.get("task_modules")
            self.assertIsInstance(modules, list, f"{path.name} task_modules 不是列表")
            self.assertTrue(modules, f"{path.name} task_modules 为空")
            for module in modules:
                self.assertIsInstance(module, dict, f"{path.name} task_modules 项不是对象")
                self.assertTrue(str(module.get("type", "")).strip(), f"{path.name} 有空 module")

    def test_caption_chains_are_caption_only(self):
        for stem, profile in CAPTION_CHAINS.items():
            bundle = load_bundle(stem)
            self.assertEqual(
                (bundle.get("format_module") or {}).get("type"), profile, stem
            )
            self.assertEqual(
                (bundle.get("fixed_inputs") or {}).get("caption_mode"),
                "training_caption_only",
                f"{stem} 不是 caption-only 模式",
            )
            module = bundle["task_modules"][0]
            self.assertEqual(module.get("type"), "generate_natural_caption", stem)
            self.assertEqual(
                (module.get("fixed_inputs") or {}).get("caption_output_format"),
                "plain",
                f"{stem} 没有要求 plain 输出",
            )

    def test_only_the_style_bundle_sets_style_mode(self):
        for stem in CAPTION_CHAINS:
            module_inputs = load_bundle(stem)["task_modules"][0].get("fixed_inputs") or {}
            expected = "style" if stem == "chain_image_to_moss_style_caption" else None
            self.assertEqual(module_inputs.get("caption_style_mode"), expected, stem)

    def test_vision_caption_bundle_is_image_first(self):
        bundle = load_bundle("chain_qwen_vision_to_moss_caption")
        self.assertEqual(bundle["task_modules"][0].get("type"), "image_captioning")
        self.assertEqual(
            (bundle.get("format_module") or {}).get("type"), "moss_train_nl_v1"
        )
        self.assertEqual(
            (bundle.get("fixed_inputs") or {}).get("caption_mode"),
            "training_caption_only",
        )


class ProfileTests(unittest.TestCase):
    def test_caption_only_profiles_have_notes(self):
        profiles = load_profiles()
        for name in CAPTION_ONLY_PROFILES:
            self.assertIn(name, {entry.get("type") for entry in profiles.values()}, name)
            notes = ""
            for entry in profiles.values():
                if entry.get("type") == name:
                    notes = str(entry.get("notes_cn") or "")
                    break
            self.assertTrue(notes.strip(), f"{name} 缺少 notes_cn")

    def test_gateway_supports_every_caption_only_profile(self):
        result = {
            "wd14_raw_tags_text": "1girl, long hair",
            "expanded_tags_en": ["1girl", "long hair"],
            "caption_long_en": CAPTION,
        }
        for name in CAPTION_ONLY_PROFILES:
            formatted = gateway.profile_format_prompt(name, result)
            self.assertEqual(formatted.get("target_profile"), name)
            self.assertEqual(formatted.get("formatted_training_text"), CAPTION, name)
            self.assertEqual(formatted.get("formatted_negative_prompt"), "", name)

    def test_trigger_prefix_is_prepended_for_caption_only_profiles(self):
        result = {"caption_long_en": CAPTION}
        formatted = gateway.profile_format_prompt("moss_train_nl_v1", result, "mytag")
        self.assertEqual(
            formatted.get("formatted_training_text"), "mytag, " + CAPTION
        )


if __name__ == "__main__":
    unittest.main()
