import json
import math
import os
import re

import folder_paths


def _detect_locale() -> str:
    candidates = []

    env_locale = os.environ.get("AGL_LOCALE") or os.environ.get("LANG") or os.environ.get("LC_ALL")
    if env_locale:
        candidates.append(env_locale)

    comfy_root = os.path.dirname(os.path.abspath(folder_paths.__file__))
    settings_path = os.path.join(comfy_root, "user", "default", "comfy.settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            setting_locale = data.get("AGL.Locale") or data.get("Comfy.Locale")
            if isinstance(setting_locale, str) and setting_locale.strip():
                candidates.append(setting_locale.strip())
        except Exception:
            pass

    for val in candidates:
        low = val.lower()
        if low.startswith("zh") or "zh-" in low:
            return "zh"
    return "en"


LOCALE = _detect_locale()

DISPLAY_NAME = "区域布局" if LOCALE == "zh" else "Region Layout"

CATEGORY_NAME = (
    "Studio Suite/图像预处理"
    if LOCALE == "zh"
    else "Studio Suite/Image Prep"
)

SCHEMA = "prompt-studio/region-v1"

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TAGGING_TERMS_PATH = os.path.join(PROJECT_DIR, "config", "tagging_terms.json")
TOKEN_BUDGET = 4000

TAGGING_TERMS_DEFAULT = {
    "banned_exact_tags": [
        "masterpiece",
        "best quality",
        "detailed",
        "ultra-detailed",
        "highres",
    ],
    "banned_substrings": [
        "azur lane",
        "arknights",
        "genshin impact",
        "honkai: star rail",
        "honkai impact 3rd",
        "fate/grand order",
        "kantai collection",
        "touhou project",
    ],
    "banned_artifact_tags": [
        "artist name",
        "signature",
        "character name",
        "copyright name",
        "character signature",
        "twitter username",
        "username",
        "watermark",
        "english text",
        "copyright notice",
        "cover page",
        "doujin cover",
        "magazine cover",
        "cover art",
    ],
    "trigger_prefix": "",
}

_TAGGING_TERMS_CACHE = None

_TAG_SEPARATOR = re.compile(r"[\s,]+")


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _js_number(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        num = float(value)
        return num if math.isfinite(num) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            num = float(text)
        except ValueError:
            return None
        return num if math.isfinite(num) else None
    return None


def _js_round(value: float) -> int:
    # JS Math.round, not Python's banker's rounding.
    return int(math.floor(value + 0.5))


def estimate_tokens(text) -> int:
    total = 0.0
    for char in str(text or ""):
        total += 1.0 if ord(char) > 127 else 0.3
    return _js_round(total)


def _js_round_number(value) -> int:
    num = _js_number(value)
    if num is None:
        return 0
    return _js_round(num)


def _as_doc(doc) -> dict:
    if not isinstance(doc, dict):
        raise ValueError("doc 无效")
    canvas = doc.get("canvas")
    if not isinstance(canvas, dict):
        canvas = {}
    raw_w = canvas["w"] if "w" in canvas else canvas.get("width")
    raw_h = canvas["h"] if "h" in canvas else canvas.get("height")
    global_info = doc.get("global")
    if not isinstance(global_info, dict):
        global_info = {}
    regions = doc.get("regions")
    image = doc.get("image")
    return {
        "w": _js_round_number(raw_w),
        "h": _js_round_number(raw_h),
        "global": global_info,
        "regions": regions if isinstance(regions, list) else [],
        "image": image if isinstance(image, str) else "",
    }


def _bbox_of(region):
    if not isinstance(region, dict):
        return None
    raw = region.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    nums = [_js_number(value) for value in raw]
    if any(value is None for value in nums):
        return None
    return [
        max(0.0, min(1.0, min(nums[0], nums[2]))),
        max(0.0, min(1.0, min(nums[1], nums[3]))),
        max(0.0, min(1.0, max(nums[0], nums[2]))),
        max(0.0, min(1.0, max(nums[1], nums[3]))),
    ]


def _has_area(bbox) -> bool:
    return bool(bbox) and bbox[2] - bbox[0] > 0 and bbox[3] - bbox[1] > 0


def _tag_list(region) -> list[str]:
    raw = region.get("tags") if isinstance(region, dict) else None
    if not isinstance(raw, list):
        raw = []
    tags = []
    for item in raw:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = item.get("tag") or ""
        else:
            value = ""
        text = str(value).strip()
        if text:
            tags.append(text)
    return tags


def normalize_tag(raw) -> str:
    if isinstance(raw, str):
        text = raw
    elif raw is None:
        text = ""
    else:
        text = str(raw)
    text = text.strip().lower()
    return _TAG_SEPARATOR.sub("_", text).strip("_")


def _load_tagging_terms() -> dict:
    global _TAGGING_TERMS_CACHE
    if _TAGGING_TERMS_CACHE is not None:
        return _TAGGING_TERMS_CACHE
    merged = {
        key: (list(value) if isinstance(value, list) else value)
        for key, value in TAGGING_TERMS_DEFAULT.items()
    }
    try:
        with open(TAGGING_TERMS_PATH, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            merged.update(loaded)
    except Exception:
        pass
    _TAGGING_TERMS_CACHE = merged
    return merged


def _tagging_terms_list(name: str) -> list:
    value = _load_tagging_terms().get(name)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe_terms(terms) -> list:
    seen = set()
    result = []
    for term in terms:
        text = str(term).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _filter_banned_tags(tags) -> list:
    exact = set()
    for name in ("banned_exact_tags", "banned_artifact_tags"):
        for item in _tagging_terms_list(name):
            exact.add(item.lower())
            exact.add(normalize_tag(item))
    substrings = []
    for item in _tagging_terms_list("banned_substrings"):
        substrings.append((item.lower(), normalize_tag(item)))

    result = []
    for tag in tags or []:
        text = str(tag).strip()
        if not text:
            continue
        lowered = text.lower()
        normalized = normalize_tag(text)
        if lowered in exact or normalized in exact:
            continue
        if any(fragment in lowered for fragment, _ in substrings):
            continue
        if any(fragment and fragment in normalized for _, fragment in substrings):
            continue
        result.append(tag)
    return result


def _banned_term_pattern(term: str):
    escaped = re.escape(str(term).strip())
    escaped = escaped.replace("\\ ", "[\\s_-]+")
    return re.compile(r"(?<![A-Za-z0-9])" + escaped + r"(?![A-Za-z0-9])", re.IGNORECASE)


def _sanitize_caption(text) -> str:
    cleaned = str(text or "")
    if not cleaned.strip():
        return cleaned
    terms = _dedupe_terms(
        _tagging_terms_list("banned_artifact_tags")
        + _tagging_terms_list("banned_substrings")
        + _tagging_terms_list("banned_exact_tags")
    )
    for term in sorted(terms, key=len, reverse=True):
        cleaned = _banned_term_pattern(term).sub(" ", cleaned)
    cleaned = re.sub(r"\(\s*\)", " ", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?;:])\s*(?=[,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    cleaned = re.sub(r"^[\s,]+", "", cleaned)
    cleaned = re.sub(r"[\s,]+$", "", cleaned)
    cleaned = re.sub(r",\s*([.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _filter_region_terms(region: dict) -> dict:
    filtered = dict(region)
    filtered["tags"] = _filter_banned_tags(region.get("tags"))
    nl = region.get("nl") if isinstance(region.get("nl"), dict) else {}
    filtered["nl"] = {
        "zh": _sanitize_caption(nl.get("zh") or ""),
        "en": _sanitize_caption(nl.get("en") or ""),
    }
    return filtered


def _filter_doc_terms(doc: dict) -> dict:
    filtered = dict(doc)
    filtered["regions"] = [_filter_region_terms(region) for region in doc["regions"]]
    return filtered


def _nl_text(region, with_tags: bool) -> str:
    nl = region.get("nl") if isinstance(region, dict) else None
    if not isinstance(nl, dict):
        nl = {}
    en = str(nl.get("en") or "").strip()
    zh = str(nl.get("zh") or "").strip()
    if en:
        return en
    if zh:
        return zh
    if with_tags:
        return ", ".join(_tag_list(region))
    return ""


def _literal_of(region) -> str:
    literal = region.get("literalText") if isinstance(region, dict) else ""
    return literal if isinstance(literal, str) else ""


def _global_caption(doc) -> str:
    global_info = doc["global"]
    caption = global_info.get("caption_en") or global_info.get("caption_zh") or ""
    return caption if isinstance(caption, str) else str(caption)


def _active_regions(doc):
    active = []
    for region in doc["regions"]:
        if not isinstance(region, dict) or region.get("muted"):
            continue
        bbox = _bbox_of(region)
        if not _has_area(bbox):
            continue
        active.append((region, bbox))
    return active


def _region_token_stats(doc):
    stats = []
    for region, _bbox in _active_regions(doc):
        nl = region.get("nl") if isinstance(region.get("nl"), dict) else {}
        caption = str(nl.get("en") or "").strip() or str(nl.get("zh") or "").strip()
        content = ", ".join(region.get("tags") or [])
        if caption:
            content = content + (", " if content else "") + caption
        stats.append((str(region.get("id") or ""), estimate_tokens(content)))
    return stats


# These adapters mirror frontend/index.html RegionAdapters and must stay
# byte-compatible. compose() normalizes tags before calling them, matching the
# panel's regionSanitizeRegion() ingestion step.
def to_moss_doc(doc):
    data = _as_doc(doc)
    if not data["w"] or data["w"] % 16 != 0:
        raise ValueError(
            "MOSS 导出要求画布宽度是 16 的倍数（当前 "
            + (str(data["w"]) if data["w"] else "未设置")
            + "），请在全局区调整画布尺寸"
        )
    if not data["h"] or data["h"] % 16 != 0:
        raise ValueError(
            "MOSS 导出要求画布高度是 16 的倍数（当前 "
            + (str(data["h"]) if data["h"] else "未设置")
            + "），请在全局区调整画布尺寸"
        )

    width = data["w"]
    height = data["h"]
    items = []
    for region, bbox in _active_regions(data):
        item = {
            "label": "text" if region.get("role") == "text" else "object",
            "caption": _nl_text(region, True),
            "bbox": [
                _js_round(bbox[1] * height - height / 2),
                _js_round(bbox[0] * width - width / 2),
                _js_round(bbox[3] * height - height / 2),
                _js_round(bbox[2] * width - width / 2),
            ],
        }
        if region.get("role") == "text":
            literal = _literal_of(region)
            if literal:
                item["text"] = literal
        items.append(item)

    return {
        "summary": {"meta": [], "canvas": "single", "caption": _global_caption(data)},
        "items": items,
        "bbox_coord_space": "abs_centralized",
        "bbox_format": "yxyx",
    }


def to_ideogram_doc(doc):
    data = _as_doc(doc)
    elements = []
    for region, bbox in _active_regions(data):
        box = [
            _js_round(bbox[1] * 1000),
            _js_round(bbox[0] * 1000),
            _js_round(bbox[3] * 1000),
            _js_round(bbox[2] * 1000),
        ]
        if region.get("role") == "text":
            elements.append(
                {
                    "type": "text",
                    "bbox": box,
                    "text": _literal_of(region),
                    "desc": _nl_text(region, False),
                }
            )
        else:
            elements.append(
                {
                    "type": "obj",
                    "bbox": box,
                    "desc": _nl_text(region, True),
                }
            )

    return {
        "high_level_description": _global_caption(data),
        "style_description": {
            "aesthetics": "",
            "lighting": "",
            "photo": "",
            "medium": "",
            "color_palette": [],
        },
        "compositional_deconstruction": {"background": "", "elements": elements},
    }


def to_tag_lines_doc(doc):
    data = _as_doc(doc)
    lines = []
    for region, _bbox in _active_regions(data):
        parts = []
        tags = ", ".join(_tag_list(region))
        if tags:
            parts.append(tags)
        nl = _nl_text(region, False)
        if nl:
            parts.append(nl)
        line = ", ".join(parts)
        if region.get("role") == "text":
            literal = _literal_of(region)
            if literal:
                line = 'text: "' + literal.replace('"', '\\"') + '" | ' + line
        # Product rule: a region with no tags and no NL text emits no line.
        if not line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _sanitize_region(raw, index: int):
    if not isinstance(raw, dict):
        return None
    bbox = _bbox_of(raw)
    if bbox is None:
        return None
    nl = raw.get("nl")
    if not isinstance(nl, dict):
        nl = {}
    region_id = raw.get("id")
    if not isinstance(region_id, str) or not region_id:
        region_id = "r" + str(index + 1)
    tags = []
    for tag in _tag_list(raw):
        name = normalize_tag(tag)
        if name:
            tags.append(name)
    region = {
        "id": region_id,
        "bbox": bbox,
        "role": "text" if raw.get("role") == "text" else "obj",
        "literalText": _literal_of(raw),
        "nl": {"zh": str(nl.get("zh") or ""), "en": str(nl.get("en") or "")},
        "tags": tags,
        "muted": bool(raw.get("muted")),
        "linkedTo": str(raw["linkedTo"]) if raw.get("linkedTo") else None,
    }
    colors = raw.get("colors")
    if isinstance(colors, list):
        region["colors"] = list(colors)
    return region


def _sanitize_doc(doc, width: int, height: int) -> dict:
    data = _as_doc(doc)
    schema = doc.get("schema")
    regions = []
    for index, raw in enumerate(data["regions"]):
        region = _sanitize_region(raw, index)
        if region is not None:
            regions.append(region)
    global_info = data["global"]
    return {
        "schema": schema if isinstance(schema, str) and schema else SCHEMA,
        "canvas": {"w": int(width), "h": int(height)},
        "global": {
            "caption_zh": str(global_info.get("caption_zh") or ""),
            "caption_en": str(global_info.get("caption_en") or ""),
        },
        "regions": regions,
    }


class StudioSuiteRegionLayout:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "layout_json": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                    },
                ),
                "canvas_width": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 16}),
                "canvas_height": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 16}),
            },
            "optional": {
                "image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("doc_json", "moss_layout", "ideogram_caption", "tag_lines")
    FUNCTION = "compose"
    CATEGORY = CATEGORY_NAME

    def compose(self, layout_json, canvas_width, canvas_height, image=None):
        text = layout_json.strip() if isinstance(layout_json, str) else ""
        if not text:
            print("[StudioSuiteRegionLayout] layout_json 为空，未生成区域布局输出")
            return ("", "", "", "")

        try:
            raw_doc = json.loads(text)
        except Exception as exc:
            print(f"[StudioSuiteRegionLayout] layout_json 解析失败，未生成输出：{exc}")
            return ("", "", "", "")

        if not isinstance(raw_doc, dict):
            print("[StudioSuiteRegionLayout] layout_json 不是 JSON 对象，未生成输出")
            return ("", "", "", "")

        width = int(canvas_width)
        height = int(canvas_height)
        normalized = _sanitize_doc(raw_doc, width, height)
        filtered = _filter_doc_terms(normalized)

        doc_json = dump_json(normalized)
        moss_layout = dump_json(to_moss_doc(filtered))
        ideogram_caption = dump_json(to_ideogram_doc(filtered))
        tag_lines = to_tag_lines_doc(filtered)

        stats = _region_token_stats(filtered)
        total_tokens = sum(tokens for _region_id, tokens in stats)
        print(
            "[StudioSuiteRegionLayout] tokens: total=%d regions=[%s]"
            % (
                total_tokens,
                ", ".join("%s=%d" % (region_id, tokens) for region_id, tokens in stats),
            )
        )
        if total_tokens > TOKEN_BUDGET:
            print(
                "[StudioSuiteRegionLayout][warn] tokens 超出预算: total=%d > %d"
                % (total_tokens, TOKEN_BUDGET)
            )
        return (doc_json, moss_layout, ideogram_caption, tag_lines)


NODE_CLASS_MAPPINGS = {
    "StudioSuiteRegionLayout": StudioSuiteRegionLayout,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StudioSuiteRegionLayout": DISPLAY_NAME,
}
