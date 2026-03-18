"""FacePrint Studio — Main application window and all UI tabs."""

from __future__ import annotations

import dataclasses
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from face_processor import detect_faces_in_image, DetectedFace, Person
from print_generator import (
    render_preview,
    generate_pdf,
    generate_high_res,
    total_pages,
    PER_PAGE,
    LAYOUTS,
)
from utils import pil_to_ctk, fit_image
from library_manager import save_library, load_library, clear_library

# Drag-and-drop support — optional; app works fine without it
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

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
        self.title("FacePrint Studio")
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
        self._print_btns: dict[str, ctk.CTkButton] = {}  # pid → library card button
        self._lib_cards: dict[str, ctk.CTkFrame] = {}   # pid → library card frame
        self._lib_thumb_cache: dict[str, ctk.CTkImage] = {}        # pid → heads CTkImage
        self._lib_thumb_cache_tight: dict[str, ctk.CTkImage] = {}  # pid → faces CTkImage
        self._lib_crop_mode: str = "Heads"   # "Heads" or "Faces" for library display
        self._des_crop_mode: str = "Heads"   # "Heads" or "Faces" for designer/export
        self._lib_dirty: bool = True   # full rebuild needed when True
        self._lib_build_token: int = 0  # incremented to cancel stale staggered builds

        # Image-ref lists (prevent garbage collection of CTkImage objects)
        self._ann_ref: ctk.CTkImage | None = None
        self._grid_refs: list[ctk.CTkImage] = []
        self._lib_refs: list[ctk.CTkImage] = []
        self._des_refs: list[ctk.CTkImage] = []
        self._des_qty_entries: dict[str, ctk.CTkEntry] = {}
        self._exp_refs: list[ctk.CTkImage] = []

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_tabs()
        self._build_status_bar()
        self._load_saved_library()

    def _load_saved_library(self) -> None:
        """Restore the library saved from the previous session."""
        try:
            saved = load_library()
        except Exception:
            saved = []
        if not saved:
            return
        self.persons = saved
        self._person_counter = len(saved)
        self._lib_dirty = True
        self._refresh_library()
        self._set_status(f"Loaded {len(saved)} face(s) from saved library")

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
        for c in (self._des_canvas, self._exp_canvas):
            c.configure(bg=canvas_bg)

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
        self.update_idletasks()

    # ──────────────────────────────────────────────────────────────────────
    #  Tabs
    # ──────────────────────────────────────────────────────────────────────
    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self, command=self._on_tab_change)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        self._tab1 = self.tabs.add("Upload & Select Faces")
        self._tab2 = self.tabs.add("My Faces Library")
        self._tab3 = self.tabs.add("Print Designer")
        self._tab4 = self.tabs.add("Preview & Export")

        self._build_upload_tab()
        self._build_library_tab()
        self._build_designer_tab()
        self._build_export_tab()

    def _on_tab_change(self) -> None:
        t = self.tabs.get()
        if t == "My Faces Library":
            self._refresh_library()
        elif t == "Print Designer":
            self._refresh_designer()
        elif t == "Preview & Export":
            self._refresh_export_preview()

    # ═════════════════════════════════════════════════════════════════════
    #  TAB 1 — Upload & Select Faces
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
                "Drop photos here\n\nor click  Add Photo(s)  above"
                if _DND_AVAILABLE
                else "Click  Add Photo(s)  above to load photos"
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
        for f in self.detected_faces:
            if f.id == fid:
                f.selected = not f.selected
                break
        self._refresh_face_grid()

    def _clear_detections(self) -> None:
        self.detected_faces.clear()
        self._annotated_images.clear()
        self._ann_index = 0
        self._ann_ref = None
        self._grid_refs.clear()
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

        existing_ids = {p.id for p in self.persons}
        added = 0
        for f in sel:
            if f.id in existing_ids:
                continue
            self._person_counter += 1
            self.persons.insert(
                0,
                Person(
                    id=f.id,
                    name=f"Face {self._person_counter}",
                    face_image=f.cropped.copy(),
                    face_tight_image=f.tight_cropped.copy(),
                    is_low_res=f.is_low_res,
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

        # Right-side controls
        ctk.CTkButton(
            hdr, text="Clear Saved Library", width=160,
            fg_color="#993333", hover_color="#cc4444",
            command=self._clear_saved_library,
        ).pack(side="right", padx=4, pady=6)

        self._lib_crop_seg = ctk.CTkSegmentedButton(
            hdr, values=["Heads", "Faces"],
            command=self._on_lib_crop_toggle,
            width=160, height=30,
        )
        self._lib_crop_seg.set("Heads")
        self._lib_crop_seg.pack(side="right", padx=(4, 0), pady=6)
        ctk.CTkButton(
            hdr, text="Save Library Now", width=150,
            command=self._manual_save,
        ).pack(side="right", padx=4, pady=6)
        ctk.CTkButton(
            hdr, text="Sort A → Z", width=110,
            fg_color="gray40", hover_color="gray30",
            command=self._sort_library_alpha,
        ).pack(side="right", padx=4, pady=6)

        self._lib_scroll = ctk.CTkScrollableFrame(tab)
        self._lib_scroll.pack(fill="both", expand=True, padx=4, pady=4)
        self._lib_cols = 0
        self._lib_card_width = 140  # approximate card width including padding
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
        """Reflow cards when the frame width changes."""
        new_cols = self._calc_lib_cols()
        if new_cols != self._lib_cols and self._lib_cards:
            self._lib_cols = new_cols
            self._regrid_lib_cards()

    def _regrid_lib_cards(self) -> None:
        """Re-grid all existing cards into current column count — no rebuilds."""
        cols = self._lib_cols or self._calc_lib_cols()
        for ci in range(cols):
            self._lib_scroll.grid_columnconfigure(ci, weight=1)
        for i, p in enumerate(self.persons):
            card = self._lib_cards.get(p.id)
            if card and card.winfo_exists():
                r, c = divmod(i, cols)
                card.grid(row=r, column=c, padx=4, pady=4, sticky="n")

    def _refresh_library(self) -> None:
        # Skip full rebuild if nothing changed and cards already exist
        if not self._lib_dirty and self._lib_cards:
            return

        self._lib_dirty = False
        self._lib_build_token += 1
        token = self._lib_build_token

        for w in self._lib_scroll.winfo_children():
            w.destroy()
        self._lib_refs.clear()
        self._print_btns.clear()
        self._lib_cards.clear()

        if not self.persons:
            self._show_lib_empty()
            return

        self._lib_cols = self._calc_lib_cols()
        cols = self._lib_cols
        for ci in range(cols):
            self._lib_scroll.grid_columnconfigure(ci, weight=1)

        persons_snapshot = list(self.persons)

        def _build_row(start: int) -> None:
            if token != self._lib_build_token:
                return
            end = min(start + cols, len(persons_snapshot))
            for i in range(start, end):
                self._make_lib_card(persons_snapshot[i], i, cols)
            if end < len(persons_snapshot):
                self.after(0, _build_row, end)

        _build_row(0)

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

        use_tight = self._lib_crop_mode == "Faces"
        cache = self._lib_thumb_cache_tight if use_tight else self._lib_thumb_cache
        if p.id not in cache:
            src = (p.face_tight_image or p.face_image) if use_tight else p.face_image
            thumb = src.convert("RGB")
            thumb.thumbnail((80, 80), Image.LANCZOS)
            cache[p.id] = pil_to_ctk(thumb, (80, 80))
        ref = cache[p.id]
        self._lib_refs.append(ref)

        ctk.CTkLabel(card, image=ref, text="", fg_color=card_bg).pack(padx=6, pady=(6, 2))

        if p.is_low_res:
            tk.Label(
                card, text="⚠ Low-res source",
                fg="#ffaa00", bg=card_bg, font=("Segoe UI", 8),
            ).pack()

        name_var = ctk.StringVar(value=p.name)
        ctk.CTkEntry(
            card, textvariable=name_var, width=110, height=24, justify="center",
            font=("Segoe UI", 10),
        ).pack(padx=4, pady=2)
        name_var.trace_add(
            "write",
            lambda *_, pid=p.id, sv=name_var: self._rename(pid, sv.get()),
        )

        btns = tk.Frame(card, bg=card_bg)
        btns.pack(pady=(0, 2))
        for txt, bg, cmd in [
            ("▲", btn_bg, lambda pid=p.id: self._move(pid, -1)),
            ("▼", btn_bg, lambda pid=p.id: self._move(pid,  1)),
            ("✕", del_bg, lambda pid=p.id: self._delete(pid)),
        ]:
            tk.Button(
                btns, text=txt, width=2, padx=0, pady=0,
                bg=bg, fg=btn_fg, relief="flat", borderwidth=0,
                activebackground=card_border, activeforeground=btn_fg,
                cursor="hand2", command=cmd,
            ).pack(side="left", padx=1)

        in_queue = any(q.id == p.id for q in self.print_queue)
        pq_btn = ctk.CTkButton(
            card,
            text="✓ In Queue" if in_queue else "+ Add to Queue",
            width=100, height=24,
            fg_color="#1a4a28" if in_queue else "#1f538d",
            hover_color="#993333" if in_queue else "#1a4a70",
            font=("Segoe UI", 10, "bold" if in_queue else "normal"),
            command=lambda pid=p.id: (
                self._remove_from_print_queue(pid) if any(q.id == pid for q in self.print_queue)
                else self._add_to_print_queue(pid)
            ),
        )
        pq_btn.pack(padx=4, pady=(0, 6))
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

    def _manual_save(self) -> None:
        self._auto_save()

    def _sort_library_alpha(self) -> None:
        self.persons.sort(key=lambda p: p.name.lower())
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
        for p in self.persons:
            if p.id == pid:
                self.print_queue.append(dataclasses.replace(p, quantity=1))
                break
        self._update_print_btn(pid, in_queue=True)
        self._refresh_designer()

    def _remove_from_print_queue(self, pid: str) -> None:
        self.print_queue = [p for p in self.print_queue if p.id != pid]
        self._update_print_btn(pid, in_queue=False)
        self._refresh_designer()

    def _update_print_btn(self, pid: str, *, in_queue: bool) -> None:
        """Update just the one library card button — no full grid rebuild."""
        btn = self._print_btns.get(pid)
        if btn is None or not btn.winfo_exists():
            return
        if in_queue:
            btn.configure(
                text="✓ In Queue",
                fg_color="#1a4a28", hover_color="#993333",
                font=("Segoe UI", 11, "bold"),
                command=lambda: self._remove_from_print_queue(pid),
            )
        else:
            btn.configure(
                text="+ Add to Queue",
                fg_color="#1f538d", hover_color="#1a4a70",
                font=("Segoe UI", 11),
                command=lambda: self._add_to_print_queue(pid),
            )

    # ═════════════════════════════════════════════════════════════════════
    #  TAB 3 — Print Designer
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

        # Equalize button
        eq_btn = ctk.CTkButton(
            left, text="Equalize", width=120,
            fg_color="gray40", hover_color="gray30",
            command=self._equalize_quantities,
        )
        eq_btn.pack(pady=(2, 4))

        self._des_scroll = ctk.CTkScrollableFrame(left)
        self._des_scroll.pack(fill="both", expand=True, padx=4, pady=4)

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
        self._des_canvas.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self._des_canvas.bind("<Configure>", self._on_designer_resize)

    def _layout_info_text(self) -> str:
        lo = self._layout
        return (
            f"Paper 4\"×6\"  ·  Margins {lo.margin}\"\n"
            f"Cells {lo.cell}\"×{lo.cell}\"  ·  {lo.cols} cols × {lo.rows} rows\n"
            f"{lo.per_page} faces per page"
        )

    def _on_layout_change(self, label: str) -> None:
        self._layout = LAYOUTS[label]
        if hasattr(self, "_layout_info_lbl"):
            self._layout_info_lbl.configure(text=self._layout_info_text())
        self._update_preview()
        self._refresh_export_preview()

    def _on_lib_crop_toggle(self, mode: str) -> None:
        self._lib_crop_mode = mode
        self._lib_dirty = True
        self._refresh_library()

    def _on_des_crop_toggle(self, mode: str) -> None:
        self._des_crop_mode = mode
        self._update_preview()
        self._refresh_export_preview()

    def _refresh_designer(self) -> None:
        for w in self._des_scroll.winfo_children():
            w.destroy()
        self._des_refs.clear()
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
            row = ctk.CTkFrame(self._des_scroll, corner_radius=6)
            row.pack(fill="x", padx=4, pady=3)

            thumb = p.face_image.convert("RGB")
            thumb.thumbnail((44, 44), Image.LANCZOS)
            ref = pil_to_ctk(thumb, (44, 44))
            self._des_refs.append(ref)
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

        self._update_preview()

    def _qty_delta(self, pid: str, delta: int) -> None:
        for p in self.print_queue:
            if p.id == pid:
                p.quantity = max(0, p.quantity + delta)
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
        for p in self.print_queue:
            if p.id == pid:
                if p.quantity == v:
                    return
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
        if hasattr(self, "_des_resize_job"):
            self.after_cancel(self._des_resize_job)
        self._des_resize_job = self.after(80, self._update_preview)

    def _update_preview(self) -> None:
        """Designer preview: always page 1 (no pagination)."""
        total_n = sum(p.quantity for p in self.print_queue)
        pages = total_pages(self.print_queue, self._layout)

        self._total_lbl.configure(
            text=f"Total: {total_n} face(s) · {pages} page(s)",
        )

        cw = self._des_canvas.winfo_width()
        ch = self._des_canvas.winfo_height()
        if cw < 20 or ch < 20:
            return

        prev = render_preview(self.print_queue, 0, layout=self._layout,
                              use_tight=(self._des_crop_mode == "Faces"))
        fitted = fit_image(prev, cw, ch)
        self._des_preview_photo = ImageTk.PhotoImage(fitted)
        self._des_canvas.delete("all")
        self._des_canvas.create_image(
            cw // 2, ch // 2,
            image=self._des_preview_photo, anchor="center",
        )

    # ═════════════════════════════════════════════════════════════════════
    #  TAB 4 — Preview & Export
    # ═════════════════════════════════════════════════════════════════════
    def _build_export_tab(self) -> None:
        tab = self._tab4
        ctk.CTkLabel(
            tab, text="Preview & Export",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=8)

        # Buttons packed first with side="bottom" so they're always visible
        bf = ctk.CTkFrame(tab, fg_color="transparent")
        bf.pack(side="bottom", pady=10)
        ctk.CTkButton(
            bf, text="  Generate Printable PDF",
            command=self._export_pdf, width=220,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bf, text="  Export High-Res Image",
            command=self._export_img, width=220,
        ).pack(side="left", padx=8)

        self._exp_canvas = tk.Canvas(
            tab, highlightthickness=0, borderwidth=0, bg="#2b2b2b",
        )
        self._exp_canvas.pack(fill="both", expand=True, padx=40, pady=4)
        self._exp_canvas.bind("<Configure>", self._on_export_resize)

    def _on_export_resize(self, event=None) -> None:
        if not self.print_queue:
            return
        if hasattr(self, "_exp_resize_job"):
            self.after_cancel(self._exp_resize_job)
        self._exp_resize_job = self.after(80, self._refresh_export_preview)

    def _refresh_export_preview(self) -> None:
        self._exp_refs.clear()
        self._exp_canvas.delete("all")

        total_n = sum(p.quantity for p in self.print_queue)
        cw = self._exp_canvas.winfo_width()
        ch = self._exp_canvas.winfo_height()

        if total_n == 0:
            self._exp_canvas.create_text(
                cw // 2, ch // 2,
                text="Configure faces in Print Designer first.",
                fill="gray50", font=("Segoe UI", 12),
            )
            return

        if cw < 20 or ch < 20:
            return

        prev = render_preview(self.print_queue, 0, dpi=200, layout=self._layout,
                              use_tight=(self._des_crop_mode == "Faces"))
        fitted = fit_image(prev, cw, ch)
        self._exp_preview_photo = ImageTk.PhotoImage(fitted)
        self._exp_canvas.create_image(
            cw // 2, ch // 2,
            image=self._exp_preview_photo, anchor="center",
        )

    def _export_pdf(self) -> None:
        if sum(p.quantity for p in self.print_queue) == 0:
            messagebox.showinfo(
                "Nothing to Export",
                "Add faces and set quantities in Print Designer.",
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
                "Add faces and set quantities in Print Designer.",
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
