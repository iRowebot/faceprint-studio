# -*- mode: python ; coding: utf-8 -*-
import os, sys
from PyInstaller.utils.hooks import collect_all

_CONDA = os.path.dirname(sys.executable)
_CONDA_BIN = os.path.join(_CONDA, 'Library', 'bin')

datas = [('icon.ico', '.')]
binaries = [
    (os.path.join(_CONDA_BIN, 'tcl86t.dll'), '.'),
    (os.path.join(_CONDA_BIN, 'tk86t.dll'), '.'),
    (os.path.join(_CONDA_BIN, 'zlib1.dll'), '.'),
]
hiddenimports = []
tmp_ret = collect_all('pillow_heif')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tkinterdnd2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[_CONDA_BIN],
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
    name='FacePrint Studio',
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
    icon=['icon.ico'],
)
