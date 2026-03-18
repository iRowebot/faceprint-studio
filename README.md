# FacePrint Studio

A Windows desktop app for detecting, cropping, and printing face photo sheets — built for Walmart 4×6 photo printing.

Upload group photos, let the app automatically find every face, organize them into a library, then build a print sheet with however many copies of each face you need. Export a print-ready PDF or PNG and drop it at the Walmart photo counter.

---

## Download & Run

**No installation required. No Python. No setup.**

1. Go to the [**Releases**](https://github.com/iRowebot/faceprint-studio/releases/latest) page
2. Download **FacePrint Studio.exe**
3. Double-click to run

> On first launch, Windows may show a "Windows protected your PC" SmartScreen warning.  
> Click **"More info"** → **"Run anyway"** — this is normal for apps that aren't code-signed.

> A small face detection model (~230 KB) will be downloaded automatically on first run. An internet connection is required the first time only.

---

## How to Use

### 1. Upload & Select Faces
- Click **Add Photo(s)** or drag and drop photos onto the app
- The app automatically detects every face in the photo
- Click face thumbnails to select the ones you want
- Click **Add Selected to Library →**

### 2. My Faces Library
- Rename each face by clicking the name field
- Use **▲ / ▼** buttons to reorder, or **Sort A → Z** to sort alphabetically
- Toggle between **Heads** (full head with hair) and **Faces** (tight face-only crop) to preview both versions
- Click **+ Add to Queue** for each face you want to print

### 3. Print Designer
- Select a **face size** from the dropdown:
  - **0.5" × 0.5"** — 77 faces per page (7 × 11 grid, 0.25" margins)
  - **1" × 1"** — 15 faces per page (3 × 5 grid, 0.5" margins)
- Choose a **crop mode**:
  - **Heads** — full head crop including hair and a bit of neck
  - **Faces** — tight crop from brow to chin; **recommended for the 0.5" size** where detail matters most
- Adjust how many copies of each face to print using **− / +**
- Click **Equalize** to automatically fill the sheet evenly
- See a live preview of your 4"×6" print sheet on the right

### 4. Preview & Export
- Click **Generate Printable PDF** to save a print-ready PDF
- Or click **Export High-Res Image** to save a PNG
- Print at **actual size** on **4×6 photo paper** at 300 DPI

---

## Print Sheet Details

| Setting | 0.5" × 0.5" layout | 1" × 1" layout |
|---|---|---|
| Paper size | 4" × 6" | 4" × 6" |
| Margins | 0.25" | 0.5" |
| Grid | 7 columns × 11 rows | 3 columns × 5 rows |
| Faces per page | 77 | 15 |
| Resolution | 300 DPI | 300 DPI |

---

## Features

- **Automatic face detection** — powered by OpenCV's YuNet model; handles group photos, selfies, and high-resolution HEIC files
- **Two crop modes** — **Heads** (full head) and **Faces** (tight brow-to-chin) stored per person; switch anytime without re-uploading
- **Two print layouts** — 0.5"×0.5" (77/page) or 1"×1" (15/page), selectable per print job
- **Persistent face library** — saves between sessions automatically
- **HEIC/HEIF support** — works with iPhone photos directly
- **Smart edge padding** — padding color matches the photo background
- **Drag & drop** support
- **Fully offline** after first run — no cloud, no account, no API keys

---

## Running from Source

If you prefer to run from source (requires Python 3.10+):

```bash
git clone https://github.com/iRowebot/faceprint-studio.git
cd faceprint-studio

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
python main.py
```

---

## Dependencies (source only)

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
