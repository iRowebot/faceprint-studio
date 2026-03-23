"""Persist and restore the face library to Documents/FacePrintLibrary.

Each face is stored as:
  {SanitizedName}.png       — 300×300 "Heads" crop (case preserved; unsafe chars → _)
  {SanitizedName}_tight.png — 300×300 "Faces" (tight) crop
  {SanitizedName}_lib_thumb.jpg / _lib_thumb_tight.jpg — optional 96×96 JPEG grid thumbnails

library.json maps filename keys to metadata (display_name, original_image, date_added, person_id).

Incremental save: Person.needs_png_write False for rows loaded unchanged from disk — PNG files
are skipped until the image data changes; library.json is always rewritten.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List

from PIL import Image, ImageStat

from filename_utils import sanitize_filename_stem

try:
    from version import __version__ as _APP_VERSION
except ImportError:
    _APP_VERSION = "0.0.0"

if TYPE_CHECKING:
    from face_processor import Person

# library.json _meta.library_schema_version — bump only when on-disk JSON shape changes
LIBRARY_SCHEMA_VERSION = 2

def _windows_documents_dir() -> Path | None:
    """Resolve the real Windows Documents folder (handles OneDrive redirect)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        CSIDL_PERSONAL = 5  # My Documents
        buf = ctypes.create_unicode_buffer(260)
        hr = ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, 0, buf)
        if hr != 0:
            return None
        p = Path(buf.value)
        if p.is_dir():
            return p
    except Exception:
        pass
    return None


def _default_library_dir() -> Path:
    docs = _windows_documents_dir()
    if docs is not None:
        return docs / "FacePrintLibrary"
    return Path(os.path.expanduser("~/Documents/FacePrintLibrary"))


# Shared library folder (same “Documents” you see in Explorer on Windows)
LIBRARY_DIR = _default_library_dir()
LIBRARY_JSON = LIBRARY_DIR / "library.json"


def ensure_library_dir() -> None:
    """Create the library folder on disk. Call at startup so the path always exists."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

# Legacy location (one-time migration)
_LEGACY_DIR = Path.home() / ".faceprint_studio" / "library"
_LEGACY_MANIFEST = _LEGACY_DIR / "manifest.json"


def _heads_path(stem: str) -> Path:
    return LIBRARY_DIR / f"{stem}.png"


def _tight_path(stem: str) -> Path:
    return LIBRARY_DIR / f"{stem}_tight.png"


def _heads_thumb_path(stem: str) -> Path:
    return LIBRARY_DIR / f"{stem}_lib_thumb.jpg"


def _tight_thumb_path(stem: str) -> Path:
    return LIBRARY_DIR / f"{stem}_lib_thumb_tight.jpg"


def load_disk_thumb(stem: str, use_tight: bool) -> Image.Image | None:
    """Fast path: 96×96 JPEG sidecar next to PNG crops (optional)."""
    if not (stem or "").strip():
        return None
    path = _tight_thumb_path(stem) if use_tight else _heads_thumb_path(stem)
    if not path.exists():
        return None
    try:
        im = Image.open(path)
        im.load()
        return im.convert("RGB")
    except Exception:
        return None


def write_sidecar_thumbs(stem: str, p: "Person") -> None:
    """Write 96×96 JPEG thumbs for grid (Heads + Faces)."""
    if not (stem or "").strip():
        return
    ensure_face_tight_loaded(p)
    rs = getattr(Image, "Resampling", Image).BILINEAR
    try:
        t = p.face_image.convert("RGB")
        t.thumbnail((96, 96), rs)
        t.save(_heads_thumb_path(stem), format="JPEG", quality=88, optimize=True)
    except Exception:
        pass
    try:
        src = p.face_tight_image or p.face_image
        t2 = src.convert("RGB")
        t2.thumbnail((96, 96), rs)
        t2.save(_tight_thumb_path(stem), format="JPEG", quality=88, optimize=True)
    except Exception:
        pass


def ensure_thumbs_for_person(stem: str, p: "Person") -> None:
    """Create sidecar JPEGs if missing (migration / first display)."""
    if not (stem or "").strip():
        return
    h = _heads_thumb_path(stem)
    t = _tight_thumb_path(stem)
    if h.is_file() and t.is_file():
        return
    write_sidecar_thumbs(stem, p)


def _rename_thumb_stems_on_disk(old_stem: str, new_stem: str) -> None:
    for op, np in (
        (_heads_thumb_path(old_stem), _heads_thumb_path(new_stem)),
        (_tight_thumb_path(old_stem), _tight_thumb_path(new_stem)),
    ):
        if op.exists() and op != np:
            if np.exists():
                try:
                    np.unlink()
                except Exception:
                    pass
            try:
                op.rename(np)
            except Exception:
                pass


def _repair_incremental_flags_after_load(persons: List["Person"]) -> None:
    """Upgrade safety: older releases had no needs_png_write; if heads/tight PNGs are missing on disk, force a rewrite.

    Library path and filenames are unchanged across versions — this only fixes inconsistent state.
    """
    for p in persons:
        if getattr(p, "needs_png_write", True):
            continue
        stem = (p.file_stem or "").strip()
        if not stem:
            p.needs_png_write = True
            continue
        if not _heads_path(stem).exists():
            p.needs_png_write = True
            continue
        if not _tight_path(stem).exists():
            p.needs_png_write = True


def ensure_face_tight_loaded(p: "Person") -> None:
    """Load the Faces (tight) crop from disk if it exists but is not yet in memory.

    Saved libraries previously loaded both 300×300 crops at startup; deferring the
    tight image roughly halves PIL memory until the user needs it (Faces mode / export).
    """
    if p.face_tight_image is not None:
        return
    stem = (p.file_stem or "").strip()
    if not stem:
        return
    tp = _tight_path(stem)
    if not tp.exists():
        return
    try:
        p.face_tight_image = Image.open(tp).convert("RGB")
    except Exception:
        pass


def save_library(persons: List["Person"]) -> None:
    """Write library.json always; rewrite PNGs only when Person.needs_png_write is True."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    stem_by_id = _allocate_unique_stems(persons)

    for p in persons:
        new_stem = stem_by_id[p.id]
        old_stem = (p.file_stem or "").strip()
        if old_stem and old_stem != new_stem:
            _rename_stem_on_disk(old_stem, new_stem)
            _rename_thumb_stems_on_disk(old_stem, new_stem)
        p.file_stem = new_stem

        _heads_path(new_stem).parent.mkdir(parents=True, exist_ok=True)

        if p.needs_png_write:
            p.face_image.convert("RGB").save(_heads_path(new_stem), format="PNG", optimize=False)
            tight_src = p.face_tight_image
            if tight_src is None:
                tp = _tight_path(new_stem)
                if tp.exists():
                    try:
                        tight_src = Image.open(tp).convert("RGB")
                    except Exception:
                        tight_src = None
            if tight_src is None:
                tight_src = p.face_image
            tight_src.convert("RGB").save(_tight_path(new_stem), format="PNG", optimize=False)
            write_sidecar_thumbs(new_stem, p)
            p.needs_png_write = False
        else:
            ensure_thumbs_for_person(new_stem, p)

    # Build library.json
    order: list[str] = []
    entries: dict[str, dict] = {}
    for p in persons:
        stem = stem_by_id[p.id]
        fname = f"{stem}.png"
        order.append(fname)
        entries[fname] = {
            "display_name": p.name,
            "original_image": p.original_image or "",
            "date_added": p.date_added or datetime.now().isoformat(timespec="seconds"),
            "person_id": p.id,
            "source_face_id": p.source_face_id or "",
            "quantity": p.quantity,
            "is_low_res": p.is_low_res,
        }

    payload: dict = {
        "_meta": {
            "order": order,
            "library_schema_version": LIBRARY_SCHEMA_VERSION,
            "last_saved_with_version": _APP_VERSION,
        }
    }
    payload.update(entries)
    LIBRARY_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Remove orphaned PNGs / sidecar JPEGs
    allowed_stems = {stem_by_id[p.id] for p in persons}
    for png in LIBRARY_DIR.glob("*.png"):
        stem = png.stem
        if stem.endswith("_tight"):
            stem = stem[: -len("_tight")]
        if stem not in allowed_stems:
            try:
                png.unlink()
            except Exception:
                pass
    for jpg in LIBRARY_DIR.glob("*.jpg"):
        name = jpg.name
        stem: str | None = None
        if name.endswith("_lib_thumb_tight.jpg"):
            stem = name[: -len("_lib_thumb_tight.jpg")]
        elif name.endswith("_lib_thumb.jpg"):
            stem = name[: -len("_lib_thumb.jpg")]
        if stem is not None and stem not in allowed_stems:
            try:
                jpg.unlink()
            except Exception:
                pass


def _rename_stem_on_disk(old_stem: str, new_stem: str) -> None:
    for op, np in (
        (_heads_path(old_stem), _heads_path(new_stem)),
        (_tight_path(old_stem), _tight_path(new_stem)),
    ):
        if op.exists() and op != np:
            if np.exists():
                try:
                    np.unlink()
                except Exception:
                    pass
            try:
                op.rename(np)
            except Exception:
                pass


def _person_from_json_entry(fname: str, entry: dict) -> "Person | None":
    from face_processor import Person

    stem = Path(fname).stem
    hp = _heads_path(stem)
    if not hp.exists():
        return None
    try:
        face_image = Image.open(hp).convert("RGB")
    except Exception:
        return None

    # Tight crop is loaded on demand via ensure_face_tight_loaded() to save RAM at startup.

    pid = entry.get("person_id") or ""
    if not pid:
        pid = str(uuid.uuid4())

    return Person(
        id=pid,
        name=entry.get("display_name", stem),
        quantity=int(entry.get("quantity", 1)),
        is_low_res=bool(entry.get("is_low_res", False)),
        face_image=face_image,
        face_tight_image=None,
        needs_png_write=False,
        file_stem=stem,
        original_image=entry.get("original_image", "") or "",
        date_added=entry.get("date_added", "") or "",
        source_face_id=entry.get("source_face_id", "") or "",
    )


def _legacy_manifest_len() -> int:
    if not _LEGACY_MANIFEST.exists():
        return 0
    try:
        return len(json.loads(_LEGACY_MANIFEST.read_text(encoding="utf-8")))
    except Exception:
        return 0


def _is_suspicious_face_image(img: Image.Image) -> bool:
    """Tiny crops (e.g. test placeholders) or nearly solid black look broken in the UI."""
    w, h = img.size
    if w < 100 or h < 100:
        return True
    try:
        stat = ImageStat.Stat(img.convert("RGB"))
        mean_luma = sum(stat.mean) / 3.0
        if mean_luma < 8.0:
            return True
    except Exception:
        pass
    return False


def _should_rebuild_from_legacy(persons: List["Person"]) -> bool:
    """If images look corrupt, or new library has at most one entry but legacy has more (blocked migration)."""
    mlen = _legacy_manifest_len()
    if mlen == 0:
        return False
    for p in persons:
        if _is_suspicious_face_image(p.face_image):
            return True
        if p.face_tight_image is not None and _is_suspicious_face_image(p.face_tight_image):
            return True
    # Stray library.json with 0–1 rows while legacy still has a full manifest (e.g. dev test run)
    if len(persons) <= 1 and len(persons) < mlen:
        return True
    return False


def _rebuild_from_legacy() -> None:
    """Backup current library.json, clear new-format files, re-import legacy manifest."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    if LIBRARY_JSON.exists():
        try:
            shutil.copy2(LIBRARY_JSON, LIBRARY_DIR / "library.json.recovery_backup")
        except Exception:
            pass
    for png in LIBRARY_DIR.glob("*.png"):
        try:
            png.unlink()
        except Exception:
            pass
    if LIBRARY_JSON.exists():
        try:
            LIBRARY_JSON.unlink()
        except Exception:
            pass
    _try_migrate_legacy()


def _load_persons_from_library_json() -> List["Person"]:
    from face_processor import Person

    if not LIBRARY_JSON.exists():
        return []

    try:
        raw = json.loads(LIBRARY_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []

    meta = raw.pop("_meta", None)
    order: list[str] = []
    if isinstance(meta, dict) and isinstance(meta.get("order"), list):
        order = [str(x) for x in meta["order"]]

    by_file: dict[str, dict] = {k: v for k, v in raw.items() if isinstance(v, dict)}

    if not order:
        order = sorted(by_file.keys())

    persons: list[Person] = []
    seen: set[str] = set()
    for fname in order:
        entry = by_file.get(fname)
        if not entry:
            continue
        p = _person_from_json_entry(fname, entry)
        if p:
            persons.append(p)
            seen.add(fname)

    for fname, entry in by_file.items():
        if fname in seen:
            continue
        p = _person_from_json_entry(fname, entry)
        if p:
            persons.append(p)

    return persons


def load_library() -> List["Person"]:
    """Load library from Documents/FacePrintLibrary. Migrates legacy ~/.faceprint_studio if needed."""
    ensure_library_dir()
    if not LIBRARY_JSON.exists():
        _try_migrate_legacy()

    persons = _load_persons_from_library_json()

    # Recovery: stray library.json (e.g. dev test) blocked migration; tiny/black corrupt PNGs.
    if _should_rebuild_from_legacy(persons):
        _rebuild_from_legacy()
        persons = _load_persons_from_library_json()

    if not persons and not LIBRARY_JSON.exists():
        _try_migrate_legacy()
        persons = _load_persons_from_library_json()

    _repair_incremental_flags_after_load(persons)

    return persons


def _try_migrate_legacy() -> None:
    """If the new library is empty but the old manifest exists, copy into FacePrintLibrary."""
    from face_processor import Person

    if not _LEGACY_MANIFEST.exists():
        return
    if LIBRARY_JSON.exists():
        return

    try:
        manifest: list[dict] = json.loads(
            _LEGACY_MANIFEST.read_text(encoding="utf-8")
        )
    except Exception:
        return

    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    persons: list[Person] = []
    used_stem_keys: set[str] = set()

    for entry in manifest:
        old_id = entry.get("id", "")
        if not old_id:
            continue
        hp = _LEGACY_DIR / f"{old_id}.png"
        if not hp.exists():
            continue
        try:
            face_image = Image.open(hp).convert("RGB")
        except Exception:
            continue
        tp = _LEGACY_DIR / f"{old_id}_tight.png"
        try:
            face_tight = Image.open(tp).convert("RGB") if tp.exists() else face_image
        except Exception:
            face_tight = face_image

        name = entry.get("name", "face")
        base = sanitize_filename_stem(name)
        stem = base
        n = 2
        while _stem_uniqueness_key(stem) in used_stem_keys:
            stem = f"{base}_{n}"
            n += 1
        used_stem_keys.add(_stem_uniqueness_key(stem))

        persons.append(
            Person(
                id=str(uuid.uuid4()),
                name=name,
                quantity=entry.get("quantity", 1),
                is_low_res=entry.get("is_low_res", False),
                face_image=face_image,
                face_tight_image=face_tight,
                file_stem=stem,
                original_image="",
                date_added=datetime.now().isoformat(timespec="seconds"),
            )
        )

    if not persons:
        return

    # Persist using new scheme (rewrite ids to uuid if needed for stability)
    save_library(persons)


def clear_library() -> None:
    """Delete library.json and all face assets in the FacePrint library folder."""
    if LIBRARY_JSON.exists():
        try:
            LIBRARY_JSON.unlink()
        except Exception:
            pass
    for png in LIBRARY_DIR.glob("*.png"):
        try:
            png.unlink()
        except Exception:
            pass
    for jpg in LIBRARY_DIR.glob("*.jpg"):
        try:
            jpg.unlink()
        except Exception:
            pass


def _allocate_unique_stems(persons: List["Person"]) -> dict[str, str]:
    """Map person id -> unique sanitized file stem."""
    used: set[str] = set()
    out: dict[str, str] = {}
    for p in persons:
        base = sanitize_filename_stem(p.name)
        stem = base
        n = 2
        while stem in used:
            stem = f"{base}_{n}"
            n += 1
        used.add(stem)
        out[p.id] = stem
    return out
