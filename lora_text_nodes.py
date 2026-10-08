# -*- coding: utf-8 -*-
"""
LoRA 文本翻译器节点
解析规则严格照抄 webui (forge) 源码 extensions-builtin/sd_forge_lora/extra_networks_lora.py

LBW 具体语法待确认 —— 目前仅原样存储 lbw= 开头的命名参数，不做进一步解析。
"""

import json
import re


def _parse_lora_tag(inner_content):
    """
    解析 <lora:名字:参数...> 的内部内容。
    返回 (name, te_multiplier, unet_multiplier, lbw_string_or_None, raw_item_list)
    """
    items = inner_content.split(":")
    name = items[0].strip()
    named = {}
    positional = []
    lbw_value = None

    for item in items[1:]:
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, _, val = item.partition("=")
            key = key.strip().lower()
            val = val.strip()
            named[key] = val
            if key == "lbw":
                lbw_value = val
        else:
            positional.append(item)

    # positional[0] 是 te_multiplier，positional[1] 是 unet_multiplier
    # 对应 forge 源码: te_multiplier = float(items[1]) if len(items)>1 else 1.0
    #                 unet_multiplier = float(items[2]) if len(items)>2 else te_multiplier
    # 其中 items[0] 是名字，items[1:] 才是参数
    te_multiplier = float(positional[0]) if len(positional) > 0 else 1.0
    if "te" in named:
        te_multiplier = float(named["te"])

    unet_multiplier = float(positional[1]) if len(positional) > 1 else te_multiplier
    if "unet" in named:
        unet_multiplier = float(named["unet"])

    return name, te_multiplier, unet_multiplier, lbw_value


def parse_lora_text(text):
    """
    从文本中提取所有 <lora:...> / <lyco:...> 标记。
    返回 (cleaned_text, lora_stack_list)
    """
    # 匹配 <lora:...> 或 <lyco:...>，大小写不敏感，内容不含 >
    pattern = re.compile(r"<(lora|lyco):([^>]*)>", re.IGNORECASE)
    lora_stack = []

    def _replace(match):
        inner = match.group(2)
        raw = match.group(0)
        try:
            name, te, unet, lbw = _parse_lora_tag(inner)
        except Exception:
            # 解析失败则原样保留
            return raw
        entry = {
            "name": name,
            "clip_strength": te,
            "model_strength": unet,
            "raw": raw,
        }
        if lbw is not None:
            entry["lbw"] = lbw
        lora_stack.append(entry)
        return ""

    cleaned = pattern.sub(_replace, text)
    # 清理多余空格（标记删除后可能留下连续空格或 ", ," 之类）
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # 反复合并 "a, , b" → "a, b"
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = re.sub(r",\s*,", ",", cleaned)
    # 去掉首尾逗号和空白
    cleaned = cleaned.strip()
    cleaned = re.sub(r"^,\s*", "", cleaned)
    cleaned = re.sub(r",\s*$", "", cleaned)

    return cleaned.strip(), lora_stack


class StudioSuite_LoraFromText:
    CATEGORY = "Studio Suite/LoRA"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("clean_text", "lora_stack_json")
    FUNCTION = "translate"
    DESCRIPTION = (
        "LoRA 文本翻译器。从 prompt 文本中解析 <lora:...>/<lyco:...> 标记，"
        "输出清理后的文本 + LORA_STACK JSON。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"default": "", "multiline": True}),
            },
        }

    def translate(self, text):
        cleaned, stack = parse_lora_text(str(text or ""))
        return (cleaned, json.dumps(stack, ensure_ascii=False))


class StudioSuite_ApplyLoraStack:
    """把 LORA_STACK JSON 真正应用到 MODEL/CLIP 上（依次加载）。"""

    CATEGORY = "Studio Suite/LoRA"
    RETURN_TYPES = ("MODEL", "CLIP")
    RETURN_NAMES = ("model", "clip")
    FUNCTION = "apply"
    DESCRIPTION = ("读取 LoRA 文本翻译器输出的 lora_stack_json，按 name 在本机 loras 目录中定位文件，"
                   "依次以 model_strength / clip_strength 应用，返回新的 MODEL 和 CLIP。")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "lora_stack_json": ("STRING", {"default": "[]", "multiline": True, "forceInput": True}),
                "model": ("MODEL",),
                "clip": ("CLIP",),
            },
        }

    @staticmethod
    def _resolve(name):
        import folder_paths
        candidates = folder_paths.get_filename_list("loras")
        target = str(name or "").strip().replace("/", "\\")
        if not target:
            return None
        low = target.lower()
        for c in candidates:
            if c.lower() == low:
                return c
        for c in candidates:
            if c.lower().replace("\\", "/") == low.replace("\\", "/"):
                return c
        stem_low = low.rsplit(".", 1)[0]
        for c in candidates:
            cs = c.lower().rsplit(".", 1)[0]
            if cs == stem_low or cs.endswith("/" + stem_low) or cs.endswith("\\" + stem_low):
                return c
        return None

    def apply(self, lora_stack_json, model, clip):
        import json as _json
        import folder_paths
        import comfy.sd
        import comfy.utils
        try:
            entries = _json.loads(str(lora_stack_json or "[]"))
        except Exception:
            entries = []
        if not isinstance(entries, list):
            entries = []
        applied = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            rel = self._resolve(name)
            if not rel:
                print("[StudioSuite_ApplyLoraStack] LoRA not found:", name)
                continue
            path = folder_paths.get_full_path("loras", rel)
            ms = float(entry.get("model_strength", 1.0))
            cs = float(entry.get("clip_strength", 1.0))
            lora_sd = comfy.utils.load_torch_file(path, safe_load=True)
            model, clip = comfy.sd.load_lora_for_models(model, clip, lora_sd, ms, cs)
            applied.append(name)
        print("[StudioSuite_ApplyLoraStack] applied:", applied)
        return (model, clip)


NODE_CLASS_MAPPINGS = {
    "StudioSuite_LoraFromText": StudioSuite_LoraFromText,
    "StudioSuite_ApplyLoraStack": StudioSuite_ApplyLoraStack,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StudioSuite_LoraFromText": "LoRA 文本翻译",
    "StudioSuite_ApplyLoraStack": "LoRA 堆应用",
}
