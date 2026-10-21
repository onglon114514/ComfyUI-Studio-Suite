"""Tests for `region_layout_node.py` — pure adapters, compose, and JS parity.

Run with the ComfyUI Python interpreter, e.g.:

    COMFYUI_ROOT=<ComfyUI> PYTHONPATH=<ComfyUI> python -B scripts/test_region_layout_node.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
COMFY_ROOT = str(os.environ.get("COMFYUI_ROOT", "") or "").strip()
for candidate in (str(PACKAGE_DIR), COMFY_ROOT):
    if candidate and candidate not in sys.path:
        sys.path.insert(0, candidate)

import region_layout_node as region_layout


def fixture():
    return {
        "schema": "prompt-studio/region-v1",
        "canvas": {"w": 1024, "h": 1024},
        "global": {
            "caption_zh": "戴红帽的女孩",
            "caption_en": "a girl wearing a red hat",
        },
        "regions": [
            {
                "id": "r1",
                "bbox": [0.25, 0.25, 0.75, 0.5],
                "role": "obj",
                "literalText": "",
                "nl": {"zh": "", "en": "a red hat"},
                "tags": ["Red Hat"],
                "muted": False,
                "linkedTo": None,
                "colors": [],
            },
            {
                "id": "r2",
                "bbox": [0.1, 0.1, 0.9, 0.2],
                "role": "text",
                "literalText": "HAPPY DAY",
                "nl": {"zh": "", "en": "big title"},
                "tags": [],
                "muted": False,
                "linkedTo": None,
                "colors": [],
            },
            {
                "id": "r3",
                "bbox": [0.0, 0.0, 0.2, 0.2],
                "role": "obj",
                "literalText": "",
                "nl": {"zh": "", "en": "muted box"},
                "tags": ["ignored"],
                "muted": True,
                "linkedTo": None,
                "colors": [],
            },
            {
                "id": "r4",
                "bbox": [0.5, 0.5, 0.5, 0.7],
                "role": "obj",
                "literalText": "",
                "nl": {"zh": "", "en": "zero area"},
                "tags": [],
                "muted": False,
                "linkedTo": None,
                "colors": [],
            },
        ],
    }


JS_SOURCE = r"""
const fs = require("node:fs");
const path = require("node:path");
const html = fs.readFileSync(path.join(process.cwd(), "frontend", "index.html"), "utf8");
const match = html.match(/\/\/ == REGION_ADAPTERS_BEGIN ==([\s\S]*?)\/\/ == REGION_ADAPTERS_END ==/);
if (!match) {
  console.error("未能从 frontend/index.html 提取 RegionAdapters 块");
  process.exit(2);
}
const RegionAdapters = new Function("window", match[0] + "\nreturn window.RegionAdapters;")({});
const doc = __DOC__;
process.stdout.write(JSON.stringify({
  moss: JSON.stringify(RegionAdapters.toMoss(doc)),
  ideogram: JSON.stringify(RegionAdapters.toIdeogram(doc)),
  tag_lines: RegionAdapters.toTagLines(doc),
}));
"""


def run_panel_adapters(doc):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node 不在 PATH，无法执行 JS 对拍")
    script = JS_SOURCE.replace("__DOC__", json.dumps(doc, ensure_ascii=False))
    completed = subprocess.run(
        [node, "-e", script],
        cwd=str(PACKAGE_DIR),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "node 对拍失败："
            + completed.stderr.decode("utf-8", errors="replace")
        )
    return json.loads(completed.stdout.decode("utf-8"))


def compose(document, width=1024, height=1024):
    return region_layout.StudioSuiteRegionLayout().compose(
        json.dumps(document, ensure_ascii=False),
        width,
        height,
    )


class AdapterTests(unittest.TestCase):
    def test_moss_bbox_yxyx_central(self):
        moss = region_layout.to_moss_doc(fixture())
        self.assertEqual(
            [item["bbox"] for item in moss["items"]],
            [
                [-256, -256, 0, 256],
                [-410, -410, -307, 410],
            ],
        )
        self.assertEqual([item["label"] for item in moss["items"]], ["object", "text"])
        self.assertEqual(moss["items"][0]["caption"], "a red hat")
        self.assertEqual(moss["items"][1]["caption"], "big title")
        self.assertNotIn("text", moss["items"][0])
        self.assertEqual(moss["items"][1]["text"], "HAPPY DAY")
        self.assertEqual(moss["bbox_coord_space"], "abs_centralized")
        self.assertEqual(moss["bbox_format"], "yxyx")

    def test_moss_canvas_summary_and_skips(self):
        moss = region_layout.to_moss_doc(fixture())
        self.assertEqual(
            moss["summary"],
            {
                "meta": [],
                "canvas": "single",
                "caption": "a girl wearing a red hat",
            },
        )
        self.assertEqual(len(moss["items"]), 2)
        self.assertEqual(
            [item["caption"] for item in moss["items"]],
            ["a red hat", "big title"],
        )

    def test_ideogram_bbox_0_1000_and_types(self):
        ideogram = region_layout.to_ideogram_doc(fixture())
        self.assertEqual(ideogram["high_level_description"], "a girl wearing a red hat")
        self.assertEqual(
            ideogram["style_description"],
            {
                "aesthetics": "",
                "lighting": "",
                "photo": "",
                "medium": "",
                "color_palette": [],
            },
        )
        self.assertEqual(ideogram["compositional_deconstruction"]["background"], "")
        elements = ideogram["compositional_deconstruction"]["elements"]
        self.assertEqual(
            elements,
            [
                {"type": "obj", "bbox": [250, 250, 500, 750], "desc": "a red hat"},
                {
                    "type": "text",
                    "bbox": [100, 100, 200, 900],
                    "text": "HAPPY DAY",
                    "desc": "big title",
                },
            ],
        )

    def test_tag_lines_text_prefix_and_skips(self):
        lines = region_layout.to_tag_lines_doc(fixture()).split("\n")
        self.assertEqual(
            lines,
            [
                "Red Hat, a red hat",
                'text: "HAPPY DAY" | big title',
            ],
        )

    def test_tag_lines_skip_region_without_tags_or_nl(self):
        doc = fixture()
        doc["regions"].append(
            {
                "id": "r5",
                "bbox": [0.3, 0.3, 0.4, 0.4],
                "role": "obj",
                "literalText": "",
                "nl": {"zh": "", "en": ""},
                "tags": [],
                "muted": False,
                "linkedTo": None,
                "colors": [],
            }
        )
        lines = region_layout.to_tag_lines_doc(doc).split("\n")
        self.assertEqual(len(lines), 2)
        self.assertNotIn("", lines)

    def test_normalize_tag(self):
        self.assertEqual(region_layout.normalize_tag("Red Hat"), "red_hat")
        self.assertEqual(region_layout.normalize_tag("  red,hat  "), "red_hat")
        self.assertEqual(region_layout.normalize_tag("__Red__"), "red")


class TagFilterTests(unittest.TestCase):
    def test_filter_banned_exact_and_artifact_tags(self):
        self.assertEqual(
            region_layout._filter_banned_tags(
                ["masterpiece", "Signature", "best quality", "red hat", "watermark"]
            ),
            ["red hat"],
        )

    def test_filter_banned_substrings_case_insensitive(self):
        self.assertEqual(
            region_layout._filter_banned_tags(
                ["Azur Lane", "azur_lane", "ARKNIGHTS fanart", "blue archive", "Touhou Project"]
            ),
            ["blue archive"],
        )

    def test_filter_normalizes_before_exact_match(self):
        self.assertEqual(
            region_layout._filter_banned_tags(
                ["MasterPiece", "Best,Quality", "Artist   Name", "red hat"]
            ),
            ["red hat"],
        )

    def test_sanitize_caption_removes_terms_and_tidies(self):
        self.assertEqual(
            region_layout._sanitize_caption(
                "a beautiful Azur Lane girl, masterpiece, with Signature."
            ),
            "a beautiful girl, with.",
        )
        self.assertEqual(region_layout._sanitize_caption(""), "")

    def test_estimate_tokens_ascii_cjk_mixed(self):
        self.assertEqual(region_layout.estimate_tokens(""), 0)
        self.assertEqual(region_layout.estimate_tokens("abc"), 1)
        self.assertEqual(region_layout.estimate_tokens("abcd"), 1)
        self.assertEqual(region_layout.estimate_tokens("你好"), 2)
        self.assertEqual(region_layout.estimate_tokens("abc你好"), 3)

    def test_compose_filters_region_tags_and_captions_but_not_doc_json(self):
        doc = fixture()
        region = doc["regions"][0]
        region["tags"] = ["Masterpiece", "Azur Lane", "red hat"]
        region["nl"]["en"] = "masterpiece girl from Azur Lane, signature"

        doc_json, moss_json, ideogram_json, tag_lines = compose(doc)
        stored = json.loads(doc_json)["regions"][0]
        self.assertEqual(stored["tags"], ["masterpiece", "azur_lane", "red_hat"])
        self.assertEqual(stored["nl"]["en"], "masterpiece girl from Azur Lane, signature")

        self.assertEqual(json.loads(moss_json)["items"][0]["caption"], "girl from")
        elements = json.loads(ideogram_json)["compositional_deconstruction"]["elements"]
        self.assertEqual(elements[0]["desc"], "girl from")
        self.assertEqual(tag_lines.split("\n")[0], "red_hat, girl from")
        for banned in ("masterpiece", "azur lane", "signature"):
            self.assertNotIn(banned, moss_json.lower())
            self.assertNotIn(banned, tag_lines.lower())

    def test_compose_uses_zh_caption_when_en_empty(self):
        doc = fixture()
        doc["regions"][0]["tags"] = []
        doc["regions"][0]["nl"]["en"] = ""
        doc["regions"][0]["nl"]["zh"] = "azur lane 少女, masterpiece"
        _doc_json, _moss, _ideogram, tag_lines = compose(doc)
        self.assertIn("少女", tag_lines)
        self.assertNotIn("azur lane", tag_lines.lower())
        self.assertNotIn("masterpiece", tag_lines.lower())

    def test_compose_keeps_muted_regions_out_of_outputs(self):
        doc = fixture()
        muted = doc["regions"][2]
        muted["tags"] = ["masterpiece"]
        muted["nl"]["en"] = "azur lane"
        doc_json, moss_json, _ideogram_json, tag_lines = compose(doc)
        stored = json.loads(doc_json)["regions"][2]
        self.assertTrue(stored["muted"])
        self.assertEqual(stored["tags"], ["masterpiece"])
        self.assertEqual(stored["nl"]["en"], "azur lane")
        self.assertEqual(len(json.loads(moss_json)["items"]), 2)
        self.assertNotIn("azur lane", tag_lines.lower())

    def test_compose_token_line_and_over_budget_warning(self):
        buffer = StringIO()
        with redirect_stdout(buffer):
            compose(fixture())
        output = buffer.getvalue()
        self.assertIn("tokens: total=8 regions=[r1=5, r2=3]", output)
        self.assertNotIn("[warn]", output)

        doc = fixture()
        doc["regions"] = [doc["regions"][0]]
        doc["regions"][0]["tags"] = []
        doc["regions"][0]["nl"]["en"] = "字" * 4500
        buffer = StringIO()
        with redirect_stdout(buffer):
            compose(doc)
        output = buffer.getvalue()
        self.assertIn("tokens: total=4500 regions=[r1=4500]", output)
        self.assertIn("[warn]", output)


class ComposeTests(unittest.TestCase):
    def test_empty_and_invalid_docs_return_empty_strings(self):
        node = region_layout.StudioSuiteRegionLayout()
        for payload in ("", "   ", "{not json", "[]", "null"):
            self.assertEqual(node.compose(payload, 1024, 1024), ("", "", "", ""))
        self.assertEqual(node.compose(None, 1024, 1024), ("", "", "", ""))

    def test_canvas_widget_overrides_doc_canvas(self):
        outputs = compose(fixture(), 768, 768)
        doc_json, moss_json, _ideogram, tag_lines = outputs
        self.assertEqual(json.loads(doc_json)["canvas"], {"w": 768, "h": 768})
        self.assertEqual(
            [item["bbox"] for item in json.loads(moss_json)["items"]],
            [
                [-192, -192, 0, 192],
                [-307, -307, -230, 307],
            ],
        )
        self.assertEqual(
            tag_lines,
            "red_hat, a red hat\ntext: \"HAPPY DAY\" | big title",
        )

    def test_image_argument_is_ignored(self):
        node = region_layout.StudioSuiteRegionLayout()
        payload = json.dumps(fixture(), ensure_ascii=False)
        self.assertEqual(
            node.compose(payload, 1024, 1024, image=object()),
            node.compose(payload, 1024, 1024),
        )

    def test_compose_normalizes_tags_in_doc_json_and_lines(self):
        doc_json, _moss, _ideogram, tag_lines = compose(fixture())
        doc = json.loads(doc_json)
        self.assertEqual(doc["regions"][0]["tags"], ["red_hat"])
        self.assertEqual(tag_lines.split("\n")[0], "red_hat, a red hat")

    def test_non_multiple_of_16_raises(self):
        with self.assertRaisesRegex(ValueError, r"宽度是 16 的倍数（当前 1000）"):
            compose(fixture(), 1000, 1024)
        with self.assertRaisesRegex(ValueError, r"高度是 16 的倍数（当前 1000）"):
            compose(fixture(), 1024, 1000)


class PanelParityTests(unittest.TestCase):
    def test_pure_adapters_match_panel_js_byte_for_byte(self):
        doc = fixture()
        js = run_panel_adapters(doc)
        self.assertEqual(js["moss"], region_layout.dump_json(region_layout.to_moss_doc(doc)))
        self.assertEqual(
            js["ideogram"],
            region_layout.dump_json(region_layout.to_ideogram_doc(doc)),
        )
        self.assertEqual(js["tag_lines"], region_layout.to_tag_lines_doc(doc))

    def test_compose_matches_panel_js_after_panel_tag_ingestion(self):
        raw_doc = fixture()
        ingested = region_layout._sanitize_doc(raw_doc, 1024, 1024)
        js = run_panel_adapters(ingested)
        _doc_json, moss_json, ideogram_json, tag_lines = compose(raw_doc)
        self.assertEqual(js["moss"], moss_json)
        self.assertEqual(js["ideogram"], ideogram_json)
        self.assertEqual(js["tag_lines"], tag_lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
