"""FacePrint Studio — Main application window and all UI tabs."""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import winsound as _winsound
    def _soft_beep() -> None:
        _winsound.MessageBeep(_winsound.MB_ICONASTERISK)
except ImportError:
    def _soft_beep() -> None:  # type: ignore[misc]
        pass

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

# Small UI thumbnails: bilinear is much faster than Lanczos with negligible quality loss at ~96px.
_THUMB_RESAMPLE = getattr(Image, "Resampling", Image).BILINEAR

from face_processor import detect_faces_in_image, DetectedFace, Person
from print_generator import (
    render_preview,
    generate_pdf,
    generate_high_res,
    PER_PAGE,
    LAYOUTS,
)
from utils import pil_to_ctk, fit_image
from library_manager import (
    save_library,
    load_library,
    clear_library,
    ensure_library_dir,
    ensure_face_tight_loaded,
    load_disk_thumb,
    LIBRARY_DIR,
)
from version import __version__ as _APP_VERSION

# Drag-and-drop support — optional; app works fine without it
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

# Designer canvas: low-DPI preview while resizing (fast), full DPI after resize settles
_DES_PREVIEW_DPI_FAST = 72
_DES_PREVIEW_DPI_FULL = 150
_DES_RESIZE_DEBOUNCE_FAST_MS = 80
_DES_RESIZE_DEBOUNCE_FULL_MS = 280
# When total print slots exceed these, lower preview DPI to keep UI responsive
_PREVIEW_SLOTS_SOFT = 60
_PREVIEW_SLOTS_HARD = 120
_PREVIEW_DPI_SOFT_CAP = 110
_PREVIEW_DPI_HARD_CAP = 90

# Library grid: chunk small builds; virtualize only the viewport for large libraries
_LIB_CHUNK_SIZE = 24
_LIB_VIRTUAL_THRESHOLD = 40
_LIB_ROW_HEIGHT = 188

# ── Lightweight hover tooltip ─────────────────────────────────────────────────
class _Tooltip:
    """Show a small popup label when the mouse hovers over a widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Frame | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, event=None) -> None:
        if self._tip:
            return

        root = self._widget.winfo_toplevel()
        root.update_idletasks()

        # Use relative coords (widget minus root) so DPI scaling cancels out.
        rel_x = self._widget.winfo_rootx() - root.winfo_rootx()
        rel_y = self._widget.winfo_rooty() - root.winfo_rooty()
        w_h = tk.Misc.winfo_height(self._widget)

        self._tip = f = tk.Frame(root, relief="solid", borderwidth=1, bg="#ffffe0")
        lbl = tk.Label(
            f, text=self._text,
            justify="left",
            background="#ffffe0", foreground="#111111",
            font=("Segoe UI", 9), padx=6, pady=4,
            wraplength=420,
        )
        lbl.pack()
        f.update_idletasks()
        tip_w = f.winfo_reqwidth()
        root_w = root.winfo_width()

        x = rel_x
        y = rel_y + w_h + 4

        if x + tip_w > root_w - 8:
            x = root_w - tip_w - 8
        if x < 8:
            x = 8

        f.place(x=x, y=y)
        f.lift()

    def _hide(self, event=None) -> None:
        if self._tip:
            self._tip.place_forget()
            self._tip.destroy()
            self._tip = None

# Theme palette helpers
_SEL_BG = "#1a3d25"
_SEL_BORDER = "#22cc44"
_UNSEL_BG = "#2b2b2b"
_UNSEL_BORDER = "#444444"
_HOVER_SEL = "#254f30"
_HOVER_UNSEL = "#3a3a3a"


class App(ctk.CTk):
    """Root window for FacePrint Studio."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"FacePrint Studio  v{_APP_VERSION}")
        self.geometry("1300x840")
        self.minsize(1024, 700)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Set window icon (titlebar + taskbar)
        try:
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
            self.iconbitmap(str(base / "icon.ico"))
        except Exception:
            pass

        # Enable TkinterDnD on the existing Tk instance
        if _DND_AVAILABLE:
            try:
                TkinterDnD._require(self)
            except Exception:
                pass

        # ── Shared state ──
        self.detected_faces: list[DetectedFace] = []
        self.persons: list[Person] = []
        self.current_page: int = 0
        self._person_counter: int = 0
        self._annotated_images: list[tuple[Image.Image, str]] = []
        self._ann_index: int = 0
        self.print_queue: list[Person] = []  # faces queued for printing
        self._layout = next(iter(LAYOUTS.values()))  # active print layout
        self._print_btns: dict[str, tk.Button] = {}     # pid → library card button
        self._lib_cards: dict[str, tk.Frame] = {}      # pid → library card frame
        self._lib_thumb_cache: dict[str, ImageTk.PhotoImage] = {}        # pid → heads thumb
        self._lib_thumb_cache_tight: dict[str, ImageTk.PhotoImage] = {}  # pid → faces thumb
        self._lib_img_labels: dict[str, tk.Label] = {}  # pid → image label (for in-place swap)
        self._lib_crop_mode: str = "Heads"   # "Heads" or "Faces" for library display
        self._des_crop_mode: str = "Heads"   # "Heads" or "Faces" for designer/export
        self._lib_dirty: bool = True   # full rebuild needed when True
        self._lib_build_token: int = 0  # incremented to cancel stale staggered builds
        self._lib_building: bool = False  # suppress resize during build
        self._lib_use_virtual: bool = False  # only mount cards near the scroll viewport
        self._lib_viewport_job: str | None = None
        self._lib_chunk_job: str | None = None

        # Upload tab — per-face-card references (id → widget / label)
        self._face_cards: dict[str, ctk.CTkFrame] = {}
        self._face_status_lbls: dict[str, ctk.CTkLabel] = {}

        # Image-ref lists (prevent garbage collection of CTkImage objects)
        self._ann_ref: ctk.CTkImage | None = None
        self._grid_refs: list[ctk.CTkImage] = []
        self._lib_refs: list[ImageTk.PhotoImage] = []
        self._des_thumb_by_pid: dict[str, ctk.CTkImage] = {}  # GC refs; keyed for incremental remove
        self._des_row_by_pid: dict[str, ctk.CTkFrame] = {}  # designer quantity rows
        self._des_qty_entries: dict[str, ctk.CTkEntry] = {}

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_tabs()
        self._build_status_bar()
        self._load_saved_library()

        # Global Ctrl+V paste shortcut (works regardless of focused widget)
        self.bind_all("<Control-v>", lambda _e: self._paste_from_clipboard())

    def _load_saved_library(self) -> None:
        """Restore the library saved from the previous session."""
        ensure_library_dir()
        try:
            saved = load_library()
        except Exception:
            saved = []
        if not saved:
            return
        self.persons = saved
        self._person_counter = len(saved)
        self._lib_dirty = True
        # CTkTabview may not invoke `command` when switching tabs — do not rely on _on_tab_change
        # alone to build cards. Defer with after_idle so the window can paint first, then populate.
        self._set_status(f"Loaded {len(saved)} face(s) from saved library")

        def _deferred_refresh() -> None:
            self._refresh_library()

        self.after_idle(_deferred_refresh)

    def _on_close(self) -> None:
        """Cleanly shut down all background threads before destroying the window."""
        try:
            self.destroy()
        except Exception:
            pass
        import sys
        sys.exit(0)

    # ──────────────────────────────────────────────────────────────────────
    #  Header
    # ──────────────────────────────────────────────────────────────────────
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, height=46, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(
            hdr,
            text="  FacePrint Studio",
            font=("Segoe UI", 22, "bold"),
        ).pack(side="left", padx=10, pady=6)
        self._theme_sw = ctk.CTkSwitch(
            hdr, text="Light mode", command=self._toggle_theme, width=50,
        )
        self._theme_sw.pack(side="right", padx=16)

    def _toggle_theme(self) -> None:
        mode = "light" if self._theme_sw.get() else "dark"
        ctk.set_appearance_mode(mode)
        canvas_bg = "#dbdbdb" if mode == "light" else "#2b2b2b"
        self._des_canvas.configure(bg=canvas_bg)

    # ──────────────────────────────────────────────────────────────────────
    #  Status bar
    # ──────────────────────────────────────────────────────────────────────
    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, height=26, corner_radius=0)
        bar.pack(fill="x", side="bottom")
        self._status = ctk.StringVar(value="Ready")
        ctk.CTkLabel(
            bar, textvariable=self._status, anchor="w", font=("Segoe UI", 11),
        ).pack(side="left", padx=12)

    def _set_status(self, msg: str) -> None:
        self._status.set(msg)

    # ──────────────────────────────────────────────────────────────────────
    #  Tabs
    # ──────────────────────────────────────────────────────────────────────
    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self, command=self._on_tab_change)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        self._tab1 = self.tabs.add("Import & Select Faces")
        self._tab2 = self.tabs.add("My Faces Library")
        self._tab3 = self.tabs.add("Design & Export")

        self._build_upload_tab()
        self._build_library_tab()
        self._build_designer_tab()

    def _on_tab_change(self) -> None:
        t = self.tabs.get()
        if t == "My Faces Library":
            self._refresh_library()
        elif t == "Design & Export":
            # Avoid full _refresh_designer on every tab switch — that destroyed and
            # rebuilt every quantity row (O(n) CTk widgets, multi-second with large queues).
            # Rows are already built when adding from the library; only rebuild if out of sync.
            if not self.print_queue:
                self._refresh_designer()
            elif self._designer_rows_match_queue():
                def _sync_preview() -> None:
                    self.update_idletasks()
                    self._update_preview()

                self.after_idle(_sync_preview)
            else:
                self._refresh_designer()

    # ═════════════════════════════════════════════════════════════════════
    #  TAB 1 — Import & Select Faces
    # ═════════════════════════════════════════════════════════════════════
    def _build_upload_tab(self) -> None:
        tab = self._tab1

        # ── Toolbar ──
        bar = ctk.CTkFrame(tab)
        bar.pack(fill="x", pady=(0, 6))
        self._add_btn = ctk.CTkButton(
            bar, text="  Add Photo(s)", command=self._add_photos, width=150,
        )
        self._add_btn.pack(side="left", padx=6)
        ctk.CTkButton(
            bar, text="  Paste (Ctrl+V)", command=self._paste_from_clipboard, width=150,
            fg_color="gray40", hover_color="gray30",
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            bar, text="Clear All", width=90,
            fg_color="gray40", hover_color="gray30",
            command=self._clear_detections,
        ).pack(side="left", padx=4)
        self._progress = ctk.CTkProgressBar(bar, width=200)
        self._progress.pack(side="left", padx=14)
        self._progress.set(0)

        # ── Content split ──
        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        # ── Left — drop zone + annotated image ──
        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.grid_rowconfigure(1, weight=1)
        left.grid_rowconfigure(2, weight=0)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            left, text="Source Image", font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, pady=4)

        # Drop zone label (shows instructions; replaced by image after upload)
        self._drop_zone = ctk.CTkLabel(
            left,
            text=(
                "Drop photos here\n\nor use  Add Photo(s)  /  Paste (Ctrl+V)  above"
                if _DND_AVAILABLE
                else "Use  Add Photo(s)  or  Paste (Ctrl+V)  above to load photos"
            ),
            fg_color="#1e1e2e" if ctk.get_appearance_mode() == "dark" else "#e8e8f0",
            corner_radius=10,
            font=("Segoe UI", 13),
            text_color="gray60",
        )
        self._drop_zone.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)

        self._img_label = ctk.CTkLabel(left, text="")

        if _DND_AVAILABLE:
            try:
                self._drop_zone.drop_target_register(DND_FILES)
                self._drop_zone.dnd_bind("<<Drop>>", self._on_drop)
                left.drop_target_register(DND_FILES)
                left.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass

        # Image navigation bar (prev / filename+index / next)
        nav_bar = ctk.CTkFrame(left, fg_color="transparent")
        nav_bar.grid(row=2, column=0, pady=(0, 6))
        ctk.CTkButton(
            nav_bar, text="◀ Prev", width=80,
            fg_color="gray40", hover_color="gray30",
            command=self._prev_image,
        ).pack(side="left", padx=6)
        self._img_nav_lbl = ctk.CTkLabel(
            nav_bar, text="No image loaded", width=280,
            font=("Segoe UI", 11), text_color="gray60",
        )
        self._img_nav_lbl.pack(side="left", padx=6)
        ctk.CTkButton(
            nav_bar, text="Next ▶", width=80,
            fg_color="gray40", hover_color="gray30",
            command=self._next_image,
        ).pack(side="left", padx=6)

        # ── Right — detected face thumbnails ──
        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            right, text="Detected Faces — click to select",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, pady=4)

        self._face_scroll = ctk.CTkScrollableFrame(right, label_text="")
        self._face_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        # Pre-configure grid columns inside the scrollable frame
        _FACE_COLS = 3
        for ci in range(_FACE_COLS):
            self._face_scroll.grid_columnconfigure(ci, weight=1)

        # ── Bottom bar ──
        bot = ctk.CTkFrame(tab)
        bot.pack(fill="x", pady=(6, 0))
        self._sel_label = ctk.CTkLabel(bot, text="0 face(s) selected")
        self._sel_label.pack(side="left", padx=8)
        ctk.CTkButton(
            bot, text="Add Selected to Library  →",
            command=self._add_to_library, width=220,
        ).pack(side="right", padx=8)

    # ── Drag-and-drop handler ──

    def _on_drop(self, event: object) -> None:
        raw: str = getattr(event, "data", "")
        # tkinterdnd2 passes space-separated paths; braces wrap paths with spaces
        import re
        paths = re.findall(r"\{([^}]+)\}|(\S+)", raw)
        paths = [a or b for a, b in paths]
        image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".heic", ".heif"}
        paths = [p for p in paths if Path(p).suffix.lower() in image_exts]
        if paths:
            self._process_paths(tuple(paths))

    # ── Upload helpers ──

    def _add_photos(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select Photos",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp *.tiff *.heic *.heif")],
        )
        if not paths:
            return
        self._process_paths(paths)

    def _paste_from_clipboard(self) -> None:
        """Handle Ctrl+V — accepts a raw image or file paths from the clipboard."""
        from PIL import ImageGrab
        try:
            clip = ImageGrab.grabclipboard()
        except Exception:
            clip = None

        if clip is None:
            self._set_status("Nothing found on clipboard.")
            return

        image_exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".heic", ".heif"}

        # Clipboard contains file path(s) (e.g. files copied in Explorer)
        if isinstance(clip, list):
            paths = [p for p in clip if Path(str(p)).suffix.lower() in image_exts]
            if paths:
                self._process_paths(tuple(str(p) for p in paths))
            else:
                self._set_status("No supported image files on clipboard.")
            return

        # Clipboard contains a raw image (screenshot, copied image in browser, etc.)
        if isinstance(clip, Image.Image):
            try:
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.close()
                clip.save(tmp.name, format="PNG")
                self._process_paths((tmp.name,))
            except Exception as exc:
                self._set_status(f"Paste failed: {exc}")
            return

        self._set_status("Clipboard does not contain a supported image.")

    def _process_paths(self, paths: tuple[str, ...]) -> None:
        self._set_status(f"Processing {len(paths)} image(s)…")
        self._progress.set(0)
        self._add_btn.configure(state="disabled")
        threading.Thread(
            target=self._detect_worker, args=(paths,), daemon=True,
        ).start()

    def _detect_worker(self, paths: tuple[str, ...]) -> None:
        n = len(paths)
        new_faces: list[DetectedFace] = []
        new_anns: list[tuple[Image.Image, str]] = []

        errors: list[str] = []
        for i, p in enumerate(paths, 1):
            try:
                ann, faces = detect_faces_in_image(p)
                new_faces.extend(faces)
                new_anns.append((ann, Path(p).name))
                self.after(0, self._progress.set, i / n)
                self.after(
                    0, self._set_status,
                    f"[{i}/{n}] {Path(p).name}: {len(faces)} face(s)",
                )
            except Exception as exc:
                errors.append(f"{Path(p).name}: {exc}")
                self.after(0, self._progress.set, i / n)

        def _finish() -> None:
            self.detected_faces.extend(new_faces)
            self._annotated_images.extend(new_anns)
            if new_anns:
                self._show_annotated_at(len(self._annotated_images) - 1)
            self._refresh_face_grid()
            self._add_btn.configure(state="normal")
            if errors:
                from tkinter import messagebox as _mb
                _mb.showerror(
                    "Image Load Error",
                    "\n\n".join(errors),
                )
                self._set_status(f"Error loading {len(errors)} file(s) — see popup")
            else:
                total_found = len(new_faces)
                self._set_status(
                    f"Done — {total_found} face(s) detected in {n} image(s)"
                    if total_found
                    else f"No faces detected in {n} image(s)"
                )

        self.after(0, _finish)

    def _show_annotated_at(self, index: int) -> None:
        if not self._annotated_images:
            return
        index = max(0, min(index, len(self._annotated_images) - 1))
        self._ann_index = index
        pil_img, fname = self._annotated_images[index]
        self._drop_zone.grid_remove()
        self._img_label.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        fitted = fit_image(pil_img, 640, 500)
        self._ann_ref = pil_to_ctk(fitted)
        self._img_label.configure(image=self._ann_ref, text="")
        total = len(self._annotated_images)
        self._img_nav_lbl.configure(
            text=f"{fname}  ({index + 1} / {total})",
        )

    def _prev_image(self) -> None:
        if self._ann_index > 0:
            self._show_annotated_at(self._ann_index - 1)

    def _next_image(self) -> None:
        if self._ann_index < len(self._annotated_images) - 1:
            self._show_annotated_at(self._ann_index + 1)

    # ── Face thumbnail grid ──

    def _refresh_face_grid(self) -> None:
        for w in self._face_scroll.winfo_children():
            w.destroy()
        self._grid_refs.clear()
        self._face_cards.clear()
        self._face_status_lbls.clear()

        if not self.detected_faces:
            ctk.CTkLabel(
                self._face_scroll,
                text="No faces detected yet.",
                text_color="gray50",
            ).grid(row=0, column=0, padx=10, pady=20)
            self._sel_label.configure(text="0 face(s) selected")
            return

        cols = 3
        for ci in range(cols):
            self._face_scroll.grid_columnconfigure(ci, weight=1)

        for i, face in enumerate(self.detected_faces):
            r, c = divmod(i, cols)
            self._make_face_card(face, r, c)

        sel = sum(1 for f in self.detected_faces if f.selected)
        self._sel_label.configure(text=f"{sel} face(s) selected")

    def _make_face_card(self, face: DetectedFace, row: int, col: int) -> None:
        """Build one clickable face card and place it in the grid."""
        is_sel = face.selected
        card = ctk.CTkFrame(
            self._face_scroll,
            corner_radius=8,
            border_width=2,
            border_color=_SEL_BORDER if is_sel else _UNSEL_BORDER,
            fg_color=_SEL_BG if is_sel else _UNSEL_BG,
        )
        card.grid(row=row, column=col, padx=5, pady=5, sticky="n")
        self._face_cards[face.id] = card

        # Letterbox into a perfect square — never stretch
        thumb_pil = face.thumbnail.convert("RGB")
        padded = ImageOps.pad(thumb_pil, (90, 90), color=(43, 43, 43))
        ref = pil_to_ctk(padded, (90, 90))
        self._grid_refs.append(ref)

        img_lbl = ctk.CTkLabel(
            card, image=ref, text="",
            width=90, height=90,
            cursor="hand2",
        )
        img_lbl.pack(padx=6, pady=(6, 2))

        status_txt = "✓ Selected" if is_sel else "Click to select"
        status_col = _SEL_BORDER if is_sel else "gray60"
        status_lbl = ctk.CTkLabel(
            card,
            text=status_txt,
            text_color=status_col,
            font=("Segoe UI", 10, "bold" if is_sel else "normal"),
        )
        status_lbl.pack(pady=(0, 4))
        self._face_status_lbls[face.id] = status_lbl

        if face.is_low_res:
            ctk.CTkLabel(
                card, text="⚠ Low-res",
                text_color="#ffaa00", font=("Segoe UI", 9),
            ).pack(pady=(0, 4))

        # Bind click on every child widget of the card
        fid = face.id
        for widget in (card, img_lbl, status_lbl):
            widget.bind("<Button-1>", lambda _e, f=fid: self._toggle_face(f))
            widget.bind("<Enter>", lambda _e, c=card, s=is_sel: c.configure(
                fg_color=_HOVER_SEL if s else _HOVER_UNSEL
            ))
            widget.bind("<Leave>", lambda _e, c=card, s=is_sel: c.configure(
                fg_color=_SEL_BG if s else _UNSEL_BG
            ))

    def _toggle_face(self, fid: str) -> None:
        face = next((f for f in self.detected_faces if f.id == fid), None)
        if face is None:
            return
        face.selected = not face.selected
        is_sel = face.selected

        # Surgically update only the changed card — no full rebuild
        card = self._face_cards.get(fid)
        status_lbl = self._face_status_lbls.get(fid)
        if card and card.winfo_exists():
            card.configure(
                border_color=_SEL_BORDER if is_sel else _UNSEL_BORDER,
                fg_color=_SEL_BG if is_sel else _UNSEL_BG,
            )
        if status_lbl and status_lbl.winfo_exists():
            status_lbl.configure(
                text="✓ Selected" if is_sel else "Click to select",
                text_color=_SEL_BORDER if is_sel else "gray60",
                font=("Segoe UI", 10, "bold" if is_sel else "normal"),
            )

        sel = sum(1 for f in self.detected_faces if f.selected)
        self._sel_label.configure(text=f"{sel} face(s) selected")

    def _clear_detections(self) -> None:
        self.detected_faces.clear()
        self._annotated_images.clear()
        self._ann_index = 0
        self._ann_ref = None
        self._grid_refs.clear()
        self._face_cards.clear()
        self._face_status_lbls.clear()
        for w in self._face_scroll.winfo_children():
            w.destroy()
        self._img_label.grid_remove()
        self._drop_zone.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        self._img_nav_lbl.configure(text="No image loaded")
        self._sel_label.configure(text="0 face(s) selected")
        self._progress.set(0)
        self._set_status("Cleared all detections")

    def _add_to_library(self) -> None:
        sel = [f for f in self.detected_faces if f.selected]
        if not sel:
            messagebox.showinfo(
                "No Selection",
                "Click face thumbnails to select them first.",
            )
            return

        existing_face_ids = {p.source_face_id for p in self.persons if p.source_face_id}
        added = 0
        for f in sel:
            if f.id in existing_face_ids:
                continue
            self._person_counter += 1
            self.persons.insert(
                0,
                Person(
                    id=str(uuid.uuid4()),
                    name=f"Face {self._person_counter}",
                    face_image=f.cropped.copy(),
                    face_tight_image=f.tight_cropped.copy(),
                    is_low_res=f.is_low_res,
                    needs_png_write=True,
                    file_stem="",
                    original_image=f.source_path or "",
                    date_added=datetime.now().isoformat(timespec="seconds"),
                    source_face_id=f.id,
                ),
            )
            added += 1
            f.selected = False

        self._refresh_face_grid()
        self._lib_dirty = True
        self._refresh_library()
        self._auto_save()
        self._set_status(f"Added {added} face(s) to library")
        self.tabs.set("My Faces Library")

    # ═════════════════════════════════════════════════════════════════════
    #  TAB 2 — My Faces Library
    # ═════════════════════════════════════════════════════════════════════
    def _build_library_tab(self) -> None:
        tab = self._tab2
        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.pack(fill="x")
        ctk.CTkLabel(
            hdr, text="My Faces Library",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left", pady=8, padx=4)

        # Save status indicator
        self._save_status_lbl = ctk.CTkLabel(
            hdr, text="", text_color="#22cc44",
            font=("Segoe UI", 11),
        )
        self._save_status_lbl.pack(side="left", padx=8)

        # Right-side controls — info icon with tooltip showing save location
        _info_lbl = ctk.CTkLabel(
            hdr, text="ⓘ", width=28, height=28,
            font=("Segoe UI", 16),
            text_color="gray60",
            cursor="question_arrow",
        )
        _info_lbl.pack(side="right", padx=(0, 4), pady=6)
        _Tooltip(
            _info_lbl,
            f"Library saved to:\n{LIBRARY_DIR}",
        )

        self._lib_crop_seg = ctk.CTkSegmentedButton(
            hdr, values=["Heads", "Faces"],
            command=self._on_lib_crop_toggle,
            width=160, height=30,
        )
        self._lib_crop_seg.set("Heads")
        self._lib_crop_seg.pack(side="right", padx=(4, 0), pady=6)
        ctk.CTkButton(
            hdr, text="Sort A → Z", width=110,
            fg_color="gray40", hover_color="gray30",
            command=self._sort_library_alpha,
        ).pack(side="right", padx=4, pady=6)

        self._lib_scroll = ctk.CTkScrollableFrame(tab)
        self._lib_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self._lib_cols = 0
        self._lib_card_width = 160  # approximate card width including padding
        self._lib_scroll.bind("<Configure>", self._on_lib_resize)
        self._show_lib_empty()

    def _show_lib_empty(self) -> None:
        ctk.CTkLabel(
            self._lib_scroll,
            text="No faces yet — upload images and select faces first.",
            text_color="gray50",
        ).pack(pady=40)

    def _calc_lib_cols(self) -> int:
        """Return number of columns that fit the current scroll frame width."""
        try:
            w = self._lib_scroll.winfo_width()
        except Exception:
            w = 800
        if w < 50:
            w = 800
        return max(1, w // self._lib_card_width)

    def _on_lib_resize(self, event=None) -> None:
        """Reflow cards when the frame width changes (debounced — fullscreen fires many Configure events)."""
        if self._lib_building:
            return
        if not self._lib_cards:
            return
        if hasattr(self, "_lib_resize_job"):
            self.after_cancel(self._lib_resize_job)
        self._lib_resize_job = self.after(120, self._apply_lib_resize)

    def _apply_lib_resize(self) -> None:
        if self._lib_building:
            return
        new_cols = self._calc_lib_cols()
        if new_cols != self._lib_cols and self._lib_cards:
            self._lib_cols = new_cols
            if self._lib_use_virtual:
                self._lib_reconfigure_virtual_rows()
                self._lib_sync_virtual_cards()
            else:
                self._regrid_lib_cards()

    def _regrid_lib_cards(self) -> None:
        """Re-grid all existing cards into current column count — no rebuilds."""
        cols = self._lib_cols or self._calc_lib_cols()
        for ci in range(cols):
            self._lib_scroll.grid_columnconfigure(ci, weight=1)
        if self._lib_use_virtual:
            self._lib_reconfigure_virtual_rows()
            self._lib_sync_virtual_cards()
            self.after(50, self._fix_lib_scroll_region)
            return
        for i, p in enumerate(self.persons):
            card = self._lib_cards.get(p.id)
            if card and card.winfo_exists():
                r, c = divmod(i, cols)
                card.grid(row=r, column=c, padx=4, pady=4, sticky="n")
        self.after(50, self._fix_lib_scroll_region)

    def _refresh_library(self) -> None:
        if not self._lib_dirty and self._lib_cards:
            return

        self._lib_dirty = False
        self._lib_build_token += 1
        self._lib_building = True
        self._lib_use_virtual = False

        for w in self._lib_scroll.winfo_children():
            w.destroy()
        self._lib_refs.clear()
        self._print_btns.clear()
        self._lib_cards.clear()
        self._lib_img_labels.clear()

        if not self.persons:
            self._lib_building = False
            self._show_lib_empty()
            return

        self._lib_cols = self._calc_lib_cols()
        cols = self._lib_cols
        for ci in range(cols):
            self._lib_scroll.grid_columnconfigure(ci, weight=1)

        n = len(self.persons)
        if n > _LIB_VIRTUAL_THRESHOLD:
            self._lib_use_virtual = True
            self._refresh_library_virtual()
            return

        self._lib_chunk_token = self._lib_build_token
        if n <= _LIB_CHUNK_SIZE:
            for i, p in enumerate(self.persons):
                self._make_lib_card(p, i, cols)
            self._lib_finish_build()
        else:
            self._lib_chunk_idx = 0
            self._lib_chunk_cols = cols
            self._lib_schedule_next_chunk()

    def _lib_schedule_next_chunk(self) -> None:
        if self._lib_chunk_token != self._lib_build_token:
            return
        cols = self._lib_chunk_cols
        end = min(self._lib_chunk_idx + _LIB_CHUNK_SIZE, len(self.persons))
        for i in range(self._lib_chunk_idx, end):
            self._make_lib_card(self.persons[i], i, cols)
        self._lib_chunk_idx = end
        if self._lib_chunk_idx < len(self.persons):
            self.after(1, self._lib_schedule_next_chunk)
        else:
            self._lib_finish_build()

    def _lib_finish_build(self) -> None:
        self._lib_building = False
        self.after(50, self._fix_lib_scroll_region)

    def _refresh_library_virtual(self) -> None:
        n = len(self.persons)
        cols = self._lib_cols
        n_rows = (n + cols - 1) // cols
        for r in range(n_rows):
            self._lib_scroll.grid_rowconfigure(r, minsize=_LIB_ROW_HEIGHT)
        self._lib_bind_viewport_once()
        self._lib_sync_virtual_cards()
        self._lib_finish_build()

    def _lib_reconfigure_virtual_rows(self) -> None:
        n = len(self.persons)
        cols = self._lib_cols or self._calc_lib_cols()
        n_rows = (n + cols - 1) // cols if n else 0
        for r in range(n_rows):
            self._lib_scroll.grid_rowconfigure(r, minsize=_LIB_ROW_HEIGHT)

    def _lib_bind_viewport_once(self) -> None:
        if getattr(self, "_lib_viewport_hooked", False):
            return
        self._lib_viewport_hooked = True
        try:
            canvas = self._lib_scroll._parent_canvas
        except Exception:
            return

        def _on_cfg(_e=None):
            self._lib_on_viewport_event()

        canvas.bind("<Configure>", _on_cfg, add="+")

        def _wheel(_e=None):
            self._lib_on_viewport_event()

        canvas.bind("<MouseWheel>", _wheel, add="+")
        canvas.bind("<Button-4>", _wheel, add="+")
        canvas.bind("<Button-5>", _wheel, add="+")

    def _lib_on_viewport_event(self, event=None) -> None:
        if not self._lib_use_virtual:
            return
        if self._lib_viewport_job is not None:
            try:
                self.after_cancel(self._lib_viewport_job)
            except Exception:
                pass
        self._lib_viewport_job = self.after(60, self._lib_sync_virtual_cards)

    def _lib_sync_virtual_cards(self) -> None:
        self._lib_viewport_job = None
        if not self.persons or not self._lib_use_virtual:
            return
        n = len(self.persons)
        cols = self._lib_cols or self._calc_lib_cols()
        n_rows = (n + cols - 1) // cols
        try:
            canvas = self._lib_scroll._parent_canvas
            top, bottom = canvas.yview()
            content_h = max(1, n_rows * _LIB_ROW_HEIGHT)
            y0 = top * content_h
            y1 = bottom * content_h
        except Exception:
            content_h = max(1, n_rows * _LIB_ROW_HEIGHT)
            y0, y1 = 0.0, float(content_h)

        r0 = max(0, int(y0 // _LIB_ROW_HEIGHT) - 1)
        r1 = min(n_rows - 1, max(0, int((y1 + _LIB_ROW_HEIGHT - 1) // _LIB_ROW_HEIGHT) + 1))
        wanted: set[int] = set()
        for r in range(r0, r1 + 1):
            for c in range(cols):
                i = r * cols + c
                if i < n:
                    wanted.add(i)

        idx_by_pid = {p.id: i for i, p in enumerate(self.persons)}
        for pid in list(self._lib_cards.keys()):
            i = idx_by_pid.get(pid)
            if i is None or i not in wanted:
                card = self._lib_cards.pop(pid, None)
                self._print_btns.pop(pid, None)
                self._lib_img_labels.pop(pid, None)
                if card and card.winfo_exists():
                    card.destroy()

        for i in sorted(wanted):
            p = self.persons[i]
            pid = p.id
            if pid in self._lib_cards:
                card = self._lib_cards[pid]
                if card.winfo_exists():
                    r, c = divmod(i, cols)
                    card.grid(row=r, column=c, padx=4, pady=4, sticky="n")
            else:
                self._make_lib_card(p, i, cols)

        self.after(50, self._fix_lib_scroll_region)

    def _fix_lib_scroll_region(self) -> None:
        """Force CTkScrollableFrame to recalculate its scroll region.

        CTkScrollableFrame relies on a <Configure> event on its internal frame
        to update the canvas scrollregion.  When children are native tk.Frame
        widgets placed via grid(), that event can be missed.  Calling
        update_idletasks() flushes pending geometry, then we poke the canvas
        directly so the scrollbar reflects the real content height.
        """
        try:
            self._lib_scroll.update_idletasks()
            canvas = self._lib_scroll._parent_canvas  # CTk internal attribute
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass  # Gracefully ignore if CTk internals change in future versions

    def _make_lib_card(self, p: "Person", index: int, cols: int = 0) -> None:
        if cols <= 0:
            cols = self._lib_cols or self._calc_lib_cols()
        r, c = divmod(index, cols)

        is_dark = ctk.get_appearance_mode().lower() == "dark"
        card_bg  = "#2b2b2b" if is_dark else "#ebebeb"
        card_border = "#555555" if is_dark else "#aaaaaa"
        btn_bg   = "#1f538d"
        del_bg   = "#993333"
        btn_fg   = "white"

        card = tk.Frame(
            self._lib_scroll,
            bg=card_bg,
            highlightbackground=card_border,
            highlightthickness=1,
        )
        card.grid(row=r, column=c, padx=4, pady=4, sticky="n")
        self._lib_cards[p.id] = card

        entry_bg = "#3b3b3b" if is_dark else "#f0f0f0"
        entry_fg = "#e0e0e0" if is_dark else "#111111"

        use_tight = self._lib_crop_mode == "Faces"
        cache = self._lib_thumb_cache_tight if use_tight else self._lib_thumb_cache
        if p.id not in cache:
            disk = load_disk_thumb(p.file_stem, use_tight)
            if disk is not None:
                cache[p.id] = ImageTk.PhotoImage(disk)
            else:
                if use_tight:
                    ensure_face_tight_loaded(p)
                src = (p.face_tight_image or p.face_image) if use_tight else p.face_image
                thumb = src.convert("RGB")
                thumb.thumbnail((96, 96), _THUMB_RESAMPLE)
                cache[p.id] = ImageTk.PhotoImage(thumb)
        ref = cache[p.id]
        self._lib_refs.append(ref)

        img_lbl = tk.Label(card, image=ref, bg=card_bg, cursor="hand2")
        img_lbl.pack(padx=8, pady=(8, 3))
        self._lib_img_labels[p.id] = img_lbl

        if p.is_low_res:
            tk.Label(
                card, text="\u26a0 Low-res",
                fg="#ffaa00", bg=card_bg, font=("Segoe UI", 8),
            ).pack()

        name_var = tk.StringVar(value=p.name)
        tk.Entry(
            card, textvariable=name_var, width=18, justify="center",
            font=("Segoe UI", 10), bg=entry_bg, fg=entry_fg,
            insertbackground=entry_fg, relief="flat", bd=1,
            highlightthickness=1,
            highlightbackground=card_border, highlightcolor="#1f538d",
        ).pack(padx=6, pady=(0, 4), fill="x")
        name_var.trace_add(
            "write",
            lambda *_, pid=p.id, sv=name_var: self._rename(pid, sv.get()),
        )

        btns = tk.Frame(card, bg=card_bg)
        btns.pack(pady=(0, 3))
        for txt, bg, cmd in [
            ("\u25b2", btn_bg, lambda pid=p.id: self._move(pid, -1)),
            ("\u25bc", btn_bg, lambda pid=p.id: self._move(pid,  1)),
            ("\u2715", del_bg, lambda pid=p.id: self._delete(pid)),
        ]:
            tk.Button(
                btns, text=txt, width=2, padx=3, pady=2,
                bg=bg, fg=btn_fg, relief="flat", borderwidth=0,
                activebackground=card_border, activeforeground=btn_fg,
                cursor="hand2", command=cmd, font=("Segoe UI", 9),
            ).pack(side="left", padx=2)

        in_queue = any(q.id == p.id for q in self.print_queue)
        pq_btn = tk.Button(
            card,
            text="\u2713 In Queue" if in_queue else "+ Add to Queue",
            bg="#1a4a28" if in_queue else "#1f538d",
            fg="white", relief="flat", bd=0,
            activebackground="#993333" if in_queue else "#1a4a70",
            activeforeground="white",
            font=("Segoe UI", 9, "bold" if in_queue else "normal"),
            cursor="hand2", pady=4,
            command=lambda pid=p.id: (
                self._remove_from_print_queue(pid) if any(q.id == pid for q in self.print_queue)
                else self._add_to_print_queue(pid)
            ),
        )
        pq_btn.pack(padx=6, pady=(0, 8), fill="x")
        self._print_btns[p.id] = pq_btn

    # ── Library persistence helpers ──────────────────────────────────────

    def _auto_save(self) -> None:
        """Save library to disk and briefly show a confirmation."""
        try:
            save_library(self.persons)
            self._save_status_lbl.configure(text="✓ Saved")
            self.after(2500, lambda: self._save_status_lbl.configure(text=""))
        except Exception as exc:
            self._save_status_lbl.configure(
                text=f"⚠ Save failed: {exc}", text_color="#ff6644",
            )

    def _sort_library_alpha(self) -> None:
        self.persons.sort(key=lambda p: p.name.lower())
        if self._lib_cards:
            self._regrid_lib_cards()
        else:
            self._lib_dirty = True
            self._refresh_library()
        self._auto_save()

    def _clear_saved_library(self) -> None:
        if not messagebox.askyesno(
            "Clear Saved Library",
            "This will delete the saved library from disk.\n"
            "Faces currently in the session will NOT be removed.\n\n"
            "Continue?",
        ):
            return
        try:
            clear_library()
            self._save_status_lbl.configure(
                text="Library cleared from disk", text_color="#ffaa00",
            )
            self.after(3000, lambda: self._save_status_lbl.configure(text=""))
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    # ────────────────────────────────────────────────────────────────────

    def _rename(self, pid: str, name: str) -> None:
        for p in self.persons:
            if p.id == pid:
                p.name = name
        # Keep print queue in sync with library (same person id)
        self.print_queue = [
            dataclasses.replace(q, name=name) if q.id == pid else q
            for q in self.print_queue
        ]
        # Debounced designer refresh so quantity row labels update without rebuilding on every keypress
        if hasattr(self, "_rename_des_job"):
            self.after_cancel(self._rename_des_job)
        self._rename_des_job = self.after(400, self._refresh_designer)
        # Debounce: save 1.5 s after the user stops typing
        if hasattr(self, "_rename_save_job"):
            self.after_cancel(self._rename_save_job)
        self._rename_save_job = self.after(1500, self._auto_save)

    def _move(self, pid: str, delta: int) -> None:
        idx = next((i for i, p in enumerate(self.persons) if p.id == pid), None)
        if idx is None:
            return
        j = idx + delta
        if not (0 <= j < len(self.persons)):
            return

        self.persons[idx], self.persons[j] = self.persons[j], self.persons[idx]

        if self._lib_use_virtual:
            self._lib_reconfigure_virtual_rows()
            self._lib_sync_virtual_cards()
        else:
            cols = self._lib_cols or self._calc_lib_cols()
            for pos, person in ((idx, self.persons[idx]), (j, self.persons[j])):
                card = self._lib_cards.get(person.id)
                if card and card.winfo_exists():
                    row, col = divmod(pos, cols)
                    card.grid(row=row, column=col, padx=4, pady=4, sticky="n")

        self._auto_save()

    def _delete(self, pid: str) -> None:
        if not messagebox.askyesno(
            "Remove Face",
            "Remove this face from the library?",
            icon="warning",
        ):
            return

        self._lib_thumb_cache.pop(pid, None)
        self._lib_thumb_cache_tight.pop(pid, None)
        self._lib_img_labels.pop(pid, None)

        # Destroy just this card — no full grid rebuild
        card = self._lib_cards.pop(pid, None)
        if card and card.winfo_exists():
            card.destroy()

        self.persons = [p for p in self.persons if p.id != pid]
        self.print_queue = [p for p in self.print_queue if p.id != pid]
        self._print_btns.pop(pid, None)

        self._regrid_lib_cards()

        if not self.persons:
            self._show_lib_empty()

        self._refresh_designer()
        self._auto_save()
        self._set_status("Face removed from library")

    def _add_to_print_queue(self, pid: str) -> None:
        if any(p.id == pid for p in self.print_queue):
            return
        total_used = sum(p.quantity for p in self.print_queue)
        if total_used >= self._layout.per_page:
            _soft_beep()
            return
        for p in self.persons:
            if p.id == pid:
                self.print_queue.append(dataclasses.replace(p, quantity=1))
                break
        self._update_print_btn(pid, in_queue=True)
        # First item: full refresh (clears empty placeholder). Further adds: one row only.
        if len(self.print_queue) == 1:
            self._refresh_designer()
        else:
            self._append_designer_row(self.print_queue[-1])
            self._update_preview()

    def _remove_from_print_queue(self, pid: str) -> None:
        self.print_queue = [p for p in self.print_queue if p.id != pid]
        self._update_print_btn(pid, in_queue=False)
        if not self.print_queue:
            self._refresh_designer()
        else:
            self._remove_designer_row(pid)
            self._update_preview()

    def _update_print_btn(self, pid: str, *, in_queue: bool) -> None:
        """Update just the one library card button — no full grid rebuild."""
        btn = self._print_btns.get(pid)
        if btn is None or not btn.winfo_exists():
            return
        if in_queue:
            btn.configure(
                text="\u2713 In Queue",
                bg="#1a4a28", activebackground="#993333",
                font=("Segoe UI", 9, "bold"),
                command=lambda: self._remove_from_print_queue(pid),
            )
        else:
            btn.configure(
                text="+ Add to Queue",
                bg="#1f538d", activebackground="#1a4a70",
                font=("Segoe UI", 9),
                command=lambda: self._add_to_print_queue(pid),
            )

    # ═════════════════════════════════════════════════════════════════════
    #  TAB 3 — Design & Export
    # ═════════════════════════════════════════════════════════════════════
    def _build_designer_tab(self) -> None:
        tab = self._tab3

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1, minsize=280)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ctk.CTkLabel(
            left, text="Faces & Quantities",
            font=("Segoe UI", 13, "bold"),
        ).pack(pady=(6, 2))

        # Layout size picker
        ctk.CTkLabel(
            left, text="Face size:", font=("Segoe UI", 12),
        ).pack(pady=(2, 0))
        self._layout_menu = ctk.CTkOptionMenu(
            left,
            values=list(LAYOUTS.keys()),
            command=self._on_layout_change,
            height=34,
            font=("Segoe UI", 12),
            dropdown_font=("Segoe UI", 12),
        )
        self._layout_menu.set(list(LAYOUTS.keys())[0])
        self._layout_menu.pack(fill="x", padx=8, pady=(2, 2))

        ctk.CTkLabel(
            left, text="Crop mode:", font=("Segoe UI", 12),
        ).pack(pady=(4, 0))
        self._des_crop_seg = ctk.CTkSegmentedButton(
            left, values=["Heads", "Faces"],
            command=self._on_des_crop_toggle,
            height=34,
            font=("Segoe UI", 12),
        )
        self._des_crop_seg.set("Heads")
        self._des_crop_seg.pack(fill="x", padx=8, pady=(2, 6))

        self._des_scroll = ctk.CTkScrollableFrame(left)
        self._des_scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # Equalize button sits directly below the quantity list
        ctk.CTkButton(
            left, text="Equalize", width=120,
            fg_color="gray40", hover_color="gray30",
            command=self._equalize_quantities,
        ).pack(pady=(2, 4))

        info = ctk.CTkFrame(left, fg_color="transparent")
        info.pack(fill="x", padx=8, pady=4)
        self._total_lbl = ctk.CTkLabel(info, text="Total: 0 faces · 0 pages")
        self._total_lbl.pack()
        self._layout_info_lbl = ctk.CTkLabel(
            info, text=self._layout_info_text(),
            text_color="gray50", font=("Segoe UI", 10), justify="center",
        )
        self._layout_info_lbl.pack(pady=(2, 0))

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ctk.CTkLabel(
            right, text="Print Preview (4\" × 6\")",
            font=("Segoe UI", 13, "bold"),
        ).pack(pady=6)

        self._des_canvas = tk.Canvas(
            right, highlightthickness=0, borderwidth=0, bg="#2b2b2b",
        )
        self._des_canvas.pack(fill="both", expand=True, padx=8, pady=(4, 4))
        self._des_canvas.bind("<Configure>", self._on_designer_resize)

        # Export buttons below the preview
        exp_btns = ctk.CTkFrame(right, fg_color="transparent")
        exp_btns.pack(fill="x", padx=8, pady=(8, 8))
        ctk.CTkButton(
            exp_btns, text="  Generate Printable PDF",
            command=self._export_pdf, height=34,
            font=("Segoe UI", 12),
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(
            exp_btns, text="  Export High-Res Image",
            command=self._export_img, height=34,
            font=("Segoe UI", 12),
            fg_color="gray40", hover_color="gray30",
        ).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _layout_info_text(self) -> str:
        lo = self._layout
        def _m(v: float) -> str:
            if abs(v - 1/8) < 0.001: return "1/8"
            if abs(v - 1/6) < 0.001: return "1/6"
            if abs(v - 3/8) < 0.001: return "3/8"
            return f"{v}\""
        if lo.margin_h == lo.margin_v:
            margin_txt = f"Margins {_m(lo.margin_h)}\""
        else:
            margin_txt = f"L/R {_m(lo.margin_h)}\", T/B {_m(lo.margin_v)}\""
        return (
            f"Paper 4\"×6\"  ·  {margin_txt}\n"
            f"Cells {lo.cell}\"×{lo.cell}\"  ·  {lo.cols} cols × {lo.rows} rows\n"
            f"{lo.per_page} faces per page"
        )

    def _on_layout_change(self, label: str) -> None:
        self._layout = LAYOUTS[label]
        if hasattr(self, "_layout_info_lbl"):
            self._layout_info_lbl.configure(text=self._layout_info_text())
        self._update_preview()

    def _on_lib_crop_toggle(self, mode: str) -> None:
        self._lib_crop_mode = mode
        # Fast path: swap thumbnails in-place without destroying/rebuilding cards
        if self._lib_img_labels:
            use_tight = mode == "Faces"
            cache = self._lib_thumb_cache_tight if use_tight else self._lib_thumb_cache
            for p in self.persons:
                if p.id not in cache:
                    disk = load_disk_thumb(p.file_stem, use_tight)
                    if disk is not None:
                        cache[p.id] = ImageTk.PhotoImage(disk)
                    else:
                        if use_tight:
                            ensure_face_tight_loaded(p)
                        src = (p.face_tight_image or p.face_image) if use_tight else p.face_image
                        thumb = src.convert("RGB")
                        thumb.thumbnail((96, 96), _THUMB_RESAMPLE)
                        cache[p.id] = ImageTk.PhotoImage(thumb)
                lbl = self._lib_img_labels.get(p.id)
                if lbl and lbl.winfo_exists():
                    lbl.configure(image=cache[p.id])
            return
        self._lib_dirty = True
        self._refresh_library()

    def _on_des_crop_toggle(self, mode: str) -> None:
        self._des_crop_mode = mode
        self._update_preview()

    def _create_designer_row(self, p: Person) -> ctk.CTkFrame:
        """Build one Design & Export quantity row (not packed)."""
        row = ctk.CTkFrame(self._des_scroll, corner_radius=6)

        thumb = p.face_image.convert("RGB")
        thumb.thumbnail((44, 44), _THUMB_RESAMPLE)
        ref = pil_to_ctk(thumb, (44, 44))
        self._des_thumb_by_pid[p.id] = ref
        ctk.CTkLabel(row, image=ref, text="").pack(side="left", padx=(6, 4))

        ctk.CTkLabel(
            row, text=p.name, width=90, anchor="w",
        ).pack(side="left", padx=4)

        qf = ctk.CTkFrame(row, fg_color="transparent")
        qf.pack(side="right", padx=4)

        ctk.CTkButton(
            qf, text="✕", width=28, height=28,
            fg_color="#993333", hover_color="#cc4444",
            command=lambda pid=p.id: self._remove_from_print_queue(pid),
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            qf, text="−", width=30, height=28,
            command=lambda pid=p.id: self._qty_delta(pid, -1),
        ).pack(side="left")

        qty_entry = ctk.CTkEntry(qf, width=46, height=28, justify="center")
        qty_entry.insert(0, str(p.quantity))
        qty_entry.pack(side="left", padx=3)
        qty_entry.bind(
            "<Return>",
            lambda e, pid=p.id, ent=qty_entry: self._qty_set(pid, ent),
        )
        qty_entry.bind(
            "<FocusOut>",
            lambda e, pid=p.id, ent=qty_entry: self._qty_set(pid, ent),
        )
        self._des_qty_entries[p.id] = qty_entry

        ctk.CTkButton(
            qf, text="+", width=30, height=28,
            command=lambda pid=p.id: self._qty_delta(pid, 1),
        ).pack(side="left")

        return row

    def _designer_rows_match_queue(self) -> bool:
        """True if the Design tab quantity list already matches print_queue (skip full rebuild)."""
        if len(self.print_queue) != len(self._des_row_by_pid):
            return False
        for p in self.print_queue:
            row = self._des_row_by_pid.get(p.id)
            if row is None or not row.winfo_exists():
                return False
        return True

    def _append_designer_row(self, p: Person) -> None:
        """Add a single row without rebuilding the whole designer list."""
        row = self._create_designer_row(p)
        row.pack(fill="x", padx=4, pady=3)
        self._des_row_by_pid[p.id] = row

    def _remove_designer_row(self, pid: str) -> None:
        """Remove one row and its refs; caller updates print_queue."""
        row = self._des_row_by_pid.pop(pid, None)
        if row is not None and row.winfo_exists():
            row.destroy()
        self._des_thumb_by_pid.pop(pid, None)
        self._des_qty_entries.pop(pid, None)

    def _refresh_designer(self) -> None:
        for w in self._des_scroll.winfo_children():
            w.destroy()
        self._des_thumb_by_pid.clear()
        self._des_row_by_pid.clear()
        self._des_qty_entries.clear()

        if not self.print_queue:
            ctk.CTkLabel(
                self._des_scroll,
                text="Go to My Faces Library and click\n\"+ Add to Queue\" on each face.",
                text_color="gray50", justify="center",
            ).pack(pady=30)
            self._update_preview()
            return

        for p in self.print_queue:
            row = self._create_designer_row(p)
            row.pack(fill="x", padx=4, pady=3)
            self._des_row_by_pid[p.id] = row

        self._update_preview()

    def _qty_delta(self, pid: str, delta: int) -> None:
        per_page = self._layout.per_page
        for p in self.print_queue:
            if p.id == pid:
                new_qty = max(0, p.quantity + delta)
                if delta > 0:
                    # Check if the increment would push the total over the limit
                    other_total = sum(q.quantity for q in self.print_queue if q.id != pid)
                    if other_total + new_qty > per_page:
                        _soft_beep()
                        new_qty = max(0, per_page - other_total)
                        if new_qty == p.quantity:
                            return
                p.quantity = new_qty
                break
        ent = self._des_qty_entries.get(pid)
        if ent and ent.winfo_exists():
            ent.delete(0, "end")
            ent.insert(0, str(next((p.quantity for p in self.print_queue if p.id == pid), 0)))
        self._update_preview()

    def _qty_set(self, pid: str, entry_widget: ctk.CTkEntry) -> None:
        try:
            v = max(0, int(entry_widget.get()))
        except Exception:
            return
        per_page = self._layout.per_page
        for p in self.print_queue:
            if p.id == pid:
                if p.quantity == v:
                    return
                other_total = sum(q.quantity for q in self.print_queue if q.id != pid)
                if other_total + v > per_page:
                    _soft_beep()
                    v = max(0, per_page - other_total)
                    entry_widget.delete(0, "end")
                    entry_widget.insert(0, str(v))
                p.quantity = v
                break
        self._update_preview()

    def _equalize_quantities(self) -> None:
        """Set each face's quantity to fill the grid evenly (floor(77/n) each)."""
        n = len(self.print_queue)
        if n == 0:
            return
        per_face = max(1, self._layout.per_page // n)
        for p in self.print_queue:
            p.quantity = per_face
        for pid, ent in self._des_qty_entries.items():
            if ent.winfo_exists():
                ent.delete(0, "end")
                ent.insert(0, str(per_face))
        self._update_preview()
        self._set_status(f"Set {per_face} each ({n} faces × {per_face} = {per_face * n} slots)")

    def _on_designer_resize(self, event=None) -> None:
        if not self.print_queue:
            return
        if event is not None:
            cw, ch = event.width, event.height
            if cw < 20 or ch < 20:
                return
            last = getattr(self, "_des_last_size", (0, 0))
            if abs(last[0] - cw) < 4 and abs(last[1] - ch) < 4:
                return
            self._des_last_size = (cw, ch)
        if hasattr(self, "_des_resize_cheap_job"):
            self.after_cancel(self._des_resize_cheap_job)
        if hasattr(self, "_des_resize_full_job"):
            self.after_cancel(self._des_resize_full_job)
        self._des_resize_cheap_job = self.after(
            _DES_RESIZE_DEBOUNCE_FAST_MS, self._apply_designer_preview_fast,
        )
        self._des_resize_full_job = self.after(
            _DES_RESIZE_DEBOUNCE_FULL_MS, self._apply_designer_preview_full,
        )

    def _apply_designer_preview_fast(self) -> None:
        self._update_preview(preview_dpi=_DES_PREVIEW_DPI_FAST)

    def _apply_designer_preview_full(self) -> None:
        self._update_preview(preview_dpi=_DES_PREVIEW_DPI_FULL)

    def _update_preview(self, preview_dpi: int = _DES_PREVIEW_DPI_FULL) -> None:
        """Designer preview: always page 1 (no pagination)."""
        total_n = sum(p.quantity for p in self.print_queue)
        per_page = self._layout.per_page

        self._total_lbl.configure(
            text=f"Total: {total_n} / {per_page} spots used",
        )

        cw = self._des_canvas.winfo_width()
        ch = self._des_canvas.winfo_height()
        if cw < 20 or ch < 20:
            return

        eff_dpi = preview_dpi
        if total_n > _PREVIEW_SLOTS_HARD:
            eff_dpi = min(eff_dpi, _PREVIEW_DPI_HARD_CAP)
        elif total_n > _PREVIEW_SLOTS_SOFT:
            eff_dpi = min(eff_dpi, _PREVIEW_DPI_SOFT_CAP)

        prev = render_preview(
            self.print_queue,
            0,
            dpi=eff_dpi,
            layout=self._layout,
            use_tight=(self._des_crop_mode == "Faces"),
        )
        fitted = fit_image(prev, cw, ch)
        self._des_preview_photo = ImageTk.PhotoImage(fitted)
        self._des_canvas.delete("all")
        self._des_canvas.create_image(
            cw // 2, ch // 2,
            image=self._des_preview_photo, anchor="center",
        )

    def _export_pdf(self) -> None:
        if sum(p.quantity for p in self.print_queue) == 0:
            messagebox.showinfo(
                "Nothing to Export",
                "Add faces and set quantities on the Design & Export tab.",
            )
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            title="Save Printable PDF",
        )
        if not path:
            return
        try:
            pages = generate_pdf(self.print_queue, path, layout=self._layout,
                                 use_tight=(self._des_crop_mode == "Faces"))
            self._set_status(f"PDF saved → {path} ({pages} page(s))")
            messagebox.showinfo(
                "Success",
                f"PDF saved!\n\n{path}\n\n"
                f"{pages} page(s), 4\"×6\", 300 DPI ready.\n"
                "Print at actual size on 4×6 photo paper.",
            )
        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    def _export_img(self) -> None:
        if sum(p.quantity for p in self.print_queue) == 0:
            messagebox.showinfo(
                "Nothing to Export",
                "Add faces and set quantities on the Design & Export tab.",
            )
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")],
            title="Save High-Res Image",
        )
        if not path:
            return
        try:
            generate_high_res(self.print_queue, path, self.current_page, layout=self._layout,
                              use_tight=(self._des_crop_mode == "Faces"))
            self._set_status(f"Image saved → {path}")
            messagebox.showinfo(
                "Success",
                f"300 DPI image saved!\n\n{path}\n\n"
                f"Page {self.current_page + 1} exported.",
            )
        except Exception as e:
            messagebox.showerror("Export Error", str(e))
