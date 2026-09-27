# -*- coding: utf-8 -*-
"""
assets/make_icon.py — generates assets/icon.ico and assets/icon-256.png
and copies to app/static/icon.png for web UI and favicon.

Design: flat and square, matching the UI — an off-white frame around an
empty dark square (the "void"). No gradients, glows or rounded corners.

Run:
  python assets/make_icon.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
STATIC = ROOT / "app" / "static"
ICON_ICO = ASSETS / "icon.ico"
ICON_PNG = ASSETS / "icon-256.png"
STATIC_PNG = STATIC / "icon.png"
STATIC_ICO = STATIC / "icon.ico"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

BG = (14, 14, 16, 255)       # --bg
FG = (236, 236, 238, 255)    # --accent


def create_master_icon(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(img)
    s = size / 1024
    d.rectangle([160 * s, 160 * s, 864 * s, 864 * s], fill=FG)   # frame
    d.rectangle([320 * s, 320 * s, 704 * s, 704 * s], fill=BG)   # void
    return img


def main() -> None:
    master = create_master_icon()
    icon_256 = master.resize((256, 256), Image.Resampling.LANCZOS)
    icon_256.save(ICON_PNG, "PNG")
    icon_256.save(STATIC_PNG, "PNG")
    master.save(ICON_ICO, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    shutil.copy2(ICON_ICO, STATIC_ICO)
    print(f"Wrote {ICON_PNG}, {STATIC_PNG}, {ICON_ICO}, {STATIC_ICO}")


if __name__ == "__main__":
    sys.exit(main())
