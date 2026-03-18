"""PDF and high-resolution image export for FacePrint Studio."""

from __future__ import annotations

from io import BytesIO
from typing import List, NamedTuple, TYPE_CHECKING

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader

if TYPE_CHECKING:
    from face_processor import Person


# ── Layout descriptor ─────────────────────────────────────────────────────────

class Layout(NamedTuple):
    label: str
    cell: float    # face cell size in inches (square)
    margin: float  # margin on every side in inches
    cols: int
    rows: int
    per_page: int


def _make_layout(label: str, cell: float, margin: float) -> Layout:
    cols = int((4.0 - 2 * margin) / cell)
    rows = int((6.0 - 2 * margin) / cell)
    return Layout(label=label, cell=cell, margin=margin,
                  cols=cols, rows=rows, per_page=cols * rows)


LAYOUTS: dict[str, Layout] = {
    k: v for k, v in [
        (lbl, _make_layout(lbl, cell, margin))
        for lbl, cell, margin in [
            ('0.5" × 0.5"  ·  0.25" margins  (7×11, 77/page)', 0.5, 0.25),
            ('1" × 1"  ·  0.5" margins  (3×5, 15/page)',        1.0, 0.50),
        ]
    ]
}

# Default layout (kept for backward compatibility)
_DEFAULT = next(iter(LAYOUTS.values()))
PAPER_W: float = 4.0
PAPER_H: float = 6.0
MARGIN: float  = _DEFAULT.margin
CELL: float    = _DEFAULT.cell
COLS: int      = _DEFAULT.cols
ROWS: int      = _DEFAULT.rows
PER_PAGE: int  = _DEFAULT.per_page


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flat_faces(persons: List[Person]) -> List[Image.Image]:
    """Expand the person list into a flat sequence respecting quantities."""
    out: list[Image.Image] = []
    for p in persons:
        out.extend([p.face_image] * p.quantity)
    return out


def total_pages(persons: List[Person], layout: Layout | None = None) -> int:
    lo = layout or _DEFAULT
    n = sum(p.quantity for p in persons)
    return max(1, (n + lo.per_page - 1) // lo.per_page)


# ── On-screen preview ─────────────────────────────────────────────────────────

def render_preview(
    persons: List[Person],
    page: int = 0,
    dpi: int = 150,
    layout: Layout | None = None,
) -> Image.Image:
    """Rasterise one page at *dpi* for on-screen preview."""
    lo = layout or _DEFAULT
    pw, ph = int(PAPER_W * dpi), int(PAPER_H * dpi)
    m = int(lo.margin * dpi)
    c = int(lo.cell * dpi)

    img = Image.new("RGB", (pw, ph), "white")
    draw = ImageDraw.Draw(img)

    faces = _flat_faces(persons)
    subset = faces[page * lo.per_page : (page + 1) * lo.per_page]

    for idx, face in enumerate(subset):
        col = idx % lo.cols
        row = idx // lo.cols
        img.paste(face.resize((c, c), Image.LANCZOS), (m + col * c, m + row * c))

    # Grid guides
    for ci in range(lo.cols + 1):
        x = m + ci * c
        draw.line([(x, m), (x, m + lo.rows * c)], fill="#cccccc", width=1)
    for ri in range(lo.rows + 1):
        y = m + ri * c
        draw.line([(m, y), (m + lo.cols * c, y)], fill="#cccccc", width=1)

    draw.rectangle([m, m, pw - m, ph - m], outline="#888888", width=1)
    draw.rectangle([0, 0, pw - 1, ph - 1], outline="#aaaaaa", width=1)
    return img


# ── PDF export (print-ready, exact 4×6) ──────────────────────────────────────

def generate_pdf(
    persons: List[Person],
    path: str,
    layout: Layout | None = None,
) -> int:
    """Write a 300-DPI-ready PDF at exact 4″×6″.  Returns page count."""
    lo = layout or _DEFAULT
    pw = PAPER_W * inch
    ph = PAPER_H * inch
    m = lo.margin * inch
    c = lo.cell * inch

    cv = pdf_canvas.Canvas(path, pagesize=(pw, ph))
    faces = _flat_faces(persons)
    pages = total_pages(persons, lo)

    for pg in range(pages):
        if pg:
            cv.showPage()
        subset = faces[pg * lo.per_page : (pg + 1) * lo.per_page]
        for idx, face in enumerate(subset):
            col = idx % lo.cols
            row = idx // lo.cols
            x = m + col * c
            y = ph - m - (row + 1) * c   # ReportLab Y runs bottom→top
            buf = BytesIO()
            face.save(buf, format="PNG")
            buf.seek(0)
            cv.drawImage(ImageReader(buf), x, y, width=c, height=c)

    cv.save()
    return pages


# ── High-res raster export ────────────────────────────────────────────────────

def generate_high_res(
    persons: List[Person],
    path: str,
    page: int = 0,
    layout: Layout | None = None,
) -> None:
    """Save a single page as a 300-DPI raster image."""
    lo = layout or _DEFAULT
    dpi = 300
    pw, ph = int(PAPER_W * dpi), int(PAPER_H * dpi)
    m = int(lo.margin * dpi)
    c = int(lo.cell * dpi)

    img = Image.new("RGB", (pw, ph), "white")
    faces = _flat_faces(persons)
    subset = faces[page * lo.per_page : (page + 1) * lo.per_page]

    for idx, face in enumerate(subset):
        col = idx % lo.cols
        row = idx // lo.cols
        img.paste(face.resize((c, c), Image.LANCZOS), (m + col * c, m + row * c))

    img.save(path, dpi=(300, 300))
