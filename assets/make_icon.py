# -*- coding: utf-8 -*-
"""
assets/make_icon.py — generates assets/icon.ico and assets/icon-256.png
and copies to app/static/icon.png for web UI and favicon.

Design:
  A modern cyber-security / digital lens privacy emblem:
  * Midnight navy / sapphire blue squircle with subtle neon-cyan rim
  * Dynamic privacy shield contour in vivid electric cyan and azure
  * Concentric camera lens / aperture ring
  * Brilliant four-point diamond star / clean sparkle in pure white & cyan glow
  * Zero pink, zero purple.

Run:
  python assets/make_icon.py
"""

from __future__ import annotations

import io
import math
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
STATIC = ROOT / "app" / "static"
ICON_ICO = ASSETS / "icon.ico"
ICON_PNG = ASSETS / "icon-256.png"
STATIC_PNG = STATIC / "icon.png"
STATIC_ICO = STATIC / "icon.ico"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def create_master_icon(size: int = 1024) -> Image.Image:
    """Render the master icon on a high-res 1024x1024 canvas for lanczos downsampling."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s = size / 1024.0

    # -------------------------------------------------------------------------
    # 1. Base Squircle / Rounded Square
    # -------------------------------------------------------------------------
    corner = 224 * s
    pad = 32 * s

    # Rich vertical / diagonal gradient (Midnight Navy -> Deep Oceanic Blue -> Dark Tech Sapphire)
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / max(1, size - 1)
        if t < 0.5:
            k = t / 0.5
            r = int(6 + (11 - 6) * k)
            g = int(20 + (48 - 20) * k)
            b = int(40 + (88 - 40) * k)
        else:
            k = (t - 0.5) / 0.5
            r = int(11 + (5 - 11) * k)
            g = int(48 + (20 - 48) * k)
            b = int(88 + (38 - 88) * k)
        gd.line([(0, y), (size, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=corner, fill=255
    )
    img.paste(grad, (0, 0), mask)

    # Ambient soft cyan top-center light aura
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [size * 0.12, -size * 0.22, size * 0.88, size * 0.52],
        fill=(0, 210, 255, 45)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(85 * s))
    img.paste(Image.alpha_composite(img, glow), (0, 0), mask)

    # Upper glass highlight
    hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle(
        [pad + 4 * s, pad + 4 * s, size - pad - 4 * s, int(size * 0.5)],
        radius=corner * 0.9, fill=(255, 255, 255, 24)
    )
    hl = hl.filter(ImageFilter.GaussianBlur(25 * s))
    img.paste(Image.alpha_composite(img, hl), (0, 0), mask)

    # Outer border rim in electric cyan / aqua
    border_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border_layer)
    bd.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=corner, outline=(0, 210, 255, 110), width=int(8 * s)
    )
    img = Image.alpha_composite(img, border_layer)

    # -------------------------------------------------------------------------
    # 2. Privacy Shield Contour
    # -------------------------------------------------------------------------
    cx, cy = 512 * s, 510 * s
    top_w = 236 * s
    top_y = 224 * s

    shield_pts = []
    # Crest peak
    shield_pts.append((cx, top_y - 24 * s))
    shield_pts.append((cx + top_w, top_y))
    shield_pts.append((cx + top_w, top_y + 250 * s))
    
    # Quadratic curve down to bottom point
    steps = 24
    bottom_y = cy + 340 * s
    for i in range(steps + 1):
        t = i / steps
        x = (cx + top_w) * (1 - t)**2 + (cx + top_w * 0.38) * 2 * (1 - t) * t + cx * t**2
        y = (top_y + 250 * s) * (1 - t)**2 + (cy + 295 * s) * 2 * (1 - t) * t + bottom_y * t**2
        shield_pts.append((x, y))

    # Curve back up left side
    for i in range(steps + 1):
        t = i / steps
        x = cx * (1 - t)**2 + (cx - top_w * 0.38) * 2 * (1 - t) * t + (cx - top_w) * t**2
        y = bottom_y * (1 - t)**2 + (cy + 295 * s) * 2 * (1 - t) * t + (top_y + 250 * s) * t**2
        shield_pts.append((x, y))

    shield_pts.append((cx - top_w, top_y))

    # Fill shield with deep translucent navy-teal
    shield_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shield_layer)
    sd.polygon(shield_pts, fill=(6, 28, 54, 180))

    # Outer Shield Stroke (bold electric cyan)
    sd.line(shield_pts + [shield_pts[0]], fill=(0, 235, 255, 240), width=int(22 * s), joint="curve")
    img = Image.alpha_composite(img, shield_layer)

    # -------------------------------------------------------------------------
    # 3. Camera Lens / Aperture Rings
    # -------------------------------------------------------------------------
    lens_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lens_layer)
    lens_r = 156 * s
    lens_cy = cy - 20 * s

    # Outer aperture ring
    ld.ellipse(
        [cx - lens_r, lens_cy - lens_r, cx + lens_r, lens_cy + lens_r],
        outline=(0, 190, 255, 200), width=int(10 * s)
    )
    # Inner aperture ring
    inner_r = lens_r * 0.64
    ld.ellipse(
        [cx - inner_r, lens_cy - inner_r, cx + inner_r, lens_cy + inner_r],
        outline=(0, 240, 255, 140), width=int(5 * s)
    )

    # 6 Aperture Blade Lines
    for k in range(6):
        ang = math.radians(k * 60)
        x1 = cx + inner_r * math.cos(ang)
        y1 = lens_cy + inner_r * math.sin(ang)
        ang2 = ang + math.radians(45)
        x2 = cx + lens_r * math.cos(ang2)
        y2 = lens_cy + lens_r * math.sin(ang2)
        ld.line([(x1, y1), (x2, y2)], fill=(0, 220, 255, 160), width=int(5 * s))

    img = Image.alpha_composite(img, lens_layer)

    # -------------------------------------------------------------------------
    # 4. Clean Diamond Sparkles (Pure White + Glowing Cyan Halo)
    # -------------------------------------------------------------------------
    def draw_sparkle(center_x: float, center_y: float, r_long: float, r_short: float,
                     fill_color: tuple, halo_color: tuple | None = None) -> Image.Image:
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        if halo_color:
            halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(halo).ellipse(
                [center_x - r_long * 0.95, center_y - r_long * 0.95,
                 center_x + r_long * 0.95, center_y + r_long * 0.95],
                fill=halo_color
            )
            halo = halo.filter(ImageFilter.GaussianBlur(r_long * 0.45))
            layer = Image.alpha_composite(layer, halo)

        spd = ImageDraw.Draw(layer)
        # Vertical diamond ray
        pts_v = [
            (center_x, center_y - r_long),
            (center_x + r_short, center_y),
            (center_x, center_y + r_long),
            (center_x - r_short, center_y)
        ]
        spd.polygon(pts_v, fill=fill_color)
        # Horizontal diamond ray
        pts_h = [
            (center_x - r_long, center_y),
            (center_x, center_y + r_short),
            (center_x + r_long, center_y),
            (center_x, center_y - r_short)
        ]
        spd.polygon(pts_h, fill=fill_color)
        return layer

    # Master Sparkle at center of lens
    star1 = draw_sparkle(cx, lens_cy, 126 * s, 28 * s,
                         fill_color=(255, 255, 255, 255),
                         halo_color=(0, 230, 255, 180))
    img = Image.alpha_composite(img, star1)

    # Top-right satellite sparkle
    star2 = draw_sparkle(cx + 258 * s, top_y - 22 * s, 54 * s, 14 * s,
                         fill_color=(255, 255, 255, 245),
                         halo_color=(0, 210, 255, 130))
    img = Image.alpha_composite(img, star2)

    # Bottom-left mini sparkle
    star3 = draw_sparkle(cx - 245 * s, cy + 225 * s, 32 * s, 8 * s,
                         fill_color=(210, 250, 255, 220),
                         halo_color=(0, 190, 255, 100))
    img = Image.alpha_composite(img, star3)

    return img


def render(size: int, master: Image.Image | None = None) -> Image.Image:
    """Render at specified size using high-quality Lanczos resampling from 1024x1024 master."""
    if master is None:
        master = create_master_icon(1024)
    if size == 1024:
        return master
    return master.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    STATIC.mkdir(exist_ok=True)

    print("Rendering 1024x1024 master emblem...")
    master = create_master_icon(1024)

    # Save 256x256 PNG
    icon_256 = render(256, master)
    icon_256.save(ICON_PNG, "PNG")
    print(f"Wrote {ICON_PNG} (256x256)")

    # Also copy 256x256 PNG to app/static/icon.png
    icon_256.save(STATIC_PNG, "PNG")
    print(f"Wrote {STATIC_PNG} (web app static icon)")

    # Generate multi-resolution Windows .ico
    ico_images = [render(s, master) for s in ICO_SIZES]
    # Pillow save format="ICO"
    master_ico = max(ico_images, key=lambda im: im.size[0])
    master_ico.save(ICON_ICO, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"Wrote {ICON_ICO} ({len(ICO_SIZES)} sizes: {ICO_SIZES})")

    # Copy .ico to app/static/icon.ico for favicon
    shutil.copy2(ICON_ICO, STATIC_ICO)
    print(f"Wrote {STATIC_ICO} (web app favicon)")


if __name__ == "__main__":
    sys.exit(main())
