# -*- mode: python ; coding: utf-8 -*-
# ---------------------------------------------------------------------------
# PyInstaller spec — builds the single-file, console-less Windows .exe.
#
# Build it with:   python -m PyInstaller --noconfirm --clean Metavoid.spec
# (build.bat does exactly this inside a throwaway virtual environment.)
#
# Result:          dist/Metavoid.exe  (double-clickable, ~50-70 MB)
#
# The exe embeds:
#   * the app icon          -> assets/icon.ico   (shown in Explorer/taskbar)
#   * a Windows version resource -> version_info.txt (Properties -> Details)
#   * the web UI data       -> templates/ and static/
#   * when installed: pywebview / pythonnet / clr-loader plus the WebView2
#     loader, so the "native desktop window" mode works.
# ---------------------------------------------------------------------------

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, \
    collect_dynamic_libs

hiddenimports = []
# Flask pulls Jinja2/Werkzeug/etc.; collect their modules so the frozen app
# finds templates, static routing and the dev server machinery.
for pkg in ("flask", "jinja2", "jinja2.ext", "werkzeug", "markupsafe",
            "click", "itsdangerous", "blinker", "socket", "email"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
hiddenimports += ["piexif", "PIL._tkinter_finder"]  # engine deps

# pywebview (native window). Safe even when it isn't installed: collect_*
# simply returns empty lists, and the app falls back to browser mode.
for pkg in ("webview", "clr_loader", "pythonnet", "webview.platforms"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
datas = []
binaries = []
for pkg in ("webview", "clr_loader", "pythonnet"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass
datas += [
    ("app/templates", "templates"),
    ("app/static", "static"),
    ("assets/icon.ico", "."),                       # exposed next to exe data
]

# Keep the exe small and avoid accidentally bundling heavy scientific libs
# that are installed on some dev machines but never used here.
excludes = [
    "numpy", "scipy", "pandas", "matplotlib", "tkinter", "PyQt5", "PyQt6",
    "PySide2", "PySide6", "notebook", "jupyter", "IPython", "pytest",
    "tests", "test", "clr_loader.tests",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Metavoid",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # keep it deterministic; UPX optional
    console=False,             # windowed app — no black console on double-click
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="version_info.txt",
)
