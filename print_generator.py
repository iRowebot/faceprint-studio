"""PDF and high-resolution image export for FacePrint Studio.

Physical layout
    Paper:   4″ × 6″
    Margins: 0.25″ all sides  →  usable area 3.5″ × 5.5″
    Cell:    0.5″ × 0.5″     →  7 columns × 11 rows = 77 per page
"""

from __future__ import annotations

from io import BytesIO
from typing import List, TYPE_CHECKING

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader

if TYPE_CHECKING:
    from face_processor import Person

# ── Physical constants (inches) ──────────────────────────────────────────
PAPER_W: float = 4.0
PAPER_H: float = 6.0
MARGIN: float = 0.25
CELL: float = 0.5
COLS: int = int((PAPER_W - 2 * MARGIN) / CELL)   # 7
ROWS: int = int((PAPER_H - 2 * MARGIN) / CELL)   # 11
PER_PAGE: int = COLS * ROWS                       # 77


def _flat_faces(persons: List[Person]) -> List[Image.Image]:
    """Expand the person list into a flat sequence respecting quantities."""
    out: list[Image.Image] = []
    for p in persons:
        out.extend([p.face_image] * p.quantity)
    return out


def total_pages(persons: List[Person]) -> int:
    n = sum(p.quantity for p in persons)
    return max(1, (n + PER_PAGE - 1) // PER_PAGE)


# ── On-screen preview ────────────────────────────────────────────────────

def render_preview(
    persons: List[Person],
    page: int = 0,
    dpi: int = 150,
) -> Image.Image:
    """Rasterise one page of the 4×6 grid at *dpi* for on-screen preview."""
    pw, ph = int(PAPER_W * dpi), int(PAPER_H * dpi)
    m = int(MARGIN * dpi)
    c = int(CELL * dpi)

    img = Image.new("RGB", (pw, ph), "white")
    draw = ImageDraw.Draw(img)

    faces = _flat_faces(persons)
    subset = faces[page * PER_PAGE : (page + 1) * PER_PAGE]

    for idx, face in enumerate(subset):
        col = idx % COLS
        row = idx // COLS
        x, y = m + col * c, m + row * c
        img.paste(face.resize((c, c), Image.LANCZOS), (x, y))

    # Grid guides
    for ci in range(COLS + 1):
        x = m + ci * c
        draw.line([(x, m), (x, m + ROWS * c)], fill="#cccccc", width=1)
    for ri in range(ROWS + 1):
        y = m + ri * c
        draw.line([(m, y), (m + COLS * c, y)], fill="#cccccc", width=1)

    draw.rectangle([m, m, pw - m, ph - m], outline="#888888", width=1)
    draw.rectangle([0, 0, pw - 1, ph - 1], outline="#aaaaaa", width=1)
    return img


# ── PDF export (print-ready, exact 4×6) ──────────────────────────────────

def generate_pdf(persons: List[Person], path: str) -> int:
    """Write a 300-DPI-ready PDF at exact 4″×6″.  Returns page count."""
    pw = PAPER_W * inch
    ph = PAPER_H * inch
    m = MARGIN * inch
    c = CELL * inch

    cv = pdf_canvas.Canvas(path, pagesize=(pw, ph))
    faces = _flat_faces(persons)
    pages = total_pages(persons)

    for pg in range(pages):
        if pg:
            cv.showPage()
        subset = faces[pg * PER_PAGE : (pg + 1) * PER_PAGE]
        for idx, face in enumerate(subset):
            col = idx % COLS
            row = idx // COLS
            x = m + col * c
            y = ph - m - (row + 1) * c       # ReportLab Y runs bottom→top
            buf = BytesIO()
            face.save(buf, format="PNG")
            buf.seek(0)
            cv.drawImage(ImageReader(buf), x, y, width=c, height=c)

    cv.save()
    return pages


# ── High-res raster export ───────────────────────────────────────────────

def generate_high_res(
    persons: List[Person],
    path: str,
    page: int = 0,
) -> None:
    """Save a single page as a 300-DPI raster image."""
    dpi = 300
    pw, ph = int(PAPER_W * dpi), int(PAPER_H * dpi)
    m = int(MARGIN * dpi)
    c = int(CELL * dpi)

    img = Image.new("RGB", (pw, ph), "white")
    faces = _flat_faces(persons)
    subset = faces[page * PER_PAGE : (page + 1) * PER_PAGE]

    for idx, face in enumerate(subset):
        col = idx % COLS
        row = idx // COLS
        img.paste(face.resize((c, c), Image.LANCZOS), (m + col * c, m + row * c))

    img.save(path, dpi=(300, 300))
