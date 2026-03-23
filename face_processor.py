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

_MIN_DETECTION_CONFIDENCE = 0.35
_NMS_THRESHOLD = 0.22  # stricter internal NMS (lower = suppress more overlapping boxes)
_DETECTION_MAX_DIM = 640    # YuNet works best at this scale; larger dims cause partial-face detection

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
    # Disk: Documents/FacePrintLibrary/{file_stem}.png and {file_stem}_tight.png
    file_stem: str = ""
    original_image: str = ""
    date_added: str = ""
    # Set when added from Import tab; used to skip duplicate adds in one session
    source_face_id: str = ""
    # False after load from disk if PNGs match; True when new or crops changed — enables incremental save
    needs_png_write: bool = True


# ---------------------------------------------------------------------------
#  YuNet Face Detection — lazy init
# ---------------------------------------------------------------------------

_DETECTOR = None


def _box_iou(
    a: Tuple[int, int, int, int],
    b: Tuple[int, int, int, int],
) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return float(inter / ua) if ua > 0 else 0.0


def _fragments_same_face(
    a: Tuple[int, int, int, int],
    b: Tuple[int, int, int, int],
) -> bool:
    """YuNet often emits stacked strips on one face; IoU is tiny but columns align."""
    if _box_iou(a, b) >= 0.12:
        return True
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    aw, ah = ax2 - ax1, ay2 - ay1
    bw, bh = bx2 - bx1, by2 - by1
    if aw < 8 or ah < 8 or bw < 8 or bh < 8:
        return False
    overlap_w = max(0, min(ax2, bx2) - max(ax1, bx1))
    col_overlap = overlap_w / min(aw, bw)
    if col_overlap < 0.36:
        return False
    # Vertically stacked slices (forehead / eyes / chin)
    if ay2 <= by1 or by2 <= ay1:
        gap = (by1 - ay2) if ay2 <= by1 else (ay1 - by2)
        mh = max(ah, bh)
        if gap <= 0.55 * mh + 28:
            return True
    # Strong horizontal alignment with vertical overlap (partial IoU)
    if not (ay2 <= by1 or by2 <= ay1) and col_overlap >= 0.62:
        return True
    return False


def _merge_yunet_fragments(
    boxes_xyxy: List[Tuple[int, int, int, int]],
    scores: List[float],
) -> tuple[List[Tuple[int, int, int, int]], List[float]]:
    """Merge stacked / fragmented YuNet boxes into one bbox per real face."""
    n = len(boxes_xyxy)
    if n <= 1:
        return boxes_xyxy, scores

    parent = list(range(n))

    def find(i: int) -> int:
        if parent[i] != i:
            parent[i] = find(parent[i])
        return parent[i]

    def union(i: int, j: int) -> None:
        pi, pj = find(i), find(j)
        if pi != pj:
            parent[pi] = pj

    for i in range(n):
        for j in range(i + 1, n):
            if _fragments_same_face(boxes_xyxy[i], boxes_xyxy[j]):
                union(i, j)

    roots: dict[int, list[int]] = {}
    for i in range(n):
        r = find(i)
        roots.setdefault(r, []).append(i)

    out_boxes: List[Tuple[int, int, int, int]] = []
    out_scores: List[float] = []
    for idxs in roots.values():
        x1 = min(boxes_xyxy[i][0] for i in idxs)
        y1 = min(boxes_xyxy[i][1] for i in idxs)
        x2 = max(boxes_xyxy[i][2] for i in idxs)
        y2 = max(boxes_xyxy[i][3] for i in idxs)
        sc = max(scores[i] for i in idxs)
        if x2 - x1 >= 20 and y2 - y1 >= 20:
            out_boxes.append((x1, y1, x2, y2))
            out_scores.append(sc)

    return out_boxes, out_scores


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

    # Scale down for detection when the image is large (e.g. 12MP HEIC selfies).
    # Large images cause YuNet to fire multiple overlapping detections on the same face.
    # We detect on the downscaled copy and scale coordinates back for full-res cropping.
    longest = max(w, h)
    if longest > _DETECTION_MAX_DIM:
        scale = _DETECTION_MAX_DIM / longest
        det_w, det_h = int(w * scale), int(h * scale)
        det_bgr = cv2.resize(bgr_array, (det_w, det_h), interpolation=cv2.INTER_AREA)
    else:
        scale = 1.0
        det_w, det_h = w, h
        det_bgr = bgr_array

    detector = _get_detector()
    detector.setInputSize((det_w, det_h))
    _, raw_faces = detector.detect(det_bgr)

    annotated = pil_image.copy()
    draw = ImageDraw.Draw(annotated)
    stroke = max(2, min(w, h) // 250)

    faces: List[DetectedFace] = []

    if raw_faces is not None and len(raw_faces) > 0:
        # YuNet output: [x, y, w, h, ...landmarks..., score]
        boxes_xyxy: List[Tuple[int, int, int, int]] = []
        scores: List[float] = []
        for face_row in raw_faces:
            x1 = max(0, int(face_row[0] / scale))
            y1 = max(0, int(face_row[1] / scale))
            x2 = min(w, int((face_row[0] + face_row[2]) / scale))
            y2 = min(h, int((face_row[1] + face_row[3]) / scale))
            if x2 - x1 < 20 or y2 - y1 < 20:
                continue
            score = float(face_row[-1])
            boxes_xyxy.append((x1, y1, x2, y2))
            scores.append(score)

        merged_boxes, merged_scores = _merge_yunet_fragments(boxes_xyxy, scores)

        for (x1, y1, x2, y2), _sc in zip(merged_boxes, merged_scores):
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

    # Face (brow→chin) fills 97 % of height; barely 1 % above brows, 2 % below chin
    # Width just 1 % wider than the detected face to keep sides flush
    final_side = max(face_h / 0.97, face_w * 1.01)

    crop_l = face_cx - final_side / 2.0
    crop_r = face_cx + final_side / 2.0
    crop_t = top - 0.01 * final_side   # hairline of brow only
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
