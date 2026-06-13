import json
import os
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

CATEGORY_NAME = (
    "图像/Image Toolbox"
    if LOCALE == "zh"
    else "image/Image Toolbox"
)


class SmartFillCropResize:
    upscale_methods = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]

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

    def process(self, image, fill_transparent, background_type, background_color, width, height, upscale_method, mask=None):
        img = self._fill_with_color(image, fill_transparent, background_type, background_color, mask)
        samples = img.movedim(-1, 1)
        scaled = comfy.utils.common_upscale(samples, width, height, upscale_method, "center")
        return (scaled.movedim(1, -1),)


NODE_CLASS_MAPPINGS = {
    "SmartFillCropResize": SmartFillCropResize,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SmartFillCropResize": DISPLAY_NAME,
}
