import json
import math
import os
import re
from typing import Optional

import torch
import comfy.utils
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

DISPLAY_NAME = (
    "智能填充裁剪缩放"
    if LOCALE == "zh"
    else "Smart Fill Crop Resize"
)

BUCKET_DISPLAY_NAME = (
    "分桶裁剪缩放"
    if LOCALE == "zh"
    else "Bucket Crop Resize"
)

ASPECT_DISPLAY_NAME = (
    "分桶长宽整数"
    if LOCALE == "zh"
    else "Bucket Size Integers"
)

CATEGORY_NAME = (
    "Studio Suite/图像预处理"
    if LOCALE == "zh"
    else "Studio Suite/Image Prep"
)


DEFAULT_BUCKETS = """\
1024x1024
832x1216
896x1152
960x1088
1088x960
1152x896
1216x832
768x1344
1344x768
1024x1536
1536x1024
"""


def _image_hw(image: torch.Tensor) -> tuple[int, int]:
    if image.ndim == 3:
        return int(image.shape[0]), int(image.shape[1])
    if image.ndim == 4:
        return int(image.shape[1]), int(image.shape[2])
    raise ValueError("IMAGE tensor must be HWC or BHWC")


def _crop_offsets(current: int, target: int, mode: str, axis: str) -> tuple[int, int]:
    if target >= current:
        return 0, current
    mode = (mode or "center").lower()
    if axis == "x" and mode == "left":
        start = 0
    elif axis == "x" and mode == "right":
        start = current - target
    elif axis == "y" and mode == "top":
        start = 0
    elif axis == "y" and mode == "bottom":
        start = current - target
    else:
        start = (current - target) // 2
    return start, start + target


def _crop_to_aspect(image: torch.Tensor, target_width: int, target_height: int, crop_mode: str) -> torch.Tensor:
    crop_mode = (crop_mode or "center").lower()
    if crop_mode in ("disabled", "none", "off", "stretch"):
        return image

    height, width = _image_hw(image)
    target_width = max(1, int(target_width))
    target_height = max(1, int(target_height))
    current_ratio = width / height
    target_ratio = target_width / target_height

    if abs(current_ratio - target_ratio) < 1e-6:
        return image

    if current_ratio > target_ratio:
        crop_width = max(1, min(width, int(round(height * target_ratio))))
        x0, x1 = _crop_offsets(width, crop_width, crop_mode, "x")
        return image[:, x0:x1, :] if image.ndim == 3 else image[:, :, x0:x1, :]

    crop_height = max(1, min(height, int(round(width / target_ratio))))
    y0, y1 = _crop_offsets(height, crop_height, crop_mode, "y")
    return image[y0:y1, :, :] if image.ndim == 3 else image[:, y0:y1, :, :]


def _resize_image(image: torch.Tensor, width: int, height: int, upscale_method: str) -> torch.Tensor:
    samples = image.movedim(-1, 1)
    scaled = comfy.utils.common_upscale(samples, width, height, upscale_method, "disabled")
    return scaled.movedim(1, -1)


def _parse_bucket_specs(bucket_specs: str) -> list[tuple[int, int, str]]:
    buckets = []
    for raw_line in (bucket_specs or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.search(r"(\d+)\s*(?:x|X|\*|,|，|\s)\s*(\d+)", line)
        if not match:
            continue
        width = int(match.group(1))
        height = int(match.group(2))
        if width > 0 and height > 0:
            buckets.append((width, height, f"{width}x{height}"))
    if not buckets:
        raise ValueError("No valid bucket specs found. Use one resolution per line, e.g. 1024x1536.")
    return buckets


def _nearest_bucket(width: int, height: int, buckets: list[tuple[int, int, str]]) -> tuple[int, int, str, int]:
    image_ratio = width / height
    best_index = 0
    best_score = float("inf")
    for idx, (bucket_width, bucket_height, _) in enumerate(buckets):
        bucket_ratio = bucket_width / bucket_height
        score = abs(math.log(image_ratio / bucket_ratio))
        if score < best_score:
            best_score = score
            best_index = idx
    bucket_width, bucket_height, label = buckets[best_index]
    return bucket_width, bucket_height, label, best_index


class SmartFillCropResize:
    upscale_methods = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]
    crop_modes = ["center", "disabled", "top", "bottom", "left", "right"]

    @classmethod
    def INPUT_TYPES(cls):
        bg_type_options = ["alpha", "color"]
        return {
            "required": {
                "image": ("IMAGE",),
                "fill_transparent": ("BOOLEAN", {"default": True}),
                "background_type": (bg_type_options,),
                "background_color": ("COLORCODE", {"default": "#222222"}),
                "width": ("INT", {"default": 1024, "min": 1, "max": 8192, "step": 1}),
                "height": ("INT", {"default": 1024, "min": 1, "max": 8192, "step": 1}),
                "upscale_method": (cls.upscale_methods,),
                "crop_mode": (cls.crop_modes, {"default": "center"}),
            },
            "optional": {
                "mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "process"
    CATEGORY = CATEGORY_NAME

    def _repeat_to_batch(self, tensor: torch.Tensor, batch_size: int) -> torch.Tensor:
        if tensor.shape[0] == batch_size:
            return tensor
        if tensor.shape[0] <= 0:
            raise ValueError("tensor batch size must be positive")
        repeats = (batch_size + tensor.shape[0] - 1) // tensor.shape[0]
        return tensor.repeat(repeats, *([1] * (tensor.ndim - 1)))[:batch_size]

    def _normalize_mask(
        self,
        mask: Optional[torch.Tensor],
        batch_size: int,
        height: int,
        width: int,
        device: torch.device,
        dtype: torch.dtype,
    ) -> Optional[torch.Tensor]:
        if mask is None:
            return None

        if mask.ndim == 2:
            mask = mask.unsqueeze(0)
        elif mask.ndim == 4:
            if mask.shape[-1] == 1:
                mask = mask[..., 0]
            elif mask.shape[1] == 1:
                mask = mask[:, 0]
            else:
                mask = mask[..., 0]

        if mask.ndim != 3:
            return None

        mask = mask.to(device=device, dtype=dtype)
        mask = self._repeat_to_batch(mask, batch_size)
        mask = mask.unsqueeze(1)

        if mask.shape[-2:] != (height, width):
            mask = comfy.utils.common_upscale(mask, width, height, "bilinear", "center")

        return mask.squeeze(1).clamp(0.0, 1.0).unsqueeze(-1)

    def _parse_background_color(self, background_color: str, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
        hex_color = (background_color or "#222222").strip()
        if not hex_color.startswith("#"):
            hex_color = "#" + hex_color
        if len(hex_color) not in (7, 9):
            hex_color = "#222222"
        try:
            r = int(hex_color[1:3], 16) / 255.0
            g = int(hex_color[3:5], 16) / 255.0
            b = int(hex_color[5:7], 16) / 255.0
        except Exception:
            r, g, b = (34 / 255.0, 34 / 255.0, 34 / 255.0)
        return torch.tensor([r, g, b], device=device, dtype=dtype).view(1, 1, 1, 3)

    def _fill_with_color(
        self,
        image: torch.Tensor,
        fill_transparent: bool,
        background_type: str,
        background_color: str,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        if (not fill_transparent) or (background_type == "alpha"):
            return image

        if image.ndim == 3:
            image = image.unsqueeze(0)
        if image.ndim != 4:
            return image

        batch_size, height, width, channels = image.shape
        if channels < 3:
            return image

        rgb = image[..., :3]
        alpha_mask = None
        if channels >= 4:
            alpha_mask = (1.0 - image[..., 3:4]).clamp(0.0, 1.0)

        user_mask = self._normalize_mask(mask, batch_size, height, width, image.device, image.dtype)

        fill_mask = user_mask
        if alpha_mask is not None:
            fill_mask = alpha_mask if fill_mask is None else torch.maximum(fill_mask, alpha_mask)

        if fill_mask is None:
            return rgb

        color = self._parse_background_color(background_color, image.device, image.dtype)
        return rgb * (1.0 - fill_mask) + color * fill_mask

    def process(
        self,
        image,
        fill_transparent,
        background_type,
        background_color,
        width,
        height,
        upscale_method,
        crop_mode="center",
        mask=None,
    ):
        img = self._fill_with_color(image, fill_transparent, background_type, background_color, mask)
        cropped = _crop_to_aspect(img, width, height, crop_mode)
        return (_resize_image(cropped, width, height, upscale_method),)


class StudioSuiteBucketCropResize:
    upscale_methods = SmartFillCropResize.upscale_methods
    crop_modes = ["center", "top", "bottom", "left", "right"]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "bucket_specs": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": DEFAULT_BUCKETS,
                    },
                ),
                "upscale_method": (cls.upscale_methods,),
                "crop_mode": (cls.crop_modes, {"default": "center"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "INT", "INT", "STRING", "INT")
    RETURN_NAMES = ("image", "width", "height", "bucket", "bucket_index")
    FUNCTION = "process"
    CATEGORY = CATEGORY_NAME

    def process(self, image, bucket_specs, upscale_method, crop_mode="center"):
        height, width = _image_hw(image)
        buckets = _parse_bucket_specs(bucket_specs)
        target_width, target_height, bucket_label, bucket_index = _nearest_bucket(width, height, buckets)
        cropped = _crop_to_aspect(image, target_width, target_height, crop_mode)
        output = _resize_image(cropped, target_width, target_height, upscale_method)
        return (output, target_width, target_height, bucket_label, bucket_index)


class StudioSuiteImageAspectRatioIntegers:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "bucket_specs": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": DEFAULT_BUCKETS,
                    },
                ),
            },
        }

    RETURN_TYPES = ("INT", "INT", "STRING", "INT", "FLOAT", "INT", "INT", "STRING")
    RETURN_NAMES = (
        "width",
        "height",
        "bucket",
        "bucket_index",
        "aspect_ratio",
        "source_width",
        "source_height",
        "source_ratio",
    )
    FUNCTION = "calculate"
    CATEGORY = CATEGORY_NAME

    def calculate(self, image, bucket_specs):
        height, width = _image_hw(image)
        buckets = _parse_bucket_specs(bucket_specs)
        target_width, target_height, bucket_label, bucket_index = _nearest_bucket(width, height, buckets)
        divisor = math.gcd(width, height)
        ratio_width = width // divisor
        ratio_height = height // divisor
        return (
            target_width,
            target_height,
            bucket_label,
            bucket_index,
            width / height,
            width,
            height,
            f"{ratio_width}:{ratio_height}",
        )


NODE_CLASS_MAPPINGS = {
    "SmartFillCropResize": SmartFillCropResize,
    "StudioSuiteBucketCropResize": StudioSuiteBucketCropResize,
    "StudioSuiteImageAspectRatioIntegers": StudioSuiteImageAspectRatioIntegers,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartFillCropResize": DISPLAY_NAME,
    "StudioSuiteBucketCropResize": BUCKET_DISPLAY_NAME,
    "StudioSuiteImageAspectRatioIntegers": ASPECT_DISPLAY_NAME,
}
