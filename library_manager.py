"""Persist and restore the face library between sessions.

Faces are saved to ~/.faceprint_studio/library/
  manifest.json  — metadata for every saved person
  <id>.png       — 300×300 face image for each person
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, List

from PIL import Image

if TYPE_CHECKING:
    from face_processor import Person

LIBRARY_DIR = Path.home() / ".faceprint_studio" / "library"
MANIFEST_FILE = LIBRARY_DIR / "manifest.json"


def save_library(persons: List[Person]) -> None:
    """Write the full library to disk, replacing any previous save."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    saved_ids: set[str] = set()

    for p in persons:
        img_path = LIBRARY_DIR / f"{p.id}.png"
        p.face_image.convert("RGB").save(img_path, format="PNG", optimize=False)
        saved_ids.add(p.id)
        manifest.append(
            {
                "id": p.id,
                "name": p.name,
                "quantity": p.quantity,
                "is_low_res": p.is_low_res,
            }
        )

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Remove orphaned PNGs no longer in the library
    for png in LIBRARY_DIR.glob("*.png"):
        if png.stem not in saved_ids:
            try:
                png.unlink()
            except Exception:
                pass


def load_library() -> List[Person]:
    """Load the saved library from disk.  Returns an empty list if none exists."""
    from face_processor import Person

    if not MANIFEST_FILE.exists():
        return []

    try:
        manifest: list[dict] = json.loads(
            MANIFEST_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return []

    persons: list[Person] = []
    for entry in manifest:
        img_path = LIBRARY_DIR / f"{entry['id']}.png"
        if not img_path.exists():
            continue
        try:
            face_image = Image.open(img_path).convert("RGB")
        except Exception:
            continue
        persons.append(
            Person(
                id=entry["id"],
                name=entry.get("name", "Unknown"),
                quantity=entry.get("quantity", 1),
                is_low_res=entry.get("is_low_res", False),
                face_image=face_image,
            )
        )
    return persons


def clear_library() -> None:
    """Delete all saved library data from disk."""
    if MANIFEST_FILE.exists():
        MANIFEST_FILE.unlink()
    for png in LIBRARY_DIR.glob("*.png"):
        try:
            png.unlink()
        except Exception:
            pass
