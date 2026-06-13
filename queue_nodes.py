import copy
import json
import os
import time
import uuid
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

import folder_paths
import nodes as comfy_nodes
from server import PromptServer


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXTENSIONS = {".txt"}
DEFAULT_OUTPUT_SUBDIR = "independent_queue"
QUEUE_NODE_CLASS = "IndependentPromptFolderQueue"
_PROMPT_HOOK_REGISTERED = False


def _send_status(unique_id, text):
    try:
        PromptServer.instance.send_progress_text(text, unique_id)
    except Exception:
        pass


def _normalize_queue_prompt_inputs(inputs):
    known_text_pair_modes = {
        "none",
        "same_name_same_folder",
        "same_name_txt_folder",
        "sorted_txt_same_folder",
        "sorted_txt_folder",
    }
    known_sort_modes = {"name_asc", "name_desc", "mtime_asc", "mtime_desc"}
    known_missing_behaviors = {"empty", "error"}

    normalized = dict(inputs or {})

    text_pair_mode = normalized.get("text_pair_mode", "none")
    text_folder = normalized.get("text_folder", "")
    text_sort_mode = normalized.get("text_sort_mode", "name_asc")
    missing_text_behavior = normalized.get("missing_text_behavior", "empty")
    queue_front = normalized.get("queue_front", False)
    dry_run = normalized.get("dry_run", False)

    legacy_layout_detected = isinstance(text_pair_mode, bool) and isinstance(text_folder, bool)
    if legacy_layout_detected:
        queue_front = bool(text_pair_mode)
        dry_run = bool(text_folder)
        text_pair_mode = "none"
        text_folder = ""
        text_sort_mode = "name_asc"
        missing_text_behavior = "empty"

    if text_pair_mode not in known_text_pair_modes:
        text_pair_mode = "none"
    if text_sort_mode not in known_sort_modes:
        text_sort_mode = "name_asc"
    if missing_text_behavior not in known_missing_behaviors:
        missing_text_behavior = "empty"
    if not isinstance(text_folder, str):
        text_folder = ""

    normalized["text_pair_mode"] = text_pair_mode
    normalized["text_folder"] = text_folder
    normalized["text_sort_mode"] = text_sort_mode
    normalized["missing_text_behavior"] = missing_text_behavior
    normalized["queue_front"] = bool(queue_front)
    normalized["dry_run"] = bool(dry_run)
    return normalized


def _inject_prompt_fixes(json_data):
    if not isinstance(json_data, dict):
        return json_data

    prompt = json_data.get("prompt")
    if not isinstance(prompt, dict):
        return json_data

    patched = dict(json_data)
    patched_prompt = dict(prompt)
    queue_targets = []

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != QUEUE_NODE_CLASS:
            continue

        patched_node = dict(node)
        patched_inputs = _normalize_queue_prompt_inputs(node.get("inputs") or {})
        patched_node["inputs"] = patched_inputs
        patched_prompt[node_id] = patched_node

        if patched_inputs.get("enabled", True) is not False:
            queue_targets.append(str(node_id))

    patched["prompt"] = patched_prompt

    if queue_targets and not patched.get("partial_execution_targets"):
        patched["partial_execution_targets"] = queue_targets

    return patched


def _register_prompt_hook():
    global _PROMPT_HOOK_REGISTERED
    if _PROMPT_HOOK_REGISTERED:
        return

    server = getattr(PromptServer, "instance", None)
    if server is None:
        return

    server.add_on_prompt_handler(_inject_prompt_fixes)
    _PROMPT_HOOK_REGISTERED = True


def _sorted_image_files(folder_path, sort_mode):
    base = Path(folder_path)
    if not base.is_dir():
        raise ValueError(f"Image folder not found: {folder_path}")

    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    if sort_mode == "name_desc":
        files.sort(key=lambda p: p.name.lower(), reverse=True)
    elif sort_mode == "mtime_asc":
        files.sort(key=lambda p: p.stat().st_mtime)
    elif sort_mode == "mtime_desc":
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        files.sort(key=lambda p: p.name.lower())
    return files


def _sorted_text_files(folder_path, sort_mode):
    base = Path(folder_path)
    if not base.is_dir():
        raise ValueError(f"Text folder not found: {folder_path}")

    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in TEXT_EXTENSIONS]
    if sort_mode == "name_desc":
        files.sort(key=lambda p: p.name.lower(), reverse=True)
    elif sort_mode == "mtime_asc":
        files.sort(key=lambda p: p.stat().st_mtime)
    elif sort_mode == "mtime_desc":
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        files.sort(key=lambda p: p.name.lower())
    return files


def _read_text_file(text_path):
    if not text_path:
        return ""

    path = Path(text_path)
    if not path.is_file():
        raise ValueError(f"Text file not found: {text_path}")

    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    return path.read_text(encoding="utf-8", errors="replace")


def _resolve_text_pairs(image_files, text_pair_mode, text_folder, text_sort_mode, missing_text_behavior):
    if text_pair_mode == "none":
        return {str(path): None for path in image_files}

    resolved = {}

    if text_pair_mode == "same_name_same_folder":
        for image_path in image_files:
            candidate = image_path.with_suffix(".txt")
            if candidate.is_file():
                resolved[str(image_path)] = candidate
            elif missing_text_behavior == "error":
                raise ValueError(f"Missing paired txt for image: {image_path}")
            else:
                resolved[str(image_path)] = None
        return resolved

    if text_pair_mode == "same_name_txt_folder":
        if not text_folder:
            raise ValueError("text_folder is required for same_name_txt_folder mode")
        base = Path(text_folder)
        if not base.is_dir():
            raise ValueError(f"Text folder not found: {text_folder}")
        for image_path in image_files:
            candidate = base / f"{image_path.stem}.txt"
            if candidate.is_file():
                resolved[str(image_path)] = candidate
            elif missing_text_behavior == "error":
                raise ValueError(f"Missing paired txt for image: {image_path}")
            else:
                resolved[str(image_path)] = None
        return resolved

    if text_pair_mode in ("sorted_txt_same_folder", "sorted_txt_folder"):
        if text_pair_mode == "sorted_txt_folder" and not text_folder:
            raise ValueError("text_folder is required for sorted_txt_folder mode")
        base = image_files[0].parent if text_pair_mode == "sorted_txt_same_folder" else Path(text_folder)
        text_files = _sorted_text_files(str(base), text_sort_mode)
        if missing_text_behavior == "error" and len(text_files) < len(image_files):
            raise ValueError(
                f"Not enough txt files for sorted pairing: images={len(image_files)}, txt={len(text_files)}"
            )

        for index, image_path in enumerate(image_files):
            resolved[str(image_path)] = text_files[index] if index < len(text_files) else None
        return resolved

    raise ValueError(f"Unsupported text_pair_mode: {text_pair_mode}")


def _normalize_queue_inputs(text_pair_mode, text_folder, text_sort_mode, missing_text_behavior, queue_front, dry_run):
    known_text_pair_modes = {
        "none",
        "same_name_same_folder",
        "same_name_txt_folder",
        "sorted_txt_same_folder",
        "sorted_txt_folder",
    }
    known_sort_modes = {"name_asc", "name_desc", "mtime_asc", "mtime_desc"}
    known_missing_behaviors = {"empty", "error"}

    legacy_layout_detected = isinstance(text_pair_mode, bool) and isinstance(text_folder, bool)
    if legacy_layout_detected:
        queue_front = bool(text_pair_mode)
        dry_run = bool(text_folder)
        text_pair_mode = "none"
        text_folder = ""
        text_sort_mode = "name_asc"
        missing_text_behavior = "empty"

    if text_pair_mode not in known_text_pair_modes:
        raise ValueError(f"Unsupported text_pair_mode: {text_pair_mode}")
    if text_sort_mode not in known_sort_modes:
        text_sort_mode = "name_asc"
    if missing_text_behavior not in known_missing_behaviors:
        missing_text_behavior = "empty"

    if not isinstance(text_folder, str):
        text_folder = ""

    queue_front = bool(queue_front)
    dry_run = bool(dry_run)

    return text_pair_mode, text_folder, text_sort_mode, missing_text_behavior, queue_front, dry_run


def _normalize_output_dir(output_path, source_stem, source_text_stem, image_width, image_height):
    resolved = _apply_template(
        output_path or DEFAULT_OUTPUT_SUBDIR,
        source_stem=source_stem,
        source_text_stem=source_text_stem,
        image_width=image_width,
        image_height=image_height,
        batch_number=0,
    ).strip()

    if not resolved:
        resolved = DEFAULT_OUTPUT_SUBDIR

    path = Path(resolved)
    if not path.is_absolute():
        path = Path(folder_paths.get_output_directory()) / path

    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _apply_template(template, source_stem, source_text_stem, image_width, image_height, batch_number):
    if template is None:
        template = ""

    text = str(template)
    now = time.localtime()
    replacements = {
        "%source_stem%": source_stem or "",
        "%text_stem%": source_text_stem or "",
        "%source_text_stem%": source_text_stem or "",
        "%width%": str(image_width),
        "%height%": str(image_height),
        "%year%": str(now.tm_year),
        "%month%": str(now.tm_mon).zfill(2),
        "%day%": str(now.tm_mday).zfill(2),
        "%hour%": str(now.tm_hour).zfill(2),
        "%minute%": str(now.tm_min).zfill(2),
        "%second%": str(now.tm_sec).zfill(2),
        "%batch_num%": str(batch_number),
    }

    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def _get_extension_and_format(image_format):
    ext_map = {
        "png": (".png", "PNG"),
        "jpeg": (".jpg", "JPEG"),
        "webp": (".webp", "WEBP"),
        "tiff": (".tiff", "TIFF"),
        "bmp": (".bmp", "BMP"),
    }
    return ext_map[image_format]


def _resolve_start_counter(output_dir, base_prefix, extension, number_start, number_padding, delimiter, overwrite_existing):
    if overwrite_existing:
        return int(number_start)

    counter = int(number_start)
    while True:
        candidate = output_dir / f"{base_prefix}{delimiter}{counter:0{number_padding}d}{extension}"
        if not candidate.exists():
            return counter
        counter += 1


def _build_png_metadata(prompt, extra_pnginfo, workflow_metadata_mode):
    if workflow_metadata_mode not in ("embed_png", "embed_png_and_json"):
        return None

    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt, ensure_ascii=False))
    if extra_pnginfo is not None:
        for key, value in extra_pnginfo.items():
            metadata.add_text(key, json.dumps(value, ensure_ascii=False))
    return metadata


def _build_sidecar_payload(
    prompt_text,
    prompt,
    extra_pnginfo,
    source_image_path,
    source_image_stem,
    source_text_path,
    source_text_stem,
):
    return {
        "prompt_text": prompt_text or "",
        "prompt": prompt,
        "extra_pnginfo": extra_pnginfo,
        "source_image_path": source_image_path or "",
        "source_image_stem": source_image_stem or "",
        "source_text_path": source_text_path or "",
        "source_text_stem": source_text_stem or "",
        "saved_at": int(time.time()),
    }


def _normalize_writer_inputs(
    filename_delimiter,
    filename_number_padding,
    filename_number_start,
    overwrite_existing,
    prompt_text,
    save_prompt_txt,
    workflow_metadata_mode,
    image_format,
    jpeg_quality,
    png_compress,
    lossless_webp,
    naming_mode="counter_suffix",
):
    known_formats = {"png", "jpeg", "webp", "tiff", "bmp"}

    if isinstance(filename_number_padding, str) and filename_number_padding in known_formats:
        legacy_prompt_text = filename_delimiter if isinstance(filename_delimiter, str) else ""
        legacy_image_format = filename_number_padding
        legacy_jpeg_quality = filename_number_start
        legacy_png_compress = overwrite_existing

        filename_delimiter = "_"
        filename_number_padding = 5
        filename_number_start = 1
        overwrite_existing = False
        prompt_text = legacy_prompt_text
        save_prompt_txt = True
        workflow_metadata_mode = "embed_png_and_json"
        image_format = legacy_image_format
        jpeg_quality = legacy_jpeg_quality
        png_compress = legacy_png_compress
        lossless_webp = False

    if not isinstance(filename_delimiter, str):
        filename_delimiter = "_"
    if filename_delimiter is None:
        filename_delimiter = "_"

    try:
        filename_number_padding = int(filename_number_padding)
    except Exception:
        filename_number_padding = 5
    filename_number_padding = min(max(filename_number_padding, 1), 10)

    try:
        filename_number_start = int(filename_number_start)
    except Exception:
        filename_number_start = 1
    filename_number_start = max(filename_number_start, 0)

    if isinstance(overwrite_existing, int) and overwrite_existing not in (0, 1):
        png_compress = overwrite_existing
        overwrite_existing = False
    else:
        overwrite_existing = bool(overwrite_existing)

    if image_format not in known_formats:
        image_format = "png"

    try:
        jpeg_quality = int(jpeg_quality)
    except Exception:
        jpeg_quality = 95
    jpeg_quality = min(max(jpeg_quality, 1), 100)

    try:
        png_compress = int(png_compress)
    except Exception:
        png_compress = 4
    png_compress = min(max(png_compress, 0), 9)

    if workflow_metadata_mode not in ("none", "embed_png", "save_json", "embed_png_and_json"):
        workflow_metadata_mode = "embed_png_and_json"

    if naming_mode not in ("counter_suffix", "exact_name", "exact_name_overwrite"):
        naming_mode = "counter_suffix"

    save_prompt_txt = bool(save_prompt_txt)
    lossless_webp = bool(lossless_webp)

    if prompt_text is None:
        prompt_text = ""
    prompt_text = str(prompt_text)

    return (
        filename_delimiter,
        filename_number_padding,
        filename_number_start,
        overwrite_existing,
        prompt_text,
        save_prompt_txt,
        workflow_metadata_mode,
        image_format,
        jpeg_quality,
        png_compress,
        lossless_webp,
        naming_mode,
    )


def _save_tensor_images(
    images,
    output_path,
    filename_prefix,
    filename_delimiter,
    filename_number_padding,
    filename_number_start,
    overwrite_existing,
    prompt_text,
    save_prompt_txt,
    workflow_metadata_mode,
    image_format,
    jpeg_quality,
    png_compress,
    lossless_webp,
    naming_mode,
    source_image_path,
    source_image_stem,
    source_text_path,
    source_text_stem,
    prompt,
    extra_pnginfo,
):
    if not prompt_text and source_text_path:
        prompt_text = _read_text_file(source_text_path)

    image_height = int(images[0].shape[0])
    image_width = int(images[0].shape[1])
    output_dir = _normalize_output_dir(
        output_path,
        source_image_stem,
        source_text_stem,
        image_width,
        image_height,
    )
    extension, pil_format = _get_extension_and_format(image_format)
    delimiter = filename_delimiter or ""
    base_prefix = _apply_template(
        filename_prefix or "independent",
        source_stem=source_image_stem,
        source_text_stem=source_text_stem,
        image_width=image_width,
        image_height=image_height,
        batch_number=0,
    ).strip()
    if not base_prefix:
        base_prefix = "independent"

    start_counter = None
    if naming_mode == "counter_suffix":
        start_counter = _resolve_start_counter(
            output_dir=output_dir,
            base_prefix=base_prefix,
            extension=extension,
            number_start=filename_number_start,
            number_padding=filename_number_padding,
            delimiter=delimiter,
            overwrite_existing=overwrite_existing,
        )

    saved_files = []
    ui_images = []
    sidecar_payload = _build_sidecar_payload(
        prompt_text=prompt_text,
        prompt=prompt,
        extra_pnginfo=extra_pnginfo,
        source_image_path=source_image_path,
        source_image_stem=source_image_stem,
        source_text_path=source_text_path,
        source_text_stem=source_text_stem,
    )

    output_root = Path(folder_paths.get_output_directory()).resolve()
    try:
        subfolder = str(output_dir.relative_to(output_root)).replace("\\", "/")
        image_type = "output"
    except ValueError:
        subfolder = ""
        image_type = "output"

    for batch_index, image in enumerate(images):
        image_np = np.clip(255.0 * image.cpu().numpy(), 0, 255).astype(np.uint8)
        pil_image = Image.fromarray(image_np)
        if pil_image.mode not in ("RGB", "RGBA"):
            pil_image = pil_image.convert("RGB")

        batch_prefix = _apply_template(
            base_prefix,
            source_stem=source_image_stem,
            source_text_stem=source_text_stem,
            image_width=pil_image.width,
            image_height=pil_image.height,
            batch_number=batch_index,
        )
        if naming_mode == "counter_suffix":
            counter = start_counter + batch_index
            file_stem = f"{batch_prefix}{delimiter}{counter:0{filename_number_padding}d}"
            image_path = output_dir / f"{file_stem}{extension}"
        else:
            base_stem = batch_prefix if batch_index == 0 else f"{batch_prefix}{delimiter}{batch_index}"
            image_path = output_dir / f"{base_stem}{extension}"
            if naming_mode == "exact_name":
                suffix_counter = 1
                while image_path.exists():
                    image_path = output_dir / f"{base_stem}{delimiter}{suffix_counter}{extension}"
                    suffix_counter += 1
            file_stem = image_path.stem

        save_kwargs = {}
        png_metadata = None
        if image_format == "png":
            save_kwargs["compress_level"] = int(png_compress)
            png_metadata = _build_png_metadata(prompt, extra_pnginfo, workflow_metadata_mode)
        elif image_format == "jpeg":
            if pil_image.mode == "RGBA":
                background = Image.new("RGB", pil_image.size, (255, 255, 255))
                background.paste(pil_image, mask=pil_image.getchannel("A"))
                pil_image = background
            save_kwargs["quality"] = int(jpeg_quality)
            save_kwargs["optimize"] = True
        elif image_format == "webp":
            save_kwargs["quality"] = int(jpeg_quality)
            save_kwargs["lossless"] = bool(lossless_webp)

        if png_metadata is not None:
            pil_image.save(str(image_path), format=pil_format, pnginfo=png_metadata, **save_kwargs)
        else:
            pil_image.save(str(image_path), format=pil_format, **save_kwargs)

        saved_files.append(str(image_path))
        ui_images.append(
            {
                "filename": image_path.name,
                "subfolder": subfolder,
                "type": image_type,
            }
        )

        if save_prompt_txt and prompt_text:
            txt_path = output_dir / f"{file_stem}.txt"
            txt_path.write_text(prompt_text, encoding="utf-8")

        if workflow_metadata_mode in ("save_json", "embed_png_and_json"):
            json_path = output_dir / f"{file_stem}.workflow.json"
            json_path.write_text(json.dumps(sidecar_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return saved_files, ui_images


class IndependentLoadImagePath:
    CATEGORY = "Independent Prompt"
    RETURN_TYPES = ("IMAGE", "MASK", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("image", "mask", "image_path", "image_stem", "paired_text", "text_path", "text_stem")
    FUNCTION = "load_image"
    DESCRIPTION = "Loads one image from an absolute path for the child prompt. It can also read a paired txt file and output the text so the child graph can use per-image prompt content."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Absolute path to the source image. This field is usually filled automatically by Independent Prompt Folder Queue when it submits each child prompt.",
                    },
                ),
            },
            "optional": {
                "text_path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional paired txt path. Usually filled automatically by Independent Prompt Folder Queue when text pairing is enabled.",
                    },
                ),
            }
        }

    def load_image(self, image_path, text_path=""):
        if not image_path:
            raise ValueError("image_path is empty")

        path = Path(image_path)
        if not path.is_file():
            raise ValueError(f"Image file not found: {image_path}")

        img = Image.open(path)
        img = ImageOps.exif_transpose(img)

        if "A" in img.getbands():
            alpha = np.array(img.getchannel("A")).astype(np.float32) / 255.0
            mask = 1.0 - torch.from_numpy(alpha)
        else:
            mask = torch.zeros((img.height, img.width), dtype=torch.float32)

        rgb = img.convert("RGB")
        image = np.array(rgb).astype(np.float32) / 255.0
        image_tensor = torch.from_numpy(image)[None,]
        paired_text = _read_text_file(text_path) if text_path else ""
        text_path_str = str(text_path or "")
        text_stem = Path(text_path).stem if text_path else ""

        return (image_tensor, mask, str(path), path.stem, paired_text, text_path_str, text_stem)


class IndependentResultWriterProxy:
    CATEGORY = "Independent Prompt"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "proxy"
    OUTPUT_NODE = False
    DESCRIPTION = "Lightweight save placeholder used in the parent workflow. The queue node rewrites this proxy into the real Independent Result Writer only inside each child prompt."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": (
                    "IMAGE",
                    {
                        "tooltip": "Generated images to save. Keep this connected to the end of the heavy child graph.",
                    },
                ),
                "output_path": (
                    "STRING",
                    {
                        "default": DEFAULT_OUTPUT_SUBDIR,
                        "multiline": False,
                        "tooltip": "Output folder. Relative paths go under ComfyUI/output. Absolute paths are also allowed. Supports placeholders like %year%, %month%, %day%, %source_stem%, %text_stem%, %width%, %height%.",
                    },
                ),
                "filename_prefix": (
                    "STRING",
                    {
                        "default": "%source_stem%",
                        "multiline": False,
                        "tooltip": "Base filename before numbering. Supports %source_stem%, %text_stem%, %year%, %month%, %day%, %hour%, %minute%, %second%, %width%, %height%, %batch_num%.",
                    },
                ),
                "filename_delimiter": (
                    "STRING",
                    {
                        "default": "_",
                        "multiline": False,
                        "tooltip": "Text placed between filename_prefix and the numeric counter.",
                    },
                ),
                "filename_number_padding": (
                    "INT",
                    {
                        "default": 5,
                        "min": 1,
                        "max": 10,
                        "tooltip": "Zero padding width for the counter, e.g. 5 -> 00001.",
                    },
                ),
                "filename_number_start": (
                    "INT",
                    {
                        "default": 1,
                        "min": 0,
                        "max": 999999999,
                        "tooltip": "First counter value to try when writing files.",
                    },
                ),
                "overwrite_existing": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "If disabled, the node searches for the next free number. If enabled, existing files at the target counter are overwritten.",
                    },
                ),
                "prompt_text": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": "Plain text prompt sidecar content. If save_prompt_txt is enabled, this is written to a same-name .txt file.",
                    },
                ),
                "save_prompt_txt": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Write prompt_text to a same-name .txt file beside each saved image.",
                    },
                ),
                "workflow_metadata_mode": (
                    ["none", "embed_png", "save_json", "embed_png_and_json"],
                    {
                        "default": "embed_png_and_json",
                        "tooltip": "How to record workflow metadata. embed_png stores prompt/workflow inside PNG metadata. save_json writes a same-name .workflow.json sidecar. embed_png_and_json does both.",
                    },
                ),
                "image_format": (
                    ["png", "jpeg", "webp", "tiff", "bmp"],
                    {
                        "default": "png",
                        "tooltip": "Saved image format.",
                    },
                ),
                "jpeg_quality": (
                    "INT",
                    {
                        "default": 95,
                        "min": 1,
                        "max": 100,
                        "tooltip": "JPEG/WebP quality setting.",
                    },
                ),
                "png_compress": (
                    "INT",
                    {
                        "default": 4,
                        "min": 0,
                        "max": 9,
                        "tooltip": "PNG compression level. Lower is faster, higher is smaller.",
                    },
                ),
                "lossless_webp": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "When saving WebP, use lossless mode instead of lossy quality mode.",
                    },
                ),
                "naming_mode": (
                    ["counter_suffix", "exact_name", "exact_name_overwrite"],
                    {
                        "default": "counter_suffix",
                        "tooltip": "counter_suffix keeps the numeric suffix behavior. exact_name uses the base name directly and only adds suffixes on conflict. exact_name_overwrite uses the exact name and overwrites existing files.",
                    },
                ),
            },
            "optional": {
                "source_image_path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional original image path. Stored into workflow sidecar JSON for traceability.",
                    },
                ),
                "source_image_stem": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional source filename stem. Useful for %source_stem% based renaming.",
                    },
                ),
                "source_text_path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional paired txt path. If prompt_text is empty, the writer falls back to reading this file.",
                    },
                ),
                "source_text_stem": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional paired txt stem. Useful for %text_stem% based renaming.",
                    },
                ),
            },
        }

    def proxy(
        self,
        images,
        output_path,
        filename_prefix,
        filename_delimiter,
        filename_number_padding,
        filename_number_start,
        overwrite_existing,
        prompt_text,
        save_prompt_txt,
        workflow_metadata_mode,
        image_format,
        jpeg_quality,
        png_compress,
        lossless_webp,
        naming_mode="counter_suffix",
        source_image_path="",
        source_image_stem="",
        source_text_path="",
        source_text_stem="",
    ):
        (
            filename_delimiter,
            filename_number_padding,
            filename_number_start,
            overwrite_existing,
            prompt_text,
            save_prompt_txt,
            workflow_metadata_mode,
            image_format,
            jpeg_quality,
            png_compress,
            lossless_webp,
            naming_mode,
        ) = _normalize_writer_inputs(
            filename_delimiter=filename_delimiter,
            filename_number_padding=filename_number_padding,
            filename_number_start=filename_number_start,
            overwrite_existing=overwrite_existing,
            prompt_text=prompt_text,
            save_prompt_txt=save_prompt_txt,
            workflow_metadata_mode=workflow_metadata_mode,
            image_format=image_format,
            jpeg_quality=jpeg_quality,
            png_compress=png_compress,
            lossless_webp=lossless_webp,
            naming_mode=naming_mode,
        )
        return ("proxy",)


class IndependentResultWriter:
    CATEGORY = "Independent Prompt"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    DESCRIPTION = "Real output node used only inside queued child prompts. Saves image files, optional prompt .txt files, and optional workflow metadata sidecars."

    @classmethod
    def INPUT_TYPES(cls):
        spec = copy.deepcopy(IndependentResultWriterProxy.INPUT_TYPES())
        spec["hidden"] = {
            "prompt": "PROMPT",
            "extra_pnginfo": "EXTRA_PNGINFO",
        }
        return spec

    def save(
        self,
        images,
        output_path,
        filename_prefix,
        filename_delimiter,
        filename_number_padding,
        filename_number_start,
        overwrite_existing,
        prompt_text,
        save_prompt_txt,
        workflow_metadata_mode,
        image_format,
        jpeg_quality,
        png_compress,
        lossless_webp,
        naming_mode="counter_suffix",
        source_image_path="",
        source_image_stem="",
        source_text_path="",
        source_text_stem="",
        prompt=None,
        extra_pnginfo=None,
    ):
        (
            filename_delimiter,
            filename_number_padding,
            filename_number_start,
            overwrite_existing,
            prompt_text,
            save_prompt_txt,
            workflow_metadata_mode,
            image_format,
            jpeg_quality,
            png_compress,
            lossless_webp,
            naming_mode,
        ) = _normalize_writer_inputs(
            filename_delimiter=filename_delimiter,
            filename_number_padding=filename_number_padding,
            filename_number_start=filename_number_start,
            overwrite_existing=overwrite_existing,
            prompt_text=prompt_text,
            save_prompt_txt=save_prompt_txt,
            workflow_metadata_mode=workflow_metadata_mode,
            image_format=image_format,
            jpeg_quality=jpeg_quality,
            png_compress=png_compress,
            lossless_webp=lossless_webp,
            naming_mode=naming_mode,
        )
        files, ui_images = _save_tensor_images(
            images=images,
            output_path=output_path,
            filename_prefix=filename_prefix,
            filename_delimiter=filename_delimiter,
            filename_number_padding=filename_number_padding,
            filename_number_start=filename_number_start,
            overwrite_existing=overwrite_existing,
            prompt_text=prompt_text,
            save_prompt_txt=save_prompt_txt,
            workflow_metadata_mode=workflow_metadata_mode,
            image_format=image_format,
            jpeg_quality=jpeg_quality,
            png_compress=png_compress,
            lossless_webp=lossless_webp,
            naming_mode=naming_mode,
            source_image_path=source_image_path,
            source_image_stem=source_image_stem,
            source_text_path=source_text_path,
            source_text_stem=source_text_stem,
            prompt=prompt,
            extra_pnginfo=extra_pnginfo,
        )
        return {
            "ui": {"images": ui_images},
            "result": (json.dumps(files, ensure_ascii=False),),
        }


class IndependentPromptFolderQueue:
    CATEGORY = "Independent Prompt"
    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("summary", "queued_jobs")
    FUNCTION = "queue_jobs"
    OUTPUT_NODE = True
    DESCRIPTION = "Parent workflow scheduler. Scans an image folder and submits one completely independent child prompt per image so heavy workflows do not accumulate execution-state VRAM across a long loop."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enabled": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Master switch for queue submission. Disable to bypass scheduling without rewiring the workflow.",
                    },
                ),
                "image_folder": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Absolute folder path to scan for source images.",
                    },
                ),
                "loader_node_id": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 999999,
                        "tooltip": "Node id of Independent Load Image Path. Use 0 to auto-detect when there is exactly one such node in the workflow.",
                    },
                ),
                "writer_node_id": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 999999,
                        "tooltip": "Node id of Independent Result Writer (Proxy). Use 0 to auto-detect when there is exactly one such node in the workflow.",
                    },
                ),
                "start_index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 999999,
                        "tooltip": "Skip this many files from the sorted folder listing before queuing.",
                    },
                ),
                "limit": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 999999,
                        "tooltip": "0 means queue all matching files after start_index. Any positive value limits the number of queued child prompts.",
                    },
                ),
                "sort_mode": (
                    ["name_asc", "name_desc", "mtime_asc", "mtime_desc"],
                    {
                        "default": "name_asc",
                        "tooltip": "How files are ordered before start_index and limit are applied.",
                    },
                ),
                "text_pair_mode": (
                    ["none", "same_name_same_folder", "same_name_txt_folder", "sorted_txt_same_folder", "sorted_txt_folder"],
                    {
                        "default": "none",
                        "tooltip": "Optional txt pairing mode. same_name_* uses image stem -> txt name matching. sorted_txt_* pairs the selected images with txt files by sorted order.",
                    },
                ),
                "text_folder": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Optional txt folder used by same_name_txt_folder or sorted_txt_folder. Leave empty for modes that read txt beside the images.",
                    },
                ),
                "text_sort_mode": (
                    ["name_asc", "name_desc", "mtime_asc", "mtime_desc"],
                    {
                        "default": "name_asc",
                        "tooltip": "Sorting rule for txt files when a sorted_txt_* pairing mode is used.",
                    },
                ),
                "missing_text_behavior": (
                    ["empty", "error"],
                    {
                        "default": "empty",
                        "tooltip": "When a paired txt is missing, either continue with empty text or stop with an error.",
                    },
                ),
                "queue_front": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "If enabled, inserted child prompts are pushed to the front of the ComfyUI queue.",
                    },
                ),
                "dry_run": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "Count and prepare jobs without actually submitting them to the ComfyUI queue.",
                    },
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            },
        }

    def _detect_single_node(self, prompt, class_type):
        matched = [node_id for node_id, node in prompt.items() if node.get("class_type") == class_type]
        if len(matched) != 1:
            raise ValueError(f"Expected exactly one node of type {class_type}, found {len(matched)}")
        return matched[0]

    def _resolve_target_ids(self, prompt, loader_node_id, writer_node_id):
        loader_id = str(loader_node_id) if loader_node_id else self._detect_single_node(prompt, "IndependentLoadImagePath")
        writer_id = str(writer_node_id) if writer_node_id else self._detect_single_node(prompt, "IndependentResultWriterProxy")

        if loader_id not in prompt:
            raise ValueError(f"Loader node id not found in prompt: {loader_id}")
        if writer_id not in prompt:
            raise ValueError(f"Writer node id not found in prompt: {writer_id}")

        return loader_id, writer_id

    def _collect_output_targets(self, prompt, scheduler_id):
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

    def queue_jobs(
        self,
        enabled,
        image_folder,
        loader_node_id,
        writer_node_id,
        start_index,
        limit,
        sort_mode,
        text_pair_mode,
        text_folder,
        text_sort_mode,
        missing_text_behavior,
        queue_front,
        dry_run,
        prompt=None,
        extra_pnginfo=None,
        unique_id=None,
    ):
        if not enabled:
            return ("Independent queue disabled", 0)

        if not prompt:
            raise ValueError("Current prompt data is missing")

        (
            text_pair_mode,
            text_folder,
            text_sort_mode,
            missing_text_behavior,
            queue_front,
            dry_run,
        ) = _normalize_queue_inputs(
            text_pair_mode=text_pair_mode,
            text_folder=text_folder,
            text_sort_mode=text_sort_mode,
            missing_text_behavior=missing_text_behavior,
            queue_front=queue_front,
            dry_run=dry_run,
        )

        files = _sorted_image_files(image_folder, sort_mode)
        files = files[start_index:]
        if limit > 0:
            files = files[:limit]

        if not files:
            return ("No image files matched", 0)

        text_pairs = _resolve_text_pairs(
            image_files=files,
            text_pair_mode=text_pair_mode,
            text_folder=text_folder,
            text_sort_mode=text_sort_mode,
            missing_text_behavior=missing_text_behavior,
        )

        loader_id, writer_id = self._resolve_target_ids(prompt, loader_node_id, writer_node_id)
        scheduler_id = str(unique_id)
        server = PromptServer.instance
        client_id = getattr(server, "client_id", None)

        queued = 0
        _send_status(unique_id, f"Preparing {len(files)} independent prompts")

        for image_path in files:
            child_prompt = copy.deepcopy(prompt)
            text_path = text_pairs.get(str(image_path))
            text_path_str = str(text_path or "")
            text_stem = Path(text_path).stem if text_path else ""

            child_prompt[loader_id]["inputs"]["image_path"] = str(image_path)
            child_prompt[loader_id]["inputs"]["text_path"] = text_path_str
            child_prompt[writer_id]["class_type"] = "IndependentResultWriter"

            writer_inputs = child_prompt[writer_id].setdefault("inputs", {})
            writer_inputs.setdefault("source_image_path", str(image_path))
            writer_inputs.setdefault("source_image_stem", image_path.stem)
            writer_inputs.setdefault("source_text_path", text_path_str)
            writer_inputs.setdefault("source_text_stem", text_stem)

            if scheduler_id in child_prompt and "inputs" in child_prompt[scheduler_id]:
                child_prompt[scheduler_id]["inputs"]["enabled"] = False

            if dry_run:
                queued += 1
                continue

            prompt_id = str(uuid.uuid4())
            extra_data = {"extra_pnginfo": extra_pnginfo or {}}
            if client_id is not None:
                extra_data["client_id"] = client_id
            extra_data["create_time"] = int(time.time() * 1000)

            number = server.number
            if queue_front:
                number = -number
            server.number += 1

            outputs_to_execute = self._collect_output_targets(child_prompt, scheduler_id)
            if not outputs_to_execute:
                outputs_to_execute = [writer_id]
            sensitive = {}
            server.prompt_queue.put((number, prompt_id, child_prompt, extra_data, outputs_to_execute, sensitive))
            queued += 1

        summary = f"Queued {queued} / {len(files)} independent prompts"
        _send_status(unique_id, summary)
        return (summary, queued)


NODE_CLASS_MAPPINGS = {
    "IndependentLoadImagePath": IndependentLoadImagePath,
    "IndependentResultWriterProxy": IndependentResultWriterProxy,
    "IndependentResultWriter": IndependentResultWriter,
    "IndependentPromptFolderQueue": IndependentPromptFolderQueue,
}


NODE_DISPLAY_NAME_MAPPINGS = {
    "IndependentLoadImagePath": "Independent Load Image Path",
    "IndependentResultWriterProxy": "Independent Result Writer (Proxy)",
    "IndependentResultWriter": "Independent Result Writer",
    "IndependentPromptFolderQueue": "Independent Prompt Folder Queue",
}


_register_prompt_hook()
