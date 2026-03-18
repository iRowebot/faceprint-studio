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
    cell: float      # face cell size in inches (square)
    margin_h: float  # left & right margin in inches
    margin_v: float  # top & bottom margin in inches
    cols: int
    rows: int
    per_page: int


def _make_layout(label: str, cell: float, margin: float, margin_v: float | None = None) -> Layout:
    """Build a Layout. If margin_v is None, use margin for all sides."""
    mh = margin
    mv = margin if margin_v is None else margin_v
    cols = int((4.0 - 2 * mh) / cell)
    rows = int((6.0 - 2 * mv) / cell)
    return Layout(label=label, cell=cell, margin_h=mh, margin_v=mv,
                  cols=cols, rows=rows, per_page=cols * rows)


# Ordered smallest → largest face size
LAYOUTS: dict[str, Layout] = {}
for lbl, cell, margin in [
    ('0.5" × 0.5"  ·  0.25" margins  (7×11, 77/page)', 0.5, 0.25),
    ('0.75" × 0.75"  ·  1/8" L/R, 3/8" T/B  (5×7, 35/page)', 0.75, 1/8),
    ('1" × 1"  ·  0.5" margins  (3×5, 15/page)', 1.0, 0.50),
]:
    if cell == 0.75:
        LAYOUTS[lbl] = Layout(
            label=lbl, cell=0.75, margin_h=1/8, margin_v=3/8,
            cols=5, rows=7, per_page=35,
        )
    else:
        LAYOUTS[lbl] = _make_layout(lbl, cell, margin)

# Default layout (kept for backward compatibility)
_DEFAULT = next(iter(LAYOUTS.values()))
PAPER_W: float = 4.0
PAPER_H: float = 6.0
CELL: float    = _DEFAULT.cell
COLS: int      = _DEFAULT.cols
ROWS: int      = _DEFAULT.rows
PER_PAGE: int  = _DEFAULT.per_page


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flat_faces(persons: List[Person], use_tight: bool = False) -> List[Image.Image]:
    """Expand the person list into a flat sequence respecting quantities."""
    out: list[Image.Image] = []
    for p in persons:
        img = (p.face_tight_image or p.face_image) if use_tight else p.face_image
        out.extend([img] * p.quantity)
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
    use_tight: bool = False,
) -> Image.Image:
    """Rasterise one page at *dpi* for on-screen preview."""
    lo = layout or _DEFAULT
    pw, ph = int(PAPER_W * dpi), int(PAPER_H * dpi)
    mh = int(lo.margin_h * dpi)
    mv = int(lo.margin_v * dpi)
    c = int(lo.cell * dpi)

    img = Image.new("RGB", (pw, ph), "white")
    draw = ImageDraw.Draw(img)

    faces = _flat_faces(persons, use_tight)
    subset = faces[page * lo.per_page : (page + 1) * lo.per_page]

    for idx, face in enumerate(subset):
        col = idx % lo.cols
        row = idx // lo.cols
        img.paste(face.resize((c, c), Image.LANCZOS), (mh + col * c, mv + row * c))

    # Grid guides — uniform light gray lines only (no extra boundary rectangle)
    for ci in range(lo.cols + 1):
        x = mh + ci * c
        draw.line([(x, mv), (x, mv + lo.rows * c)], fill="#cccccc", width=1)
    for ri in range(lo.rows + 1):
        y = mv + ri * c
        draw.line([(mh, y), (mh + lo.cols * c, y)], fill="#cccccc", width=1)

    draw.rectangle([0, 0, pw - 1, ph - 1], outline="#aaaaaa", width=1)
    return img


# ── PDF export (print-ready, exact 4×6) ──────────────────────────────────────

def generate_pdf(
    persons: List[Person],
    path: str,
    layout: Layout | None = None,
    use_tight: bool = False,
) -> int:
    """Write a 300-DPI-ready PDF at exact 4″×6″.  Returns page count."""
    lo = layout or _DEFAULT
    pw = PAPER_W * inch
    ph = PAPER_H * inch
    mh = lo.margin_h * inch
    mv = lo.margin_v * inch
    c = lo.cell * inch

    cv = pdf_canvas.Canvas(path, pagesize=(pw, ph))
    faces = _flat_faces(persons, use_tight)
    pages = total_pages(persons, lo)

    for pg in range(pages):
        if pg:
            cv.showPage()
        subset = faces[pg * lo.per_page : (pg + 1) * lo.per_page]
        for idx, face in enumerate(subset):
            col = idx % lo.cols
            row = idx // lo.cols
            x = mh + col * c
            y = ph - mv - (row + 1) * c   # ReportLab Y runs bottom→top
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
    use_tight: bool = False,
) -> None:
    """Save a single page as a 300-DPI raster image."""
    lo = layout or _DEFAULT
    dpi = 300
    pw, ph = int(PAPER_W * dpi), int(PAPER_H * dpi)
    mh = int(lo.margin_h * dpi)
    mv = int(lo.margin_v * dpi)
    c = int(lo.cell * dpi)

    img = Image.new("RGB", (pw, ph), "white")
    faces = _flat_faces(persons, use_tight)
    subset = faces[page * lo.per_page : (page + 1) * lo.per_page]

    for idx, face in enumerate(subset):
        col = idx % lo.cols
        row = idx // lo.cols
        img.paste(face.resize((c, c), Image.LANCZOS), (mh + col * c, mv + row * c))

    img.save(path, dpi=(300, 300))
