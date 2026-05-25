from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image


def rgb_obs_to_jpeg(rgb, quality=72):
    if hasattr(rgb, "detach"):
        rgb = rgb.detach().cpu().numpy()
    rgb = np.asarray(rgb)
    if rgb.ndim != 3:
        raise ValueError(f"Expected HxWxC rgb frame, got {rgb.shape}")
    if rgb.shape[2] >= 3:
        rgb = rgb[:, :, :3]
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    image = Image.fromarray(rgb, mode="RGB")
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue()


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

