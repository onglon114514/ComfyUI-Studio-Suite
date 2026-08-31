import copy
import fnmatch
import glob
import json
import re
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont


def _ensure_comfy_root_on_path():
    for parent in Path(__file__).resolve().parents:
        if (parent / "server.py").exists() and (parent / "utils" / "install_util.py").exists():
            parent_text = str(parent)
            if parent_text not in sys.path:
                sys.path.insert(0, parent_text)
            existing_utils = sys.modules.get("utils")
            if existing_utils is not None and not hasattr(existing_utils, "__path__"):
                sys.modules.pop("utils", None)
            return


_ensure_comfy_root_on_path()
import utils.install_util  # Ensure ComfyUI's top-level utils package wins before server imports comfy.utils.
import folder_paths
from server import PromptServer
import nodes as comfy_nodes


XY_VERSION = 1
DEFAULT_XY_OUTPUT_SUBDIR = "studio_suite_xy"
XY_QUEUE_NODE_CLASS = "StudioSuiteXYQueue"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


class AnyType(str):
    def __ne__(self, value):
        return False


ANY_TYPE = AnyType("*")


def _send_status(unique_id, text):
    try:
        PromptServer.instance.send_progress_text(str(text), unique_id)
    except Exception:
        pass


def _strip_comment(line):
    text = str(line or "").strip()
    if not text or text.startswith("#") or text.startswith("//"):
        return ""
    return text


def _split_names(input_names):
    names = [item.strip() for item in str(input_names or "").split(",") if item.strip()]
    if not names:
        raise ValueError("At least one input name is required")
    return names


def _coerce_value(raw):
    text = str(raw).strip()
    if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
        return text[1:-1]

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("none", "null"):
        return None

    if re.fullmatch(r"[-+]?\d+", text):
        try:
            return int(text)
        except Exception:
            return text
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", text) or re.fullmatch(
        r"[-+]?\d+[eE][-+]?\d+", text
    ):
        try:
            return float(text)
        except Exception:
            return text
    return text


def _safe_label(text, fallback="cell"):
    value = str(text or "").strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    return value[:96] or fallback


def _axis_json(axis_label, items):
    return json.dumps(
        {
            "version": XY_VERSION,
            "axis_label": str(axis_label or "axis").strip() or "axis",
            "items": items,
        },
        ensure_ascii=False,
    )


def _parse_axis_json(axis_json, allow_empty=False):
    if not str(axis_json or "").strip():
        if allow_empty:
            return {
                "version": XY_VERSION,
                "axis_label": "",
                "items": [
                    {
                        "label": "",
                        "safe_label": "single",
                        "assignments": [],
                    }
                ],
            }
        raise ValueError("Axis JSON is empty")
    data = json.loads(axis_json)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Invalid XY axis JSON")
    return data


def _assignment(node_id, input_name, value):
    if not str(node_id or "").strip():
        raise ValueError("target_node_id is required")
    if not str(input_name or "").strip():
        raise ValueError("input_name is required")
    return {
        "node_id": str(node_id).strip(),
        "input_name": str(input_name).strip(),
        "value": value,
    }


def _resolve_target_node_id(target_node_id, target_ref=""):
    ref_text = str(target_ref or "").strip()
    if ref_text:
        try:
            data = json.loads(ref_text)
            for key in ("node_id", "target_node_id", "source_node_id"):
                if str(data.get(key, "")).strip():
                    return str(data[key]).strip()
        except Exception:
            return ref_text
    return str(target_node_id or "").strip()


def _find_parameter_node_id(prompt, slot_name):
    slot = str(slot_name or "").strip()
    if not slot:
        raise ValueError("XY parameter slot_name is required")
    matched = []
    for node_id, node in (prompt or {}).items():
        if node.get("class_type") != "StudioSuiteXYParameterInput":
            continue
        if str((node.get("inputs") or {}).get("slot_name", "")).strip() == slot:
            matched.append(str(node_id))
    if len(matched) != 1:
        raise ValueError(f"Expected exactly one XY Parameter Input with slot '{slot}', found {len(matched)}")
    return matched[0]


def _parameter_assignment(prompt, slot_name, value):
    return _assignment(
        _find_parameter_node_id(prompt, slot_name),
        "value_json",
        json.dumps(value, ensure_ascii=False),
    )


def _source_node_id_from_link(prompt, unique_id, input_name):
    if not prompt or unique_id is None:
        raise ValueError("Current prompt data is missing; cannot resolve XY target bridge")
    node = prompt.get(str(unique_id))
    if not isinstance(node, dict):
        raise ValueError(f"XY target bridge node not found in prompt: {unique_id}")
    inputs = node.get("inputs") or {}
    link = inputs.get(input_name)
    if isinstance(link, (list, tuple)) and len(link) >= 1:
        return str(link[0])
    raise ValueError(f"XY target bridge input '{input_name}' is not connected")


def _target_ref_json(source_node_id, source_input_name, prompt):
    node = (prompt or {}).get(str(source_node_id), {})
    return json.dumps(
        {
            "version": XY_VERSION,
            "node_id": str(source_node_id),
            "source_input_name": source_input_name,
            "class_type": node.get("class_type", ""),
        },
        ensure_ascii=False,
    )


def _match_filter(name, filters, default_match=True):
    patterns = [part.strip().lower() for part in str(filters or "").replace("\n", ",").split(",") if part.strip()]
    if not patterns:
        return default_match
    lowered = str(name or "").lower()
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[]"):
            if fnmatch.fnmatch(lowered, pattern):
                return True
        elif pattern in lowered:
            return True
    return False


def _available_lora_names():
    try:
        return list(folder_paths.get_filename_list("loras"))
    except Exception:
        return []


def _resolve_output_dir(output_path):
    resolved = str(output_path or DEFAULT_XY_OUTPUT_SUBDIR).strip() or DEFAULT_XY_OUTPUT_SUBDIR
    path = Path(resolved)
    if not path.is_absolute():
        path = Path(folder_paths.get_output_directory()) / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _render_template(template, cell, run_id):
    text = str(template or "xy_r%row%_c%col%_%x_label%_%y_label%")
    replacements = {
        "%run_id%": run_id,
        "%row%": str(cell.get("row", 0) + 1).zfill(2),
        "%col%": str(cell.get("col", 0) + 1).zfill(2),
        "%row0%": str(cell.get("row", 0)).zfill(2),
        "%col0%": str(cell.get("col", 0)).zfill(2),
        "%x_label%": _safe_label(cell.get("x_label"), "x"),
        "%y_label%": _safe_label(cell.get("y_label"), "y"),
        "%cell_label%": _safe_label(cell.get("label"), "cell"),
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return _safe_label(text, "xy_cell")


def _collect_output_targets(prompt, scheduler_id):
    targets = []
    for node_id, node in prompt.items():
        class_type = node.get("class_type")
        cls = comfy_nodes.NODE_CLASS_MAPPINGS.get(class_type)
        if not cls or not getattr(cls, "OUTPUT_NODE", False):
            continue
        if str(node_id) == str(scheduler_id):
            continue
        inputs = node.get("inputs") or {}
        if inputs.get("enabled", True) is False:
            continue
        targets.append(str(node_id))
    return targets


def _apply_assignments(child_prompt, assignments):
    for item in assignments:
        node_id = str(item.get("node_id", "")).strip()
        input_name = str(item.get("input_name", "")).strip()
        if node_id not in child_prompt:
            raise ValueError(f"XY target node id not found: {node_id}")
        if not input_name:
            raise ValueError(f"XY input name is empty for node {node_id}")
        child_prompt[node_id].setdefault("inputs", {})[input_name] = item.get("value")


def _read_manifest(manifest_path, output_path):
    if str(manifest_path or "").strip():
        path = Path(str(manifest_path).strip())
        if not path.is_file():
            raise ValueError(f"XY manifest not found: {manifest_path}")
        return json.loads(path.read_text(encoding="utf-8"))

    output_dir = _resolve_output_dir(output_path)
    manifests = sorted(output_dir.glob("xy_manifest_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not manifests:
        raise ValueError(f"No xy_manifest_*.json found in: {output_dir}")
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def _find_cell_image(output_dir, expected_prefix, image_ext):
    ext = str(image_ext or "png").strip().lower().lstrip(".") or "png"
    candidates = [
        output_dir / f"{expected_prefix}.{ext}",
        output_dir / f"{expected_prefix}_00001.{ext}",
        output_dir / f"{expected_prefix}00001.{ext}",
    ]
    for path in candidates:
        if path.is_file():
            return path
    escaped_prefix = glob.escape(str(expected_prefix or ""))
    globbed = sorted(output_dir.glob(f"{escaped_prefix}*.{ext}"))
    if globbed:
        return globbed[0]
    for suffix in IMAGE_EXTENSIONS:
        globbed = sorted(output_dir.glob(f"{escaped_prefix}*{suffix}"))
        if globbed:
            return globbed[0]
    return None


def _pil_to_tensor(image):
    array = np.asarray(image.convert("RGB")).astype(np.float32) / 255.0
    return torch.from_numpy(array)[None,]


class StudioSuiteXYParameterInput:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = (ANY_TYPE, "STRING", "FLOAT", "INT", "BOOLEAN")
    RETURN_NAMES = ("value", "string", "float", "int", "boolean")
    FUNCTION = "read_value"
    DESCRIPTION = "Main-workflow parameter inlet. XY schedulers find it by slot_name and inject one child-job value into value_json."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "slot_name": ("STRING", {"default": "xy_value", "multiline": False}),
                "value_json": (
                    "STRING",
                    {
                        "default": '""',
                        "multiline": False,
                        "tooltip": "Default JSON value. The XY queue replaces this field only in child prompts.",
                    },
                ),
            }
        }

    def read_value(self, slot_name, value_json):
        try:
            value = json.loads(str(value_json))
        except Exception:
            value = str(value_json or "")
        text = str(value) if value is not None else ""
        try:
            float_value = float(value)
        except (TypeError, ValueError):
            float_value = 0.0
        try:
            int_value = int(float(value))
        except (TypeError, ValueError):
            int_value = 0
        if isinstance(value, str):
            bool_value = value.strip().lower() in ("1", "true", "yes", "on")
        else:
            bool_value = bool(value)
        return (value, text, float_value, int_value, bool_value)


class StudioSuiteXYAxisParameter:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("x_or_y_axis_json",)
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis for an XY Parameter Input selected by slot_name. No ComfyUI node id is required."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "axis_label": ("STRING", {"default": "Parameter", "multiline": False}),
                "slot_name": ("STRING", {"default": "xy_value", "multiline": False}),
                "values_text": (
                    "STRING",
                    {
                        "default": "20\n30\n40",
                        "multiline": True,
                        "tooltip": "One value per line. Formats: value or label|value.",
                    },
                ),
            },
            "hidden": {"prompt": "PROMPT"},
        }

    def build_axis(self, axis_label, slot_name, values_text, prompt=None):
        items = []
        for line in str(values_text or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) == 2:
                label, raw_value = parts
            else:
                label = raw_value = parts[0]
            items.append(
                {
                    "label": label,
                    "safe_label": _safe_label(label),
                    "assignments": [_parameter_assignment(prompt, slot_name, _coerce_value(raw_value))],
                }
            )
        if not items:
            raise ValueError("No XY parameter values were parsed")
        return (_axis_json(axis_label, items),)


class StudioSuiteXYTargetModelClipBridge:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model", "clip", "target_ref")
    FUNCTION = "bridge"
    DESCRIPTION = "Pass MODEL/CLIP through and expose the upstream node id as target_ref for XY axes. Put it directly after a LoRA Loader."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    def bridge(self, model, clip, prompt=None, unique_id=None):
        source_node_id = _source_node_id_from_link(prompt, unique_id, "model")
        return (model, clip, _target_ref_json(source_node_id, "model", prompt))


class StudioSuiteXYTargetModelBridge:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("MODEL", "STRING")
    RETURN_NAMES = ("model", "target_ref")
    FUNCTION = "bridge"
    DESCRIPTION = "Pass MODEL through and expose the upstream node id as target_ref for XY axes. Use it after model-only LoRA loaders."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    def bridge(self, model, prompt=None, unique_id=None):
        source_node_id = _source_node_id_from_link(prompt, unique_id, "model")
        return (model, _target_ref_json(source_node_id, "model", prompt))


class StudioSuiteXYAxisGeneric:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("x_or_y_axis_json",)
    FUNCTION = "build_axis"
    DESCRIPTION = "Build a generic XY axis by targeting one node id and one or more input names. Line format: value, label|value, or label|v1|v2 for multiple inputs."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "axis_label": ("STRING", {"default": "Steps", "multiline": False}),
                "target_node_id": ("STRING", {"default": "", "multiline": False}),
                "input_names": ("STRING", {"default": "steps", "multiline": False}),
                "values_text": (
                    "STRING",
                    {
                        "default": "20\n30\n40",
                        "multiline": True,
                        "tooltip": "One value per line. For two inputs use: label|value1|value2",
                    },
                ),
            },
        }

    def build_axis(self, axis_label, target_node_id, input_names, values_text, target_ref=""):
        target_node_id = _resolve_target_node_id(target_node_id, target_ref)
        names = _split_names(input_names)
        items = []
        for line in str(values_text or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(names) == 1:
                if len(parts) >= 2:
                    label, raw_value = parts[0], "|".join(parts[1:]).strip()
                else:
                    label, raw_value = parts[0], parts[0]
                assignments = [_assignment(target_node_id, names[0], _coerce_value(raw_value))]
            else:
                if len(parts) < len(names) + 1:
                    raise ValueError(f"Expected label plus {len(names)} values: {line}")
                label = parts[0]
                assignments = [
                    _assignment(target_node_id, name, _coerce_value(value))
                    for name, value in zip(names, parts[1 : len(names) + 1])
                ]
            items.append({"label": label, "safe_label": _safe_label(label), "assignments": assignments})
        if not items:
            raise ValueError("No XY axis values were parsed")
        return (_axis_json(axis_label, items),)


class StudioSuiteXYAxisSamplerScheduler:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("x_or_y_axis_json",)
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis for KSampler sampler_name + scheduler combinations."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target_node_id": ("STRING", {"default": "", "multiline": False}),
                "sampler_input_name": ("STRING", {"default": "sampler_name", "multiline": False}),
                "scheduler_input_name": ("STRING", {"default": "scheduler", "multiline": False}),
                "combos_text": (
                    "STRING",
                    {
                        "default": "euler normal|euler|normal\neuler karras|euler|karras\ndpmpp_2m karras|dpmpp_2m|karras\ndpmpp_2m sgm_uniform|dpmpp_2m|sgm_uniform\ndpmpp_3m_sde karras|dpmpp_3m_sde|karras",
                        "multiline": True,
                    },
                ),
            },
        }

    def build_axis(self, target_node_id, sampler_input_name, scheduler_input_name, combos_text, target_ref=""):
        target_node_id = _resolve_target_node_id(target_node_id, target_ref)
        items = []
        for line in str(combos_text or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) != 3:
                raise ValueError(f"Sampler axis line must be label|sampler|scheduler: {line}")
            label, sampler, scheduler = parts
            items.append(
                {
                    "label": label,
                    "safe_label": _safe_label(label),
                    "assignments": [
                        _assignment(target_node_id, sampler_input_name, sampler),
                        _assignment(target_node_id, scheduler_input_name, scheduler),
                    ],
                }
            )
        if not items:
            raise ValueError("No sampler/scheduler combos were parsed")
        return (_axis_json("Sampler Scheduler", items),)


class StudioSuiteXYAxisFreeU:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("x_or_y_axis_json",)
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis for FreeU style b1/b2/s1/s2 presets."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target_node_id": ("STRING", {"default": "", "multiline": False}),
                "input_names": ("STRING", {"default": "b1,b2,s1,s2", "multiline": False}),
                "presets_text": (
                    "STRING",
                    {
                        "default": "off|1.0|1.0|1.0|1.0\nsoft|1.05|1.08|0.95|0.8\nbalanced|1.1|1.2|0.9|0.6\nstrong|1.2|1.4|0.8|0.4",
                        "multiline": True,
                    },
                ),
            },
        }

    def build_axis(self, target_node_id, input_names, presets_text, target_ref=""):
        target_node_id = _resolve_target_node_id(target_node_id, target_ref)
        names = _split_names(input_names)
        if len(names) != 4:
            raise ValueError("FreeU axis requires exactly four input names")
        items = []
        for line in str(presets_text or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) != 5:
                raise ValueError(f"FreeU line must be label|b1|b2|s1|s2: {line}")
            label = parts[0]
            items.append(
                {
                    "label": label,
                    "safe_label": _safe_label(label),
                    "assignments": [
                        _assignment(target_node_id, name, _coerce_value(value))
                        for name, value in zip(names, parts[1:])
                    ],
                }
            )
        if not items:
            raise ValueError("No FreeU presets were parsed")
        return (_axis_json("FreeU", items),)


class StudioSuiteXYAxisLoraStrength:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("x_or_y_axis_json",)
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis for an existing LoRA Loader strength_model / strength_clip inputs."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target_node_id": ("STRING", {"default": "", "multiline": False}),
                "strength_model_input": ("STRING", {"default": "strength_model", "multiline": False}),
                "strength_clip_input": ("STRING", {"default": "strength_clip", "multiline": False}),
                "strengths_text": (
                    "STRING",
                    {
                        "default": "0, 0.25, 0.5, 0.75, 1.0",
                        "multiline": True,
                        "tooltip": "Comma or newline separated model strengths.",
                    },
                ),
                "clip_strength_mode": (["same_as_model", "fixed", "zero"], {"default": "same_as_model"}),
                "fixed_clip_strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05}),
            },
        }

    def build_axis(
        self,
        target_node_id,
        strength_model_input,
        strength_clip_input,
        strengths_text,
        clip_strength_mode,
        fixed_clip_strength,
        target_ref="",
    ):
        target_node_id = _resolve_target_node_id(target_node_id, target_ref)
        raw_values = re.split(r"[,\n\r]+", str(strengths_text or ""))
        strengths = [_coerce_value(value) for value in raw_values if str(value).strip()]
        items = []
        for strength in strengths:
            if not isinstance(strength, (int, float)):
                raise ValueError(f"LoRA strength must be numeric: {strength}")
            if clip_strength_mode == "fixed":
                clip_strength = float(fixed_clip_strength)
            elif clip_strength_mode == "zero":
                clip_strength = 0.0
            else:
                clip_strength = float(strength)
            label = f"lora {strength:g}" if isinstance(strength, float) else f"lora {strength}"
            items.append(
                {
                    "label": label,
                    "safe_label": _safe_label(label),
                    "assignments": [
                        _assignment(target_node_id, strength_model_input, float(strength)),
                        _assignment(target_node_id, strength_clip_input, clip_strength),
                    ],
                }
            )
        if not items:
            raise ValueError("No LoRA strengths were parsed")
        return (_axis_json("LoRA Strength", items),)


class StudioSuiteXYAxisLoraFile:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("x_or_y_axis_json", "matched_loras")
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis that swaps LoRA files and optional strength_model / strength_clip values on an existing LoRA Loader."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target_node_id": ("STRING", {"default": "", "multiline": False}),
                "lora_name_input": ("STRING", {"default": "lora_name", "multiline": False}),
                "strength_model_input": ("STRING", {"default": "strength_model", "multiline": False}),
                "strength_clip_input": ("STRING", {"default": "strength_clip", "multiline": False}),
                "axis_label": ("STRING", {"default": "LoRA File", "multiline": False}),
                "lora_names_text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": "Optional manual list. Formats: lora, label|lora, label|lora|model_strength, label|lora|model_strength|clip_strength. Leave empty to scan ComfyUI loras.",
                    },
                ),
                "default_model_strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05}),
                "default_clip_strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05}),
                "write_strengths": ("BOOLEAN", {"default": True}),
                "include_filter": (
                    "STRING",
                    {
                        "default": "*",
                        "multiline": False,
                        "tooltip": "Used only when lora_names_text is empty. Supports comma separated substrings or wildcards, e.g. my_lora*, epoch_*.safetensors.",
                    },
                ),
                "exclude_filter": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Comma separated substrings or wildcards to exclude from scanned LoRA names.",
                    },
                ),
                "sort_mode": (["name_asc", "name_desc"], {"default": "name_asc"}),
                "limit": ("INT", {"default": 0, "min": 0, "max": 1000}),
            },
        }

    def _lora_assignments(
        self,
        target_node_id,
        lora_name_input,
        strength_model_input,
        strength_clip_input,
        lora_name,
        model_strength,
        clip_strength,
        write_strengths,
    ):
        assignments = [_assignment(target_node_id, lora_name_input, lora_name)]
        if bool(write_strengths):
            assignments.append(_assignment(target_node_id, strength_model_input, float(model_strength)))
            assignments.append(_assignment(target_node_id, strength_clip_input, float(clip_strength)))
        return assignments

    def _manual_items(
        self,
        target_node_id,
        lora_name_input,
        strength_model_input,
        strength_clip_input,
        lora_names_text,
        default_model_strength,
        default_clip_strength,
        write_strengths,
    ):
        items = []
        for line in str(lora_names_text or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) >= 2:
                label, lora_name = parts[0], parts[1]
            else:
                lora_name = parts[0]
                label = Path(lora_name).stem
            model_strength = float(default_model_strength)
            clip_strength = float(default_clip_strength)
            if len(parts) >= 3 and parts[2]:
                model_strength = float(_coerce_value(parts[2]))
                clip_strength = model_strength
            if len(parts) >= 4 and parts[3]:
                clip_strength = float(_coerce_value(parts[3]))
            if len(parts) > 4:
                raise ValueError(f"LoRA File line has too many fields. Use label|lora|model|clip: {line}")
            strength_suffix = ""
            if bool(write_strengths):
                strength_suffix = f" [{model_strength:g}/{clip_strength:g}]"
            items.append(
                {
                    "label": f"{label}{strength_suffix}",
                    "safe_label": _safe_label(f"{label}{strength_suffix}"),
                    "assignments": self._lora_assignments(
                        target_node_id,
                        lora_name_input,
                        strength_model_input,
                        strength_clip_input,
                        lora_name,
                        model_strength,
                        clip_strength,
                        write_strengths,
                    ),
                }
            )
        return items

    def _scanned_items(
        self,
        target_node_id,
        lora_name_input,
        strength_model_input,
        strength_clip_input,
        include_filter,
        exclude_filter,
        sort_mode,
        limit,
        default_model_strength,
        default_clip_strength,
        write_strengths,
    ):
        names = []
        for lora_name in _available_lora_names():
            if not _match_filter(lora_name, include_filter, default_match=True):
                continue
            if _match_filter(lora_name, exclude_filter, default_match=False):
                continue
            names.append(lora_name)

        names.sort(key=lambda item: item.lower(), reverse=(sort_mode == "name_desc"))
        if int(limit or 0) > 0:
            names = names[: int(limit)]

        items = []
        for lora_name in names:
            label = Path(lora_name).stem
            if bool(write_strengths):
                label = f"{label} [{float(default_model_strength):g}/{float(default_clip_strength):g}]"
            items.append(
                {
                    "label": label,
                    "safe_label": _safe_label(label),
                    "assignments": self._lora_assignments(
                        target_node_id,
                        lora_name_input,
                        strength_model_input,
                        strength_clip_input,
                        lora_name,
                        default_model_strength,
                        default_clip_strength,
                        write_strengths,
                    ),
                }
            )
        return items

    def build_axis(
        self,
        target_node_id,
        lora_name_input,
        strength_model_input,
        strength_clip_input,
        axis_label,
        lora_names_text,
        default_model_strength,
        default_clip_strength,
        write_strengths,
        include_filter,
        exclude_filter,
        sort_mode,
        limit,
        target_ref="",
    ):
        target_node_id = _resolve_target_node_id(target_node_id, target_ref)
        if str(lora_names_text or "").strip():
            items = self._manual_items(
                target_node_id,
                lora_name_input,
                strength_model_input,
                strength_clip_input,
                lora_names_text,
                default_model_strength,
                default_clip_strength,
                write_strengths,
            )
        else:
            items = self._scanned_items(
                target_node_id,
                lora_name_input,
                strength_model_input,
                strength_clip_input,
                include_filter,
                exclude_filter,
                sort_mode,
                limit,
                default_model_strength,
                default_clip_strength,
                write_strengths,
            )
        if not items:
            raise ValueError("No LoRA files matched. Check lora_names_text or include_filter.")
        matched = "\n".join(item["assignments"][0]["value"] for item in items)
        return (_axis_json(axis_label or "LoRA File", items), matched)


class StudioSuiteXYAxisLoraCompare:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("x_or_y_axis_json", "matched_loras")
    FUNCTION = "build_axis"
    DESCRIPTION = "Compare LoRA files and an optional strength range through named XY Parameter Inputs. No target node ids are required."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "lora_name_slot": ("STRING", {"default": "lora_name", "multiline": False}),
                "model_strength_slot": ("STRING", {"default": "lora_model_strength", "multiline": False}),
                "clip_strength_slot": ("STRING", {"default": "lora_clip_strength", "multiline": False}),
                "axis_label": ("STRING", {"default": "LoRA Compare", "multiline": False}),
                "lora_names_text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": "One LoRA per line. Formats: lora_name or label|lora_name. Leave empty to scan ComfyUI's LoRA folder.",
                    },
                ),
                "strength_mode": (["fixed", "range"], {"default": "fixed"}),
                "start_strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05}),
                "end_strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05}),
                "strength_steps": ("INT", {"default": 1, "min": 1, "max": 100}),
                "clip_strength_mode": (["same_as_model", "fixed", "zero"], {"default": "same_as_model"}),
                "fixed_clip_strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05}),
                "include_filter": ("STRING", {"default": "*", "multiline": False}),
                "exclude_filter": ("STRING", {"default": "", "multiline": False}),
                "sort_mode": (["name_asc", "name_desc"], {"default": "name_asc"}),
                "limit": ("INT", {"default": 0, "min": 0, "max": 1000}),
            },
            "hidden": {"prompt": "PROMPT"},
        }

    def _lora_entries(self, lora_names_text, include_filter, exclude_filter, sort_mode, limit):
        entries = []
        if str(lora_names_text or "").strip():
            for line in str(lora_names_text).splitlines():
                line = _strip_comment(line)
                if not line:
                    continue
                parts = [part.strip() for part in line.split("|", 1)]
                if len(parts) == 2:
                    label, lora_name = parts
                else:
                    lora_name = parts[0]
                    label = Path(lora_name).stem
                entries.append((label, lora_name))
        else:
            names = [
                name
                for name in _available_lora_names()
                if _match_filter(name, include_filter, default_match=True)
                and not _match_filter(name, exclude_filter, default_match=False)
            ]
            names.sort(key=lambda item: item.lower(), reverse=(sort_mode == "name_desc"))
            entries = [(Path(name).stem, name) for name in names]
        if int(limit or 0) > 0:
            entries = entries[: int(limit)]
        return entries

    @staticmethod
    def _strengths(strength_mode, start_strength, end_strength, strength_steps):
        if strength_mode != "range" or int(strength_steps) <= 1:
            return [float(start_strength)]
        return [float(value) for value in np.linspace(float(start_strength), float(end_strength), int(strength_steps))]

    def build_axis(
        self,
        lora_name_slot,
        model_strength_slot,
        clip_strength_slot,
        axis_label,
        lora_names_text,
        strength_mode,
        start_strength,
        end_strength,
        strength_steps,
        clip_strength_mode,
        fixed_clip_strength,
        include_filter,
        exclude_filter,
        sort_mode,
        limit,
        prompt=None,
    ):
        entries = self._lora_entries(lora_names_text, include_filter, exclude_filter, sort_mode, limit)
        strengths = self._strengths(strength_mode, start_strength, end_strength, strength_steps)
        items = []
        for label, lora_name in entries:
            for model_strength in strengths:
                if clip_strength_mode == "fixed":
                    clip_strength = float(fixed_clip_strength)
                elif clip_strength_mode == "zero":
                    clip_strength = 0.0
                else:
                    clip_strength = model_strength
                item_label = label if len(strengths) == 1 else f"{label} [{model_strength:g}/{clip_strength:g}]"
                items.append(
                    {
                        "label": item_label,
                        "safe_label": _safe_label(item_label),
                        "assignments": [
                            _parameter_assignment(prompt, lora_name_slot, lora_name),
                            _parameter_assignment(prompt, model_strength_slot, model_strength),
                            _parameter_assignment(prompt, clip_strength_slot, clip_strength),
                        ],
                    }
                )
        if not items:
            raise ValueError("No LoRA files matched. Check lora_names_text or include_filter.")
        matched = "\n".join(name for _, name in entries)
        return (_axis_json(axis_label or "LoRA Compare", items), matched)


class StudioSuiteXYAxisLoraStacker:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("x_or_y_axis_json", "parsed_stacks")
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis for Efficiency Nodes TSC LoRA Stacker style widgets: lora_count, lora_name_i and weights."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target_node_id": ("STRING", {"default": "", "multiline": False}),
                "axis_label": ("STRING", {"default": "LoRA Stack", "multiline": False}),
                "stack_lines": (
                    "STRING",
                    {
                        "default": "single_a|example_a.safetensors|1.0\ncombo_ab|example_a.safetensors|0.8|example_b.safetensors|0.6",
                        "multiline": True,
                        "tooltip": "Simple: label|lora|weight|lora|weight. Advanced: label|lora|model|clip|lora|model|clip.",
                    },
                ),
                "line_mode": (["simple_weight", "advanced_model_clip"], {"default": "simple_weight"}),
                "max_slots": ("INT", {"default": 10, "min": 1, "max": 50}),
                "input_mode_input": ("STRING", {"default": "input_mode", "multiline": False}),
                "input_mode_value": ("STRING", {"default": "simple", "multiline": False}),
                "lora_count_input": ("STRING", {"default": "lora_count", "multiline": False}),
                "lora_name_prefix": ("STRING", {"default": "lora_name_", "multiline": False}),
                "simple_weight_prefix": ("STRING", {"default": "lora_wt_", "multiline": False}),
                "model_strength_prefix": ("STRING", {"default": "model_str_", "multiline": False}),
                "clip_strength_prefix": ("STRING", {"default": "clip_str_", "multiline": False}),
                "clear_unused_slots": ("BOOLEAN", {"default": True}),
            },
        }

    def _parse_stack_line(self, line, line_mode):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            raise ValueError(f"LoRA stack line is too short: {line}")
        label = parts[0]
        values = parts[1:]
        group_size = 2 if line_mode == "simple_weight" else 3
        if len(values) % group_size != 0:
            raise ValueError(f"LoRA stack line has invalid field count: {line}")
        entries = []
        for index in range(0, len(values), group_size):
            lora_name = values[index]
            if not lora_name or lora_name == "None":
                continue
            if line_mode == "simple_weight":
                weight = float(_coerce_value(values[index + 1]))
                entries.append((lora_name, weight, weight))
            else:
                model_strength = float(_coerce_value(values[index + 1]))
                clip_strength = float(_coerce_value(values[index + 2]))
                entries.append((lora_name, model_strength, clip_strength))
        return label, entries

    def build_axis(
        self,
        target_node_id,
        axis_label,
        stack_lines,
        line_mode,
        max_slots,
        input_mode_input,
        input_mode_value,
        lora_count_input,
        lora_name_prefix,
        simple_weight_prefix,
        model_strength_prefix,
        clip_strength_prefix,
        clear_unused_slots,
        target_ref="",
    ):
        target_node_id = _resolve_target_node_id(target_node_id, target_ref)
        items = []
        parsed_lines = []
        max_slots = int(max_slots)
        for line in str(stack_lines or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            label, entries = self._parse_stack_line(line, line_mode)
            if not entries:
                continue
            if len(entries) > max_slots:
                raise ValueError(f"LoRA stack '{label}' has {len(entries)} entries, but max_slots is {max_slots}")
            assignments = [
                _assignment(target_node_id, input_mode_input, input_mode_value),
                _assignment(target_node_id, lora_count_input, len(entries)),
            ]
            for index, (lora_name, model_strength, clip_strength) in enumerate(entries, start=1):
                assignments.append(_assignment(target_node_id, f"{lora_name_prefix}{index}", lora_name))
                if line_mode == "simple_weight":
                    assignments.append(_assignment(target_node_id, f"{simple_weight_prefix}{index}", float(model_strength)))
                assignments.append(_assignment(target_node_id, f"{model_strength_prefix}{index}", float(model_strength)))
                assignments.append(_assignment(target_node_id, f"{clip_strength_prefix}{index}", float(clip_strength)))
            if clear_unused_slots:
                for index in range(len(entries) + 1, max_slots + 1):
                    assignments.append(_assignment(target_node_id, f"{lora_name_prefix}{index}", "None"))
                    if line_mode == "simple_weight":
                        assignments.append(_assignment(target_node_id, f"{simple_weight_prefix}{index}", 1.0))
                    assignments.append(_assignment(target_node_id, f"{model_strength_prefix}{index}", 1.0))
                    assignments.append(_assignment(target_node_id, f"{clip_strength_prefix}{index}", 1.0))
            items.append({"label": label, "safe_label": _safe_label(label), "assignments": assignments})
            parsed_lines.append(
                label + ": " + ", ".join(f"{Path(name).stem}({model:g},{clip:g})" for name, model, clip in entries)
            )
        if not items:
            raise ValueError("No LoRA stack presets were parsed")
        return (_axis_json(axis_label or "LoRA Stack", items), "\n".join(parsed_lines))


class StudioSuiteXYAxisLoraLoaderChain:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("x_or_y_axis_json", "parsed_chains")
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis for several serial LoRA Loader nodes. Each preset assigns lora_name/strength_model/strength_clip per loader."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target_node_ids": ("STRING", {"default": "", "multiline": False}),
                "axis_label": ("STRING", {"default": "LoRA Chain", "multiline": False}),
                "chain_lines": (
                    "STRING",
                    {
                        "default": "lora_a|example_a.safetensors|1.0|1.0\ncombo_ab|example_a.safetensors|0.8|0.8|example_b.safetensors|0.6|0.6",
                        "multiline": True,
                        "tooltip": "Line format: label|lora1|model1|clip1|lora2|model2|clip2. target_node_ids order decides loader slots.",
                    },
                ),
                "lora_name_input": ("STRING", {"default": "lora_name", "multiline": False}),
                "strength_model_input": ("STRING", {"default": "strength_model", "multiline": False}),
                "strength_clip_input": ("STRING", {"default": "strength_clip", "multiline": False}),
                "missing_slot_mode": (["disable_strength", "keep_existing", "set_none"], {"default": "disable_strength"}),
            }
        }

    def build_axis(
        self,
        target_node_ids,
        axis_label,
        chain_lines,
        lora_name_input,
        strength_model_input,
        strength_clip_input,
        missing_slot_mode,
    ):
        node_ids = [item.strip() for item in str(target_node_ids or "").replace("\n", ",").split(",") if item.strip()]
        if not node_ids:
            raise ValueError("target_node_ids is required")
        items = []
        parsed_lines = []
        for line in str(chain_lines or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 4 or (len(parts) - 1) % 3 != 0:
                raise ValueError(f"LoRA chain line must be label|lora|model|clip...: {line}")
            label = parts[0]
            values = parts[1:]
            entries = []
            for index in range(0, len(values), 3):
                entries.append((values[index], float(_coerce_value(values[index + 1])), float(_coerce_value(values[index + 2]))))
            if len(entries) > len(node_ids):
                raise ValueError(f"LoRA chain '{label}' needs {len(entries)} nodes, but only {len(node_ids)} target ids were provided")
            assignments = []
            for index, node_id in enumerate(node_ids):
                if index < len(entries):
                    lora_name, model_strength, clip_strength = entries[index]
                    assignments.append(_assignment(node_id, lora_name_input, lora_name))
                    assignments.append(_assignment(node_id, strength_model_input, model_strength))
                    assignments.append(_assignment(node_id, strength_clip_input, clip_strength))
                elif missing_slot_mode == "disable_strength":
                    assignments.append(_assignment(node_id, strength_model_input, 0.0))
                    assignments.append(_assignment(node_id, strength_clip_input, 0.0))
                elif missing_slot_mode == "set_none":
                    assignments.append(_assignment(node_id, lora_name_input, "None"))
                    assignments.append(_assignment(node_id, strength_model_input, 0.0))
                    assignments.append(_assignment(node_id, strength_clip_input, 0.0))
            items.append({"label": label, "safe_label": _safe_label(label), "assignments": assignments})
            parsed_lines.append(label + ": " + ", ".join(f"{Path(name).stem}({model:g},{clip:g})" for name, model, clip in entries))
        if not items:
            raise ValueError("No LoRA chain presets were parsed")
        return (_axis_json(axis_label or "LoRA Chain", items), "\n".join(parsed_lines))


class StudioSuiteXYAxisLoraBlockWeight:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("x_or_y_axis_json", "parsed_block_weights")
    FUNCTION = "build_axis"
    DESCRIPTION = "Build an axis for Inspire LoRA Block Weight nodes by assigning block_vector and optional strength/A/B/preset fields."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target_node_id": ("STRING", {"default": "", "multiline": False}),
                "axis_label": ("STRING", {"default": "LoRA Block Weight", "multiline": False}),
                "block_weight_lines": (
                    "STRING",
                    {
                        "default": "all|1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1|1.0|1.0\nearly_off|0,0,0,0,0,0,0,0,1,1,1,1,1,1,1,1,1|1.0|1.0\nlate_off|1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0|1.0|1.0",
                        "multiline": True,
                        "tooltip": "Line format: label|block_vector|model_strength|clip_strength|A|B|preset. Fields after block_vector are optional.",
                    },
                ),
                "block_vector_input": ("STRING", {"default": "block_vector", "multiline": False}),
                "strength_model_input": ("STRING", {"default": "strength_model", "multiline": False}),
                "strength_clip_input": ("STRING", {"default": "strength_clip", "multiline": False}),
                "a_input": ("STRING", {"default": "A", "multiline": False}),
                "b_input": ("STRING", {"default": "B", "multiline": False}),
                "preset_input": ("STRING", {"default": "preset", "multiline": False}),
                "set_preset_to_custom": ("BOOLEAN", {"default": False}),
            },
        }

    def build_axis(
        self,
        target_node_id,
        axis_label,
        block_weight_lines,
        block_vector_input,
        strength_model_input,
        strength_clip_input,
        a_input,
        b_input,
        preset_input,
        set_preset_to_custom,
        target_ref="",
    ):
        target_node_id = _resolve_target_node_id(target_node_id, target_ref)
        items = []
        parsed_lines = []
        for line in str(block_weight_lines or "").splitlines():
            line = _strip_comment(line)
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 2:
                raise ValueError(f"LoRA block weight line must be label|block_vector: {line}")
            label = parts[0]
            block_vector = parts[1]
            assignments = [_assignment(target_node_id, block_vector_input, block_vector)]
            if len(parts) >= 3 and parts[2]:
                assignments.append(_assignment(target_node_id, strength_model_input, float(_coerce_value(parts[2]))))
            if len(parts) >= 4 and parts[3]:
                assignments.append(_assignment(target_node_id, strength_clip_input, float(_coerce_value(parts[3]))))
            if len(parts) >= 5 and parts[4]:
                assignments.append(_assignment(target_node_id, a_input, float(_coerce_value(parts[4]))))
            if len(parts) >= 6 and parts[5]:
                assignments.append(_assignment(target_node_id, b_input, float(_coerce_value(parts[5]))))
            if len(parts) >= 7 and parts[6]:
                assignments.append(_assignment(target_node_id, preset_input, parts[6]))
            elif set_preset_to_custom:
                assignments.append(_assignment(target_node_id, preset_input, "Preset"))
            items.append({"label": label, "safe_label": _safe_label(label), "assignments": assignments})
            parsed_lines.append(f"{label}: {block_vector}")
        if not items:
            raise ValueError("No LoRA block weight presets were parsed")
        return (_axis_json(axis_label or "LoRA Block Weight", items), "\n".join(parsed_lines))


class StudioSuiteXYMatrix:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("matrix_json", "summary")
    FUNCTION = "build_matrix"
    DESCRIPTION = "Combine X/Y axes into a cell matrix. Leave y_axis_json empty for a one-dimensional sweep."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "x_axis_json": ("STRING", {"default": "", "multiline": True}),
                "y_axis_json": ("STRING", {"default": "", "multiline": True}),
                "matrix_title": ("STRING", {"default": "XY Test", "multiline": False}),
            }
        }

    def build_matrix(self, x_axis_json, y_axis_json, matrix_title):
        x_axis = _parse_axis_json(x_axis_json)
        y_axis = _parse_axis_json(y_axis_json, allow_empty=True)
        cells = []
        for row, y_item in enumerate(y_axis["items"]):
            for col, x_item in enumerate(x_axis["items"]):
                x_label = x_item.get("label", f"x{col + 1}")
                y_label = y_item.get("label", f"y{row + 1}")
                label = x_label if not y_label else f"{x_label} / {y_label}"
                cells.append(
                    {
                        "row": row,
                        "col": col,
                        "x_label": x_label,
                        "y_label": y_label,
                        "label": label,
                        "assignments": list(y_item.get("assignments", [])) + list(x_item.get("assignments", [])),
                    }
                )

        matrix = {
            "version": XY_VERSION,
            "title": str(matrix_title or "XY Test"),
            "x_axis_label": x_axis.get("axis_label", "X"),
            "y_axis_label": y_axis.get("axis_label", "Y"),
            "columns": len(x_axis["items"]),
            "rows": len(y_axis["items"]),
            "cells": cells,
        }
        summary = f"{matrix['title']}: {matrix['columns']} x {matrix['rows']} = {len(cells)} cells"
        return (json.dumps(matrix, ensure_ascii=False), summary)


class StudioSuiteXYQueue:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("summary", "queued_jobs", "manifest_path")
    FUNCTION = "queue_matrix"
    OUTPUT_NODE = True
    DESCRIPTION = "Submit one independent child prompt per XY cell, then automatically build the contact sheet after the final child finishes."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True}),
                "matrix_json": ("STRING", {"default": "", "multiline": True}),
                "writer_node_id": ("STRING", {"default": "", "multiline": False}),
                "output_path": ("STRING", {"default": DEFAULT_XY_OUTPUT_SUBDIR, "multiline": False}),
                "filename_prefix_template": (
                    "STRING",
                    {
                        "default": "xy_%run_id%_r%row%_c%col%_%x_label%_%y_label%",
                        "multiline": False,
                    },
                ),
                "queue_front": ("BOOLEAN", {"default": False}),
                "dry_run": ("BOOLEAN", {"default": False}),
                "overwrite_existing": ("BOOLEAN", {"default": True}),
                "cleanup_after_save": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "auto_build_grid": ("BOOLEAN", {"default": True}),
                "grid_image_ext": (["png", "jpg", "jpeg", "webp", "bmp", "tiff"], {"default": "png"}),
                "grid_cell_width": ("INT", {"default": 320, "min": 64, "max": 2048}),
                "grid_cell_height": ("INT", {"default": 448, "min": 64, "max": 2048}),
                "grid_label_height": ("INT", {"default": 42, "min": 0, "max": 256}),
                "grid_gap": ("INT", {"default": 8, "min": 0, "max": 64}),
                "grid_filename": ("STRING", {"default": "xy_grid_%run_id%.png", "multiline": False}),
                "grid_background": ("STRING", {"default": "#202024", "multiline": False}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            },
        }

    def queue_matrix(
        self,
        enabled,
        matrix_json,
        writer_node_id,
        output_path,
        filename_prefix_template,
        queue_front,
        dry_run,
        overwrite_existing,
        cleanup_after_save,
        auto_build_grid=True,
        grid_image_ext="png",
        grid_cell_width=320,
        grid_cell_height=448,
        grid_label_height=42,
        grid_gap=8,
        grid_filename="xy_grid_%run_id%.png",
        grid_background="#202024",
        prompt=None,
        extra_pnginfo=None,
        unique_id=None,
    ):
        if not enabled:
            return ("XY queue disabled", 0, "")
        if not prompt:
            raise ValueError("Current prompt data is missing")
        matrix = json.loads(matrix_json)
        cells = matrix.get("cells") or []
        if not cells:
            raise ValueError("XY matrix has no cells")

        writer_id = str(writer_node_id or "").strip()
        if not writer_id:
            matched_writers = [
                node_id
                for node_id, node in prompt.items()
                if node.get("class_type") == "IndependentResultWriterProxy"
            ]
            if len(matched_writers) != 1:
                raise ValueError(
                    f"Leave writer_node_id empty only when exactly one Independent Result Writer (Proxy) exists; found {len(matched_writers)}"
                )
            writer_id = str(matched_writers[0])
        if writer_id not in prompt:
            raise ValueError(f"Writer node id not found in prompt: {writer_id}")

        output_dir = _resolve_output_dir(output_path)
        run_id = time.strftime("%Y%m%d_%H%M%S")
        manifest = {
            "version": XY_VERSION,
            "run_id": run_id,
            "title": matrix.get("title", "XY Test"),
            "output_dir": str(output_dir),
            "x_axis_label": matrix.get("x_axis_label", "X"),
            "y_axis_label": matrix.get("y_axis_label", "Y"),
            "columns": matrix.get("columns", 1),
            "rows": matrix.get("rows", 1),
            "cells": [],
        }

        server = None if dry_run else PromptServer.instance
        client_id = getattr(server, "client_id", None)
        scheduler_id = str(unique_id)
        queued = 0
        jobs = []
        _send_status(unique_id, f"Preparing {len(cells)} XY child prompts")

        for cell in cells:
            prefix = _render_template(filename_prefix_template, cell, run_id)
            manifest_cell = dict(cell)
            manifest_cell["filename_prefix"] = prefix
            manifest_cell["expected_image_prefix"] = prefix
            manifest["cells"].append(manifest_cell)

            child_prompt = copy.deepcopy(prompt)
            _apply_assignments(child_prompt, cell.get("assignments") or [])

            child_prompt[writer_id]["class_type"] = "IndependentResultWriter"
            writer_inputs = child_prompt[writer_id].setdefault("inputs", {})
            writer_inputs["output_path"] = str(output_dir)
            writer_inputs["filename_prefix"] = prefix
            writer_inputs["filename_number_start"] = 1
            writer_inputs["overwrite_existing"] = bool(overwrite_existing)
            writer_inputs["naming_mode"] = "exact_name_overwrite" if overwrite_existing else "exact_name"
            writer_inputs["cleanup_after_save"] = bool(cleanup_after_save)
            writer_inputs.setdefault("source_image_stem", prefix)
            writer_inputs.setdefault("source_text_stem", prefix)

            if scheduler_id in child_prompt and "inputs" in child_prompt[scheduler_id]:
                child_prompt[scheduler_id]["inputs"]["enabled"] = False

            extra_data = {"extra_pnginfo": extra_pnginfo or {}}
            if client_id is not None:
                extra_data["client_id"] = client_id
            extra_data["create_time"] = int(time.time() * 1000)

            # XY child prompts have one intentional sink. Running every output node
            # would repeat unrelated preview, text, or analysis branches per cell.
            outputs_to_execute = [writer_id]
            jobs.append((child_prompt, extra_data, outputs_to_execute))
            queued += 1

        manifest_path = output_dir / f"xy_manifest_{run_id}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        if bool(auto_build_grid) and not dry_run:
            final_grid_name = str(grid_filename or "xy_grid_%run_id%.png").replace("%run_id%", run_id)
            finalizer_prompt = {
                "1": {
                    "class_type": "StudioSuiteXYGridFinalizer",
                    "inputs": {
                        "manifest_path": str(manifest_path),
                        "image_ext": grid_image_ext,
                        "cell_width": int(grid_cell_width),
                        "cell_height": int(grid_cell_height),
                        "label_height": int(grid_label_height),
                        "gap": int(grid_gap),
                        "grid_filename": final_grid_name,
                        "background": str(grid_background),
                    },
                }
            }
            finalizer_extra = {"extra_pnginfo": extra_pnginfo or {}, "create_time": int(time.time() * 1000)}
            if client_id is not None:
                finalizer_extra["client_id"] = client_id
            jobs.append((finalizer_prompt, finalizer_extra, ["1"]))

        if not dry_run:
            ordered_jobs = list(reversed(jobs)) if queue_front else jobs
            for child_prompt, extra_data, outputs_to_execute in ordered_jobs:
                prompt_id = str(uuid.uuid4())
                number = server.number
                if queue_front:
                    number = -number
                server.number += 1
                server.prompt_queue.put((number, prompt_id, child_prompt, extra_data, outputs_to_execute, {}))

        grid_note = " Auto grid queued." if bool(auto_build_grid) and not dry_run else ""
        summary = f"Queued {queued} / {len(cells)} XY prompts. Manifest: {manifest_path}.{grid_note}"
        _send_status(unique_id, summary)
        return (summary, queued, str(manifest_path))


class StudioSuiteXYGridBuilder:
    CATEGORY = "Studio Suite/XY"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("grid_image", "status")
    FUNCTION = "build_grid"
    DESCRIPTION = "Build a contact-sheet image from the latest XY manifest after child prompts have finished."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "manifest_path": ("STRING", {"default": "", "multiline": False}),
                "output_path": ("STRING", {"default": DEFAULT_XY_OUTPUT_SUBDIR, "multiline": False}),
                "image_ext": (["png", "jpg", "jpeg", "webp", "bmp", "tiff"], {"default": "png"}),
                "cell_width": ("INT", {"default": 320, "min": 64, "max": 2048}),
                "cell_height": ("INT", {"default": 448, "min": 64, "max": 2048}),
                "label_height": ("INT", {"default": 42, "min": 0, "max": 256}),
                "gap": ("INT", {"default": 8, "min": 0, "max": 64}),
                "grid_filename": ("STRING", {"default": "xy_grid.png", "multiline": False}),
                "background": ("STRING", {"default": "#202024", "multiline": False}),
            }
        }

    def build_grid(
        self,
        manifest_path,
        output_path,
        image_ext,
        cell_width,
        cell_height,
        label_height,
        gap,
        grid_filename,
        background,
    ):
        manifest = _read_manifest(manifest_path, output_path)
        output_dir = Path(manifest.get("output_dir") or _resolve_output_dir(output_path))
        columns = max(1, int(manifest.get("columns") or 1))
        rows = max(1, int(manifest.get("rows") or 1))
        cells = manifest.get("cells") or []

        def parse_color(text):
            value = str(text or "#202024").strip()
            if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))
            return (32, 32, 36)

        bg = parse_color(background)
        width = columns * int(cell_width) + (columns + 1) * int(gap)
        height = rows * (int(cell_height) + int(label_height)) + (rows + 1) * int(gap)
        grid = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(grid)
        font = ImageFont.load_default()
        missing = []

        for cell in cells:
            row = int(cell.get("row") or 0)
            col = int(cell.get("col") or 0)
            x = int(gap) + col * (int(cell_width) + int(gap))
            y = int(gap) + row * (int(cell_height) + int(label_height) + int(gap))
            image_path = _find_cell_image(output_dir, cell.get("expected_image_prefix") or cell.get("filename_prefix"), image_ext)
            if image_path is None:
                missing.append(str(cell.get("label") or cell.get("filename_prefix")))
                draw.rectangle([x, y, x + int(cell_width), y + int(cell_height)], fill=(48, 48, 52), outline=(96, 96, 104))
            else:
                image = Image.open(image_path).convert("RGB")
                image.thumbnail((int(cell_width), int(cell_height)), Image.Resampling.LANCZOS)
                paste_x = x + (int(cell_width) - image.width) // 2
                paste_y = y + (int(cell_height) - image.height) // 2
                grid.paste(image, (paste_x, paste_y))

            label = str(cell.get("label") or "").strip()
            if int(label_height) > 0 and label:
                draw.text((x + 4, y + int(cell_height) + 4), label[:120], fill=(235, 235, 235), font=font)

        grid_path = output_dir / (str(grid_filename or "xy_grid.png").strip() or "xy_grid.png")
        grid.save(grid_path)
        status = f"XY grid saved: {grid_path}"
        if missing:
            status += f" | missing {len(missing)} cells: {', '.join(missing[:10])}"
        return (_pil_to_tensor(grid), status)


class StudioSuiteXYGridFinalizer:
    CATEGORY = "Studio Suite/XY/Internal"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "finalize"
    OUTPUT_NODE = True
    DESCRIPTION = "Internal queue finalizer. Studio Suite XY Queue appends it after all cell jobs."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "manifest_path": ("STRING", {"default": "", "multiline": False}),
                "image_ext": (["png", "jpg", "jpeg", "webp", "bmp", "tiff"], {"default": "png"}),
                "cell_width": ("INT", {"default": 320, "min": 64, "max": 2048}),
                "cell_height": ("INT", {"default": 448, "min": 64, "max": 2048}),
                "label_height": ("INT", {"default": 42, "min": 0, "max": 256}),
                "gap": ("INT", {"default": 8, "min": 0, "max": 64}),
                "grid_filename": ("STRING", {"default": "xy_grid.png", "multiline": False}),
                "background": ("STRING", {"default": "#202024", "multiline": False}),
            }
        }

    def finalize(
        self,
        manifest_path,
        image_ext,
        cell_width,
        cell_height,
        label_height,
        gap,
        grid_filename,
        background,
    ):
        _, status = StudioSuiteXYGridBuilder().build_grid(
            manifest_path,
            "",
            image_ext,
            cell_width,
            cell_height,
            label_height,
            gap,
            grid_filename,
            background,
        )
        print(f"[StudioSuiteXYGridFinalizer] {status}", flush=True)
        return (status,)


NODE_CLASS_MAPPINGS = {
    "StudioSuiteXYParameterInput": StudioSuiteXYParameterInput,
    "StudioSuiteXYAxisParameter": StudioSuiteXYAxisParameter,
    "StudioSuiteXYTargetModelClipBridge": StudioSuiteXYTargetModelClipBridge,
    "StudioSuiteXYTargetModelBridge": StudioSuiteXYTargetModelBridge,
    "StudioSuiteXYAxisGeneric": StudioSuiteXYAxisGeneric,
    "StudioSuiteXYAxisSamplerScheduler": StudioSuiteXYAxisSamplerScheduler,
    "StudioSuiteXYAxisFreeU": StudioSuiteXYAxisFreeU,
    "StudioSuiteXYAxisLoraStrength": StudioSuiteXYAxisLoraStrength,
    "StudioSuiteXYAxisLoraFile": StudioSuiteXYAxisLoraFile,
    "StudioSuiteXYAxisLoraCompare": StudioSuiteXYAxisLoraCompare,
    "StudioSuiteXYAxisLoraStacker": StudioSuiteXYAxisLoraStacker,
    "StudioSuiteXYAxisLoraLoaderChain": StudioSuiteXYAxisLoraLoaderChain,
    "StudioSuiteXYAxisLoraBlockWeight": StudioSuiteXYAxisLoraBlockWeight,
    "StudioSuiteXYMatrix": StudioSuiteXYMatrix,
    "StudioSuiteXYQueue": StudioSuiteXYQueue,
    "StudioSuiteXYGridBuilder": StudioSuiteXYGridBuilder,
    "StudioSuiteXYGridFinalizer": StudioSuiteXYGridFinalizer,
}


NODE_DISPLAY_NAME_MAPPINGS = {
    "StudioSuiteXYParameterInput": "Studio Suite XY Parameter Input",
    "StudioSuiteXYAxisParameter": "Studio Suite XY Axis - Parameter Slot",
    "StudioSuiteXYTargetModelClipBridge": "Studio Suite XY Target Bridge - Model/Clip",
    "StudioSuiteXYTargetModelBridge": "Studio Suite XY Target Bridge - Model",
    "StudioSuiteXYAxisGeneric": "Studio Suite XY Axis - Generic",
    "StudioSuiteXYAxisSamplerScheduler": "Studio Suite XY Axis - Sampler/Scheduler",
    "StudioSuiteXYAxisFreeU": "Studio Suite XY Axis - FreeU",
    "StudioSuiteXYAxisLoraStrength": "Studio Suite XY Axis - LoRA Strength",
    "StudioSuiteXYAxisLoraFile": "Studio Suite XY Axis - LoRA File",
    "StudioSuiteXYAxisLoraCompare": "Studio Suite XY Axis - LoRA Compare",
    "StudioSuiteXYAxisLoraStacker": "Studio Suite XY Axis - LoRA Stacker",
    "StudioSuiteXYAxisLoraLoaderChain": "Studio Suite XY Axis - LoRA Loader Chain",
    "StudioSuiteXYAxisLoraBlockWeight": "Studio Suite XY Axis - LoRA Block Weight",
    "StudioSuiteXYMatrix": "Studio Suite XY Matrix",
    "StudioSuiteXYQueue": "Studio Suite XY Queue",
    "StudioSuiteXYGridBuilder": "Studio Suite XY Grid Builder",
    "StudioSuiteXYGridFinalizer": "Studio Suite XY Grid Finalizer (Internal)",
}
