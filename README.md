# FacePrint Studio

A Windows desktop app for detecting, cropping, and printing face photo sheets on standard 4×6 photo paper.

Upload group photos, let the app automatically find every face, organize them into a library, then build a print sheet with however many copies of each face you need. Export a print-ready PDF or high-res PNG.

---

## Download & Run

**No installation required. No Python. No setup.**

1. Go to the [**Releases**](https://github.com/iRowebot/faceprint-studio/releases/latest) page
2. Download **FacePrint Studio.exe**
3. Double-click to run

> On first launch, Windows may show a "Windows protected your PC" SmartScreen warning.  
> Click **"More info"** → **"Run anyway"** — this is normal for apps that aren't code-signed.

> A small face detection model (~230 KB) will be downloaded automatically on first run. An internet connection is required the first time only.

### When your browser or antivirus blocks the download

**This will happen for many people.** FacePrint Studio is distributed as a single `.exe` file. It was built with [PyInstaller](https://pyinstaller.org/), which bundles Python and the app into one file so you don’t have to install Python yourself. That same kind of packaged file is also used by some harmful software, so **Microsoft Defender**, **Edge**, **Chrome**, and other tools often treat **unsigned** or **newly published** downloads with extra suspicion—even when the file is safe. What you’re seeing is usually a **false positive**: the app isn’t malware, but automated scanners can’t tell a good file from a bad one by reputation alone.

The download isn’t **code-signed** because a Windows code-signing certificate typically costs **about $250–$500 per year**, which isn’t worth paying for a free app. That’s why you may see warnings—but you can still run the program safely if you downloaded it from the [official Releases](https://github.com/iRowebot/faceprint-studio/releases) page.

**What you can do:**

1. **Browser** — If the download is blocked or removed, look for **Keep** or **Show more** → **Keep anyway** (the exact wording depends on your browser). You can also try another browser after allowing the file in Windows Security (below).
2. **Windows Security** — Open **Settings → Privacy & security → Windows Security → Virus & threat protection → Protection history**. If `FacePrint Studio.exe` was quarantined, choose **Allow on device** or **Restore** (only use files from the official Releases link above).
3. **Optional exclusion** — If you use FacePrint Studio often, you can add a folder under **Virus & threat protection → Manage settings → Exclusions** (for example, the folder where you keep the app). Only do this if you’re comfortable trusting files you put in that folder.

---

## How to Use

### 1. Import & Select Faces
- Click **Add Photo(s)**, **Paste (Ctrl+V)**, or drag and drop photos onto the app
- The app automatically detects every face in the photo
- Click face thumbnails to select the ones you want
- Click **Add Selected to Library →**

### 2. My Faces Library
- Rename each face by clicking the name field
- Use **▲ / ▼** buttons to reorder, or **Sort A → Z** to sort alphabetically
- Toggle between **Heads** (full head with hair) and **Faces** (tight face-only crop) to preview both versions
- Click **+ Add to Queue** for each face you want to print

### 3. Design & Export
- Select a **face size** from the dropdown:
  - **0.5" × 0.5"** — 77 faces per page (7 × 11 grid, 0.25" margins)
  - **0.75" × 0.75"** — 35 faces per page (5 × 7 grid, 1/8" L/R, 3/8" T/B margins)
  - **1" × 1"** — 15 faces per page (3 × 5 grid, 0.5" margins)
- Choose a **crop mode**:
  - **Heads** — full head crop including hair
  - **Faces** — tight crop from brow to chin; **recommended for 0.5" size faces** where detail matters most
- Adjust how many copies of each face to print using **− / +**
- Click **Equalize** to automatically fill the sheet evenly
- See a live preview of your 4"×6" print sheet on the right

- Click **Generate Printable PDF** or **Export High-Res Image** below the preview
- Print at **actual size** on **4×6 photo paper** at 300 DPI

---

## Print Sheet Details

| Setting | 0.5" × 0.5" layout | 0.75" × 0.75" layout | 1" × 1" layout |
|---|---|---|---|
| Paper size | 4" × 6" | 4" × 6" | 4" × 6" |
| Margins | 0.25" all sides | 1/8" L/R, 3/8" T/B | 0.5" all sides |
| Grid | 7 × 11 | 5 × 7 | 3 × 5 |
| Faces per page | 77 | 35 | 15 |
| Resolution | 300 DPI | 300 DPI | 300 DPI |

---

## Features

- **Automatic face detection** — powered by OpenCV's YuNet model; handles group photos, selfies, and high-resolution HEIC files
- **Two crop modes** — **Heads** (full head) and **Faces** (tight brow-to-chin) stored per person; switch anytime without re-uploading
- **Three print layouts** — 0.5"×0.5" (77/page), 0.75"×0.75" (35/page), or 1"×1" (15/page), selectable per print job
- **Persistent face library** — saves between sessions to `Documents/FacePrintLibrary` (`Mom.png`, `library.json`, etc.; filenames preserve your casing). Optional sidecar thumbnails (`*_lib_thumb.jpg`) may appear in the same folder for faster scrolling; they are regenerated as needed and do not replace your PNGs.
- **HEIC/HEIF support** — works with iPhone photos directly
- **Smart edge padding** — padding color matches the photo background
- **Drag & drop** and **paste from clipboard (Ctrl+V)** support
- **Fully offline** after first run — no cloud, no account, no API keys

---

## Upgrading

### Library location

| Version | Library folder |
|---|---|
| Pre-1.5.0 | `C:\Users\<you>\.faceprint_studio\library` |
| 1.5.0 and later | `Documents\FacePrintLibrary` |

If you used an earlier version, your data was stored in a hidden folder under your home directory. Starting in v1.5.0 the library moved to your **Documents** folder so it's easy to find and back up.

**The migration is automatic** — on first launch after updating, the app detects your old library and copies it to the new location. You don't need to do anything.

If you are already on v1.5.0 or later, your library is already at `Documents\FacePrintLibrary` and nothing changes.

### Thumbnails (v1.5.0+)

Newer versions may write small JPEG sidecar thumbnails (`*_lib_thumb.jpg`) alongside your face PNGs in `Documents\FacePrintLibrary`. These are optional display helpers — they are regenerated automatically and do not replace your PNGs. The PNGs and `library.json` remain the source of truth.

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

## Building the Windows `.exe` (maintainers)

Use the **`faceprint`** Conda environment (dependencies + PyInstaller are installed there):

```bash
conda activate faceprint
pip install pyinstaller   # once, if not already in the env
python -m PyInstaller "FacePrint Studio.spec"
```

The one-file `FacePrint Studio.exe` is written under `dist/`. The spec picks up Tcl/Tk DLLs from Conda’s `Library\bin` or from a standard Python `DLLs` folder.

> **Note:** A one-off project `.venv` is not required; if you previously created `.venv` in this repo, you can delete it and rely on `conda activate faceprint` instead.

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
