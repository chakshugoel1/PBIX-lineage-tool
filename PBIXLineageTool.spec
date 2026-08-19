# PBIXLineageTool.spec
# Build with:  pyinstaller PBIXLineageTool.spec
# Produces a standalone dist/PBIXLineageTool/PBIXLineageTool.exe that needs
# no Python installation on the target machine.
import sys
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = []
binaries = []
hiddenimports = []

for pkg in ("pbixray", "pandas", "openpyxl", "qfluentwidgets"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("apsw")

# pandas/numpy ship their entire test suites; collect_all() pulls those in
# too, which massively slows the build and bloats the exe for no benefit
# in a packaged app, so strip anything under a "tests" subpackage/file.
hiddenimports = [m for m in hiddenimports if ".tests." not in m and not m.endswith(".tests")]
datas = [d for d in datas if f"{os.sep}tests{os.sep}" not in d[0]]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PBIXLineageTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    # Add an icon by dropping a .ico file at installer\app.ico and setting
    # icon="installer\\app.ico" below.
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PBIXLineageTool",
)
