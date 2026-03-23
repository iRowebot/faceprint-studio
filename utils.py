"""Shared helpers for FacePrint Studio."""

from __future__ import annotations

from PIL import Image
import customtkinter as ctk

from filename_utils import sanitize_filename_stem

__all__ = ["pil_to_ctk", "fit_image", "sanitize_filename_stem"]


def pil_to_ctk(
    pil_img: Image.Image,
    size: tuple[int, int] | None = None,
) -> ctk.CTkImage:
    """Wrap a PIL image for display in CustomTkinter widgets."""
    sz = size or (pil_img.width, pil_img.height)
    return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=sz)


def fit_image(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Return a copy that fits inside *max_w*×*max_h*, preserving aspect ratio."""
    ratio = min(max_w / img.width, max_h / img.height, 1.0)
    w = max(1, int(img.width * ratio))
    h = max(1, int(img.height * ratio))
    if (w, h) == (img.width, img.height):
        return img
    return img.resize((w, h), Image.LANCZOS)
