from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image


def rgb_obs_to_image(rgb):
    if hasattr(rgb, "detach"):
        rgb = rgb.detach().cpu().numpy()
    rgb = np.asarray(rgb)
    if rgb.ndim != 3:
        raise ValueError(f"Expected HxWxC rgb frame, got {rgb.shape}")
    if rgb.shape[2] >= 3:
        rgb = rgb[:, :, :3]
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, mode="RGB")


def resize_image_to_width(image, width):
    width = int(width or 0)
    if width <= 0 or image.width <= width:
        return image
    height = max(1, round(image.height * (width / image.width)))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def rgb_obs_to_jpeg(rgb, quality=72, width=None):
    image = resize_image_to_width(rgb_obs_to_image(rgb), width)
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue()


def write_rgb_obs_jpeg(rgb, path, quality=80, width=None):
    path = Path(path)
    image = resize_image_to_width(rgb_obs_to_image(rgb), width)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=int(quality), optimize=False)
    return {
        "path": str(path),
        "width": int(image.width),
        "height": int(image.height),
        "format": "jpeg",
        "quality": int(quality),
        "bytes": int(path.stat().st_size),
    }


def zero_action_like(value):
    if isinstance(value, dict):
        return {key: zero_action_like(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(zero_action_like(item) for item in value)
    if isinstance(value, list):
        return [zero_action_like(item) for item in value]
    try:
        return value * 0.0
    except TypeError:
        return np.zeros_like(value)
