# FacePrint Studio

A Windows desktop app for detecting, cropping, and printing face photo sheets — built for Walmart 4×6 photo printing.

Upload group photos, let the app automatically find every face, organize them into a library, then build a print sheet with however many copies of each face you need. Export a print-ready PDF or high-res PNG and drop it at the Walmart photo counter.

---

## Features

- **Automatic face detection** — drag & drop or file-pick photos; faces are detected instantly using OpenCV's YuNet model
- **Face library** — save faces with names, reorder them, and persist the library between sessions
- **Print Designer** — set per-face quantities and see a live preview of your 4"×6" sheet
- **Smart cropping** — face crops expand with forehead/chin padding and match the background color at the edges
- **Export** — generates a 300 DPI print-ready PDF or PNG; 77 faces per page (7 columns × 11 rows, 0.5"×0.5" each)
- **HEIC/HEIF support** — works with iPhone photos directly
- **Fully offline** — no cloud, no API keys, no account needed

---

## Installation

### Requirements

- Python 3.10+
- Windows (tested on Windows 10/11)

### Setup

```bash
git clone https://github.com/iRowebot/faceprint-studio.git
cd faceprint-studio

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

### Run

```bash
python main.py
```

On first run, the YuNet face detection model (~230 KB) will be downloaded automatically and cached in `~/.faceprint_studio/models/`.

---

## Usage

1. **Upload & Select Faces** — add photos, click face thumbnails to select them, then click **Add Selected to Library**
2. **My Faces Library** — rename faces, reorder with ▲/▼, or sort A→Z; click **+ Add to Print Queue**
3. **Print Designer** — adjust quantities per face; use **Equalize** to fill the sheet evenly
4. **Preview & Export** — generate a PDF or high-res image ready for printing

---

## Project Structure

```
faceprint-studio/
├── main.py              # Entry point
├── app.py               # UI and all tab logic
├── face_processor.py    # Face detection and cropping (OpenCV YuNet)
├── print_generator.py   # PDF and image export
├── library_manager.py   # Save/load face library to disk
├── utils.py             # Image helpers
└── requirements.txt
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `customtkinter` | Modern dark-mode UI |
| `opencv-python` | YuNet face detection |
| `Pillow` | Image processing and cropping |
| `reportlab` | PDF generation |
| `numpy` | Array operations |
| `tkinterdnd2` | Drag-and-drop support |
| `pillow-heif` | HEIC/HEIF photo support |

---

## License

MIT
