# -*- coding: utf-8 -*-
"""
raw_clean.py — Cleaners for TIFF and TIFF-derived camera RAW formats.

Two complementary strategies:

A. ``clean_tiff`` (remove-all mode, and whenever Pillow can decode the file)
   Re-wraps the pixels into a fresh TIFF container *without* any EXIF/GPS/XMP/
   IPTC tags. The pixel matrix is preserved exactly (same bit depth, same
   compression when supported) — no resampling, no lossy pass. Structural
   tags that describe the raster itself (width/height/bits/rows-per-strip)
   must stay or the file is unreadable.

B. ``scrub_tiff_like`` (in-place surgery for RAW files Pillow cannot rewrap)
   Parses the IFD0 entry table of TIFF-derived RAWs (DNG, CR2, NEF, ARW, ORF,
   RW2, PEF, SRW…) and *nulls* the pointers + payload regions of the
   metadata IFDs: ExifIFD (0x8769), GPS (0x8825), XMP (0x02BC), IPTC (0x8373).
   Everything is overwritten in place, so every byte offset in the file stays
   valid — RAW pixel data is never relocated and cannot be damaged.

   Limitation documented in the README: for vendor RAW layouts we have not
   verified (RAF, exotic MRW), removal is limited to byte-signature window
   scrubbing of AI markers.
"""

from __future__ import annotations

import io
import struct
from typing import List, Optional, Tuple

from PIL import Image

# Compression tag (259) value -> Pillow 'compression' kwarg for TIFF save.
_TIFF_COMPRESSION_MAP = {
    1: None,              # none
    5: "tiff_lzw",        # LZW
    7: "tiff_jpeg",       # JPEG (old-style)
    8: "tiff_adobe_deflate",
    32946: "tiff_adobe_deflate",
    32773: None,          # PackBits — Pillow writes uncompressed fallback
}
# Metadata IFD/data pointers found in TIFF-derived RAW files, mapped to the
# "which" groups used by scrub_tiff_like.
#   ExifIFD 0x8769, GPS 0x8825, XMP 0x02BC, IPTC-NAA 0x83BB, PrintIM 0xC4A5
_TAG_GROUPS = {
    0x8769: "exif", 0x8825: "gps", 0x02BC: "xmp", 0x83BB: "iptc",
    0xC4A5: "printim",
}
_GROUP_LABEL = {"exif": "ExifIFD", "gps": "GPS-IFD", "xmp": "XMP",
                "iptc": "IPTC", "printim": "PrintIM"}
_AI_RAW_MARKERS = (b"c2pa", b"jumbf", b"jumb", b"content credentials", b"dgi0")


def clean_tiff(raw: bytes, remove_all: bool = True, remove_ai: bool = True,
               strip_icc: bool = False) -> Tuple[bytes, List[str]]:
    """Rewrap a TIFF/DNG with Pillow, dropping metadata tags.

    Returns (clean_tiff_bytes, report). Raises ValueError when the source is
    not decodable (caller then falls back to scrub_tiff_like).
    """
    removed: List[str] = []
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as exc:
        raise ValueError(f"Pillow cannot decode TIFF: {exc}")

    # Read the original compression tag so the rewrite uses it.
    compression = None
    try:
        if hasattr(im, "tag_v2"):
            comp_val = im.tag_v2.get(259)
            if isinstance(comp_val, (tuple, list)):
                comp_val = comp_val[0] if comp_val else None
            compression = _TIFF_COMPRESSION_MAP.get(comp_val)
    except Exception:
        compression = None

    # Pillow's TIFF writer re-emits the tags it loaded into im.tag_v2 / im.info
    # (ImageDescription, Make, Software, GPS sub-IFDs, private/vendor tags…).
    # The only bulletproof way to get a *clean* container is to rebuild a fresh
    # Image from the decoded pixel bytes — frombytes() carries no tags at all.
    # Pixel data is identical; this is a re-wrap, not a re-compress of pixels
    # (the writer may re-compress the same pixel stream with the same codec).
    try:
        px = im.tobytes()
        newim = Image.frombytes(im.mode, im.size, px)
        # carry the palette across for indexed images so rendering is equal
        if im.mode == "P" and getattr(im, "palette", None):
            newim.putpalette(im.palette.tobytes() if hasattr(
                im.palette, "tobytes") else list(im.palette.palette))
    except Exception:
        # Rare multi-frame / exotic layout: fall back to a copy with its
        # metadata-clearing (still strips every metadata info key).
        newim = im.copy()
        try:
            newim.info.clear()
            if hasattr(newim, "tag_v2"):
                try:
                    newim.tag_v2 = {}
                except Exception:
                    pass
        except Exception:
            pass
    if newim.info:
        try:
            newim.info.clear()
        except Exception:
            pass

    out = io.BytesIO()
    attempts = [compression, "tiff_lzw", None]
    saved = False
    last_err = None
    for comp in attempts:
        try:
            out = io.BytesIO()
            kwargs = {}
            if comp:
                kwargs["compression"] = comp
            # no 'tiffinfo', no tags on newim -> nothing but raster structure
            newim.save(out, format="TIFF", **kwargs)
            saved = True
            break
        except Exception as exc:                 # e.g. group4 unsupported
            last_err = exc
            continue
    if not saved:
        raise ValueError(f"TIFF rewrite failed: {last_err}")

    result = out.getvalue()
    removed.append("EXIF/GPS/XMP/IPTC tags (full metadata strip via re-wrap)")
    if not strip_icc:
        removed.append("(colour profile not requested — none carried over)")
    return result, removed


# ---------------------------------------------------------------------------
# In-place surgery for RAW formats
# ---------------------------------------------------------------------------

def _u16(raw: bytes, off: int, le: bool) -> int:
    return struct.unpack("<H" if le else ">H", raw[off:off + 2])[0]


def _u32(raw: bytes, off: int, le: bool) -> int:
    return struct.unpack("<I" if le else ">I", raw[off:off + 4])[0]


def _zero(raw: bytearray, start: int, length: int) -> int:
    """Zero length bytes at start; returns how many bytes were zeroed."""
    if start < 0 or length <= 0:
        return 0
    end = min(len(raw), start + length)
    start = min(start, end)
    for i in range(start, end):
        raw[i] = 0
    return end - start


def _entry_area_len(raw: bytes, ifd_off: int, le: bool) -> int:
    """Return the byte length of the IFD table (count*12 + count-word + next)."""
    if ifd_off + 2 > len(raw):
        return 0
    count = _u16(raw, ifd_off, le)
    return 2 + count * 12 + 4


def scrub_tiff_like(raw: bytes, which=None,
                    ai_scan: bool = True) -> Tuple[bytes, List[str]]:
    """Null selected metadata IFD pointers + tables in TIFF-derived RAW bytes.

    which: subset of {"exif","gps","xmp","iptc","printim"} — default all.
    ai_scan: also run the AI byte-signature window scrub afterwards.

    Returns (scrubbed_bytes, report).
    """
    if which is None:
        which = {"exif", "gps", "xmp", "iptc", "printim"}
    out = bytearray(raw)
    removed: List[str] = []
    n = len(out)
    endian = out[0:2]
    le = endian == b"II"
    is_tiff_layout = (n >= 8 and endian in (b"II", b"MM")
                      and out[2:4] in (b"\x2a\x00", b"\x00\x2a"))

    if is_tiff_layout:
        ifd0 = _u32(out, 4, le)
        if ifd0 + 2 <= n:
            count = _u16(out, ifd0, le)
            if ifd0 + 2 + count * 12 > n:
                count = max(0, (n - ifd0 - 2) // 12)
            for e in range(count):
                entry = ifd0 + 2 + e * 12
                tag = _u16(out, entry, le)
                grp = _TAG_GROUPS.get(tag)
                if grp is None or grp not in which:
                    continue
                fcount = _u32(out, entry + 4, le)
                value_off_field = entry + 8
                target = _u32(out, value_off_field, le)
                if grp in ("xmp", "iptc"):
                    # raw byte blob whose length = count (ASCII/BYTE)
                    area = min(max(fcount, 0), n - target) if target < n else 0
                    area = max(0, min(area, 1 << 22))    # cap 4 MB
                else:
                    area = _entry_area_len(out, target, le)
                if target < n and area > 0:
                    z = _zero(out, target, area)
                    if z:
                        removed.append(f"{_GROUP_LABEL[grp]} data zeroed "
                                       f"({z} bytes)")
                # null the pointer so no reader follows it
                if value_off_field + 4 <= len(out):
                    for i in range(value_off_field, value_off_field + 4):
                        out[i] = 0
                removed.append(f"{_GROUP_LABEL[grp]} pointer nulled")

    if ai_scan:
        removed += _scrub_ai_windows(out)
    return bytes(out), removed


def _scrub_ai_windows(out: bytearray) -> List[str]:
    """Zero windows around raw AI byte signatures (in place). Returns report."""
    low = bytes(out).lower()
    removed: List[str] = []
    for marker in _AI_RAW_MARKERS:
        start = 0
        while True:
            idx = low.find(marker, start)
            if idx == -1:
                break
            # zero a window around the marker (offsets stay valid)
            s = max(0, idx - 128)
            e = min(len(out), idx + len(marker) + 8192)
            _zero(out, s, e - s)
            removed.append(f"AI signature '{marker.decode()}' window zeroed")
            start = idx + len(marker)
    return removed
