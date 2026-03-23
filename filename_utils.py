"""Filename sanitization (no heavy UI imports)."""

from __future__ import annotations

import re


def sanitize_filename_stem(name: str) -> str:
    """Safe filename stem: preserves letter case; only A–Z, a–z, 0–9; spaces/specials → _."""
    s = (name or "").strip()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "face"
