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
- Click **+ Add to Print Queue** for each face you want to print

### 3. Print Designer
- Adjust how many copies of each face to print using **− / +**
- Click **Equalize** to automatically fill the sheet evenly
- See a live preview of your 4"×6" print sheet on the right

### 4. Preview & Export
- Click **Generate Printable PDF** to save a print-ready PDF
- Or click **Export High-Res Image** to save a PNG
- Print at **actual size** on **4×6 photo paper** at 300 DPI

---

## Print Sheet Details

| Setting | Value |
|---|---|
| Paper size | 4" × 6" |
| Faces per page | 77 (7 columns × 11 rows) |
| Face size | 0.5" × 0.5" |
| Margins | 0.25" |
| Resolution | 300 DPI |

---

## Features

- **Automatic face detection** — powered by OpenCV's YuNet model
- **Persistent face library** — saves between sessions automatically
- **HEIC/HEIF support** — works with iPhone photos directly
- **Smart edge padding** — crops match the photo background color
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
