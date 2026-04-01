# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-file build. Works with conda Python (Library\\bin) or standard Windows Python (DLLs)."""
import os
import sys

from PyInstaller.utils.hooks import collect_all

_PREFIX = sys.prefix
_CONDA_BIN = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
_DLLS = os.path.join(_PREFIX, "DLLs")

datas = [
    ("icon.ico", "."),
    ("models/face_detection_yunet_2023mar.onnx", "models"),
]
binaries: list = []

# Tcl/Tk / zlib — include only files that exist (conda vs python.org layout)
for folder in (_CONDA_BIN, _DLLS):
    if not os.path.isdir(folder):
        continue
    for name in ("tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        p = os.path.join(folder, name)
        if os.path.isfile(p) and not any(b[0] == p for b in binaries):
            binaries.append((p, "."))

hiddenimports: list = []
tmp_ret = collect_all("pillow_heif")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]
tmp_ret = collect_all("tkinterdnd2")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

_pathex = [p for p in (_CONDA_BIN,) if os.path.isdir(_CONDA_BIN)]

a = Analysis(
    ["main.py"],
    pathex=_pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FacePrint Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icon.ico"],
)
