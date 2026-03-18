"""Face detection and precise cropping engine for FacePrint Studio.

Uses OpenCV's FaceDetectorYN (YuNet) — fast, accurate at all distances,
works great for group photos. No dlib, no cmake, no heavy dependencies.
"""

from __future__ import annotations

import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

_HEIC_EXTS = {".heic", ".heif"}

CROP_TARGET_PX = 300

# Minimum raw face crop size (pixels) before upscaling is considered low-res.
# Based on 0.5" × 0.5" print size at 300 DPI = 150 px.
_LOW_RES_THRESHOLD_PX = 150

_MIN_DETECTION_CONFIDENCE = 0.3
_NMS_THRESHOLD = 0.3

_YUNET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_MODELS_DIR = Path.home() / ".faceprint_studio" / "models"


@dataclass
class DetectedFace:
    id: str
    source_path: str
    location: Tuple[int, int, int, int]  # (top, right, bottom, left)
    thumbnail: Image.Image
    cropped: Image.Image          # "Heads" crop (with hair/shoulders)
    tight_cropped: Image.Image    # "Faces" crop (face-only, tight)
    is_low_res: bool = False
    selected: bool = False


@dataclass
class Person:
    id: str
    name: str
    face_image: Image.Image              # "Heads" crop
    face_tight_image: Image.Image | None = None  # "Faces" crop
    is_low_res: bool = False
    quantity: int = 1


# ---------------------------------------------------------------------------
#  YuNet Face Detection — lazy init
# ---------------------------------------------------------------------------

_DETECTOR = None


def _ensure_yunet_model() -> Path:
    """Download and cache the YuNet ONNX model."""
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = (_MODELS_DIR / "face_detection_yunet_2023mar.onnx").resolve()
    if not path.exists():
        urllib.request.urlretrieve(_YUNET_MODEL_URL, path)
    return path


def _get_detector() -> cv2.FaceDetectorYN:
    global _DETECTOR
    if _DETECTOR is None:
        model_path = _ensure_yunet_model()
        _DETECTOR = cv2.FaceDetectorYN.create(
            str(model_path),
            "",
            (320, 320),
            _MIN_DETECTION_CONFIDENCE,
            _NMS_THRESHOLD,
            5000,
        )
    return _DETECTOR


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def _open_image(image_path: str) -> Image.Image:
    """Open any image file to an RGB PIL Image, with explicit HEIC/HEIF support."""
    suffix = Path(image_path).suffix.lower()
    if suffix in _HEIC_EXTS:
        try:
            import pillow_heif  # type: ignore[import]
        except ImportError:
            raise RuntimeError(
                "Opening HEIC/HEIF files requires the 'pillow-heif' package.\n"
                "Install with:  pip install pillow-heif"
            )
        try:
            heif = pillow_heif.open_heif(image_path, convert_hdr_to_8bit=True)
            img = Image.frombytes(
                heif.mode, heif.size, heif.data, "raw", heif.mode, heif.stride
            )
        except Exception:
            pillow_heif.register_heif_opener()
            img = Image.open(image_path)
        return img.convert("RGB")

    img = Image.open(image_path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img.convert("RGB")


def detect_faces_in_image(
    image_path: str,
) -> Tuple[Image.Image, List[DetectedFace]]:
    """Load *image_path*, detect every face, return (annotated_copy, faces)."""
    pil_image = _open_image(image_path)
    rgb_array = np.array(pil_image)
    bgr_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)
    h, w = bgr_array.shape[:2]

    detector = _get_detector()
    detector.setInputSize((w, h))
    _, raw_faces = detector.detect(bgr_array)

    annotated = pil_image.copy()
    draw = ImageDraw.Draw(annotated)
    stroke = max(2, min(w, h) // 250)

    faces: List[DetectedFace] = []

    if raw_faces is not None:
        for face_row in raw_faces:
            # YuNet output: [x, y, w, h, ...landmarks..., score]
            x1 = max(0, int(face_row[0]))
            y1 = max(0, int(face_row[1]))
            x2 = min(w, int(face_row[0] + face_row[2]))
            y2 = min(h, int(face_row[1] + face_row[3]))

            if x2 - x1 < 20 or y2 - y1 < 20:
                continue

            loc: Tuple[int, int, int, int] = (y1, x2, y2, x1)

            draw.rectangle([x1, y1, x2, y2], outline="#FF3333", width=stroke)

            tight = pil_image.crop((x1, y1, x2, y2))
            thumb = tight.copy()
            thumb.thumbnail((80, 80), Image.LANCZOS)

            cropped, low = _precise_crop(pil_image, loc)
            tight, _    = _tight_crop(pil_image, loc)
            faces.append(
                DetectedFace(
                    id=uuid.uuid4().hex[:8],
                    source_path=image_path,
                    location=loc,
                    thumbnail=thumb,
                    cropped=cropped,
                    tight_cropped=tight,
                    is_low_res=low,
                )
            )

    return annotated, faces


# ---------------------------------------------------------------------------
#  Precise cropping
# ---------------------------------------------------------------------------

def _precise_crop(
    image: Image.Image,
    face_loc: Tuple[int, int, int, int],
) -> Tuple[Image.Image, bool]:
    """
    1. Start with face bounding box.
    2. Expand to a perfect square (larger dimension, centre the smaller one).
    3. Further expand so the detected face occupies 70 % of the square height,
       with 20 % buffer above (hair/forehead) and 10 % below (chin space).
    4. Horizontally centre the face in the square.
    5. Resize to 300×300 with LANCZOS.
    """
    top, right, bottom, left = face_loc
    img_w, img_h = image.size

    face_w = right - left
    face_h = bottom - top
    face_cx = (left + right) / 2.0

    final_side = max(face_h / 0.70, face_w)

    crop_l = face_cx - final_side / 2.0
    crop_r = face_cx + final_side / 2.0
    crop_t = top - 0.20 * final_side
    crop_b = crop_t + final_side

    pad_l = int(max(0, -crop_l))
    pad_t = int(max(0, -crop_t))
    pad_r = int(max(0, crop_r - img_w))
    pad_b = int(max(0, crop_b - img_h))

    src_l = max(0, int(crop_l))
    src_t = max(0, int(crop_t))
    src_r = min(img_w, int(crop_r))
    src_b = min(img_h, int(crop_b))

    cropped = image.crop((src_l, src_t, src_r, src_b))

    if pad_l or pad_t or pad_r or pad_b:
        arr = np.array(cropped.convert("RGB"))

        def _edge_fill(pixels: np.ndarray) -> tuple[int, int, int]:
            return (0, 0, 0) if float(pixels.mean()) < 128 else (255, 255, 255)

        canvas = Image.new(
            "RGB",
            (cropped.width + pad_l + pad_r, cropped.height + pad_t + pad_b),
            (255, 255, 255),
        )
        canvas.paste(cropped, (pad_l, pad_t))
        draw = ImageDraw.Draw(canvas)
        cw, ch = canvas.size

        if pad_l:
            draw.rectangle([0, 0, pad_l - 1, ch - 1], fill=_edge_fill(arr[:, 0]))
        if pad_r:
            draw.rectangle([cw - pad_r, 0, cw - 1, ch - 1], fill=_edge_fill(arr[:, -1]))
        if pad_t:
            draw.rectangle([0, 0, cw - 1, pad_t - 1], fill=_edge_fill(arr[0]))
        if pad_b:
            draw.rectangle([0, ch - pad_b, cw - 1, ch - 1], fill=_edge_fill(arr[-1]))

        cropped = canvas

    is_low_res = cropped.width < _LOW_RES_THRESHOLD_PX or cropped.height < _LOW_RES_THRESHOLD_PX
    final = cropped.resize((CROP_TARGET_PX, CROP_TARGET_PX), Image.LANCZOS)
    return final, is_low_res


def _tight_crop(
    image: Image.Image,
    face_loc: Tuple[int, int, int, int],
) -> Tuple[Image.Image, bool]:
    """
    Tighter "faces-only" crop — forehead to chin, cheek to cheek.
    Face occupies ~88 % of the square height with only a small forehead buffer.
    """
    top, right, bottom, left = face_loc
    img_w, img_h = image.size

    face_w = right - left
    face_h = bottom - top
    face_cx = (left + right) / 2.0

    # Face fills 88 % of height; allow slight extra width for cheeks
    final_side = max(face_h / 0.88, face_w * 1.08)

    crop_l = face_cx - final_side / 2.0
    crop_r = face_cx + final_side / 2.0
    crop_t = top - 0.07 * final_side   # small forehead buffer
    crop_b = crop_t + final_side

    pad_l = int(max(0, -crop_l))
    pad_t = int(max(0, -crop_t))
    pad_r = int(max(0, crop_r - img_w))
    pad_b = int(max(0, crop_b - img_h))

    src_l = max(0, int(crop_l))
    src_t = max(0, int(crop_t))
    src_r = min(img_w, int(crop_r))
    src_b = min(img_h, int(crop_b))

    cropped = image.crop((src_l, src_t, src_r, src_b))

    if pad_l or pad_t or pad_r or pad_b:
        arr = np.array(cropped.convert("RGB"))

        def _edge_fill(pixels: np.ndarray) -> tuple[int, int, int]:
            return (0, 0, 0) if float(pixels.mean()) < 128 else (255, 255, 255)

        canvas = Image.new(
            "RGB",
            (cropped.width + pad_l + pad_r, cropped.height + pad_t + pad_b),
            (255, 255, 255),
        )
        canvas.paste(cropped, (pad_l, pad_t))
        draw = ImageDraw.Draw(canvas)
        cw, ch = canvas.size

        if pad_l:
            draw.rectangle([0, 0, pad_l - 1, ch - 1], fill=_edge_fill(arr[:, 0]))
        if pad_r:
            draw.rectangle([cw - pad_r, 0, cw - 1, ch - 1], fill=_edge_fill(arr[:, -1]))
        if pad_t:
            draw.rectangle([0, 0, cw - 1, pad_t - 1], fill=_edge_fill(arr[0]))
        if pad_b:
            draw.rectangle([0, ch - pad_b, cw - 1, ch - 1], fill=_edge_fill(arr[-1]))

        cropped = canvas

    is_low_res = cropped.width < _LOW_RES_THRESHOLD_PX or cropped.height < _LOW_RES_THRESHOLD_PX
    final = cropped.resize((CROP_TARGET_PX, CROP_TARGET_PX), Image.LANCZOS)
    return final, is_low_res
