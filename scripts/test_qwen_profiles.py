"""Consistency checks for the Qwen backend profiles and the vision task assets.

These tests never load a model and never touch the network: they only assert that
the checked-in example configuration, task templates and task bundles agree with
each other and with the rules the gateway relies on.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
PROFILES_PATH = PACKAGE_DIR / "config" / "backend_profiles.example.json"
DOWNLOADS_PATH = PACKAGE_DIR / "config" / "model_downloads.json"
TEMPLATE_DIR = PACKAGE_DIR / "resources" / "task_templates"
BUNDLE_DIR = PACKAGE_DIR / "resources" / "task_bundles"

QWEN_PROFILES = ("qwen38_27b_abliterated_vision_q4", "qwen36_35b_a3b_uncensored_vision_q4")

VISION_TASK_TYPES = {"extract_tags_from_image", "vision_tagging", "image_captioning"}
TEXT_TASK_TYPES = {
    "expand_anime_tags",
    "normalize_anime_tags",
    "translate_anime_tags",
    "generate_natural_caption",
    "refine_wd14_tags",
}
KNOWN_TASK_TYPES = VISION_TASK_TYPES | TEXT_TASK_TYPES

VISION_TEMPLATES = ("vision_tagging_qwen.json", "image_caption_qwen.json")
VISION_BUNDLE = "chain_qwen_vision_to_caption.json"


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


# A format module is either the training-caption format or one of the declared prompt profiles.
KNOWN_FORMAT_MODULES = {"anima_train_v1"} | set(load_json(PACKAGE_DIR / "config" / "prompt_profiles.json").keys())


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_json(PROFILES_PATH)

    def test_qwen_profiles_exist(self):
        for key in QWEN_PROFILES:
            self.assertIn(key, self.profiles, f"缺少 Qwen 档案 {key}")

    def test_shipped_profiles_use_relative_gguf_paths(self):
        # The example file ships with the package, so it must never contain machine paths.
        for key, profile in self.profiles.items():
            for field in ("model_path", "mmproj_path"):
                value = str(profile.get(field) or "")
                if not value:
                    continue
                self.assertFalse(value.startswith(("/", "\\")), f"{key}.{field} 必须是相对路径")
                self.assertNotIn(":", value, f"{key}.{field} 不允许带盘符（本机路径放 backend_profiles.json）")
                self.assertNotIn("..", value, f"{key}.{field} 不允许跳出节点目录")
                self.assertTrue(value.endswith(".gguf"), f"{key}.{field} 必须是 .gguf")

    def test_qwen_profiles_are_vision_capable(self):
        for key in QWEN_PROFILES:
            profile = self.profiles[key]
            # backend_choice_supports_vision() relies on this substring.
            self.assertIn("vision", key, f"{key} 的键名必须含 vision")
            self.assertTrue(profile.get("supports_vision"), f"{key} 必须声明 supports_vision")
            self.assertTrue(str(profile.get("mmproj_path") or "").endswith(".gguf"), f"{key} 缺少 mmproj_path")

    def test_non_vision_profiles_do_not_carry_mmproj(self):
        for key, profile in self.profiles.items():
            if profile.get("supports_vision"):
                continue
            self.assertFalse(profile.get("mmproj_path"), f"{key} 不是视觉档案，不应带 mmproj_path")
            self.assertNotIn("vision", key, f"{key} 的键名不应含 vision")


class DownloadEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_json(PROFILES_PATH)
        cls.downloads = load_json(DOWNLOADS_PATH)

    def test_every_download_entry_points_at_a_real_profile(self):
        for name, entry in self.downloads.items():
            self.assertIn(entry.get("profile_key"), self.profiles, f"{name} 的 profile_key 不存在于档案里")

    def test_qwen_targets_match_profile_paths(self):
        for key in QWEN_PROFILES:
            entry = self.downloads[key]
            profile = self.profiles[key]
            expected = {str(profile.get("model_path") or "")}
            if profile.get("mmproj_path"):
                expected.add(str(profile["mmproj_path"]))
            expected.discard("")
            targets = {str(item.get("target") or "") for item in entry["files"]}
            self.assertEqual(
                targets,
                expected,
                f"{key} 的下载 target 与档案里的 model_path / mmproj_path 不一致",
            )

    def test_optional_models_are_not_marked_required(self):
        for key in QWEN_PROFILES:
            for item in self.downloads[key]["files"]:
                self.assertFalse(item.get("required"), f"{key} 的可选模型不应标记 required")

    def test_repo_ids_are_present(self):
        for key in QWEN_PROFILES:
            repo_id = str(self.downloads[key].get("repo_id") or "")
            self.assertRegex(repo_id, r"^[\w.\-]+/[\w.\-]+$", f"{key} 的 repo_id 形状不对：{repo_id}")


class TaskAssetTests(unittest.TestCase):
    def test_vision_templates_are_well_formed(self):
        for name in VISION_TEMPLATES:
            template = load_json(TEMPLATE_DIR / name)
            self.assertIn(template["task_type"], VISION_TASK_TYPES, f"{name} 的 task_type 不是视觉任务")
            self.assertEqual(template["input_field"], "image_path", f"{name} 的输入字段应为 image_path")
            self.assertTrue(template.get("purpose"), f"{name} 缺少 purpose")

    def test_existing_templates_keep_known_task_types(self):
        for path in TEMPLATE_DIR.glob("*.json"):
            template = load_json(path)
            self.assertIn(template.get("task_type"), KNOWN_TASK_TYPES, f"{path.name} 的 task_type 未知")

    def test_vision_bundle_is_well_formed(self):
        bundle = load_json(BUNDLE_DIR / VISION_BUNDLE)
        self.assertTrue(bundle["task_modules"], "任务链不能为空")
        for module in bundle["task_modules"]:
            self.assertIn(module["type"], KNOWN_TASK_TYPES, f"未知模块类型 {module['type']}")
        self.assertIn(bundle["format_module"]["type"], KNOWN_FORMAT_MODULES)
        self.assertIn("vision_tagging", [module["type"] for module in bundle["task_modules"]])

    def test_all_bundles_keep_known_modules(self):
        for path in BUNDLE_DIR.glob("*.json"):
            bundle = load_json(path)
            for module in bundle.get("task_modules", []):
                self.assertIn(module["type"], KNOWN_TASK_TYPES, f"{path.name} 含未知模块 {module['type']}")
            format_module = bundle.get("format_module")
            if format_module:
                self.assertIn(format_module["type"], KNOWN_FORMAT_MODULES, f"{path.name} 的格式模块未知")


class LowVramGuidanceTests(unittest.TestCase):
    def test_a_small_8gb_friendly_default_still_ships(self):
        profiles = load_json(PROFILES_PATH)
        # Qwen profiles target the large uncensored models; the low-VRAM default stays Gemma.
        self.assertFalse(profiles["gemma4_e2b_hauhau_q8"].get("supports_vision"))
        self.assertEqual(profiles["gemma4_e2b_hauhau_q8"].get("default_context_size"), 1024)
        self.assertTrue(profiles["gemma4_e2b_hauhau_q8_vision"].get("supports_vision"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
