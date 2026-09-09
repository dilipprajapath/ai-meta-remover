# -*- coding: utf-8 -*-
"""
webp_clean.py — Lossless metadata removal for WebP (RIFF container).

WebP files are RIFF: ``RIFF <size> WEBP`` followed by a chunk list. The image
chunks (``VP8 `` / ``VP8L`` / ``VP8X`` / ``ALPH`` / ``ANIM`` / ``ANMF``) are
kept byte-for-byte — pixels are never touched. Metadata lives in sibling
chunks:

    ``EXIF``  -> EXIF table (in WebP it may wrap a JPEG-style APP11/C2PA blob)
    ``XMP ``  -> XMP packet (AI tools also store C2PA provenance JSON here)
    ``ICCP``  -> ICC colour profile (dropped only when the checkbox asks)

``VP8X`` begins with a flags byte announcing which optional chunks exist.
When we delete EXIF / XMP / ICCP we clear the matching flag bit so decoders
never go looking for a chunk that is gone.

Implementation is a clean two-pass walk: (1) classify every chunk, (2) rebuild
the RIFF container writing only what survives.
"""

from __future__ import annotations

import struct
from typing import List, Tuple

from . import xmp_clean
from .ai_metadata import XMP_VALUE_AI_MARKERS

RIFF_HEAD = b"RIFF"
WEBP_TAG = b"WEBP"

# VP8X flags byte bit positions (WebP spec / RFC 9649):
#   0x80 Rsv | 0x40 A(anim) | 0x20 X(XMP) | 0x10 E(EXIF)
#   | 0x08 L(alpha) | 0x04 I(ICC) | 0x03 Rsv
_FLAG_ICC = 0x04
_FLAG_EXIF = 0x10
_FLAG_XMP = 0x20


def clean_webp(raw: bytes, remove_all: bool = False,
               remove_ai: bool = True,
               strip_icc: bool = False) -> Tuple[bytes, List[str]]:
    """Returns (cleaned_webp, removed_chunks_report)."""
    removed: List[str] = []
    if not (raw[:4] == RIFF_HEAD and raw[8:12] == WEBP_TAG):
        raise ValueError("Not a WebP file")

    riff_size = struct.unpack("<I", raw[4:8])[0]
    limit = min(len(raw), 12 + riff_size)

    # ------------------------------------------------------------------ pass 1
    # decisions: list of (cid, start, csize, drop, replacement) per chunk
    chunks = []
    pos = 12
    vp8x_flags = None    # value of the VP8X flags byte once seen
    while pos + 8 <= limit:
        cid = raw[pos:pos + 4]
        (csize,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        if pos + 8 + csize > limit:               # truncated file — bail safely
            break
        data = raw[pos + 8:pos + 8 + csize]
        padded = csize + (csize & 1)            # RIFF chunks are 2-byte padded
        seg_end = min(pos + 8 + padded, limit)

        drop = False
        label = ""
        replacement = None

        if cid == b"VP8X":
            if len(data) >= 1:
                vp8x_flags = data[0]
        elif cid == b"EXIF":
            low = data.lower()
            has_c2pa = (b"jumbf" in low or b"c2pa" in low or
                        b"content credentials" in low or
                        b"raw profile type c2pa" in low)
            if remove_all:
                drop, label = True, "EXIF chunk"
            elif remove_ai and has_c2pa:
                drop, label = True, "EXIF chunk wrapping C2PA/JUMBF data"
        elif cid == b"XMP ":
            if remove_all:
                drop, label = True, "XMP chunk"
            elif remove_ai:
                xrem: List[str] = []
                newp, why = xmp_clean.clean_xmp_payload(data, xrem)
                if newp is None:
                    drop, label = True, "XMP chunk (AI-only content)"
                elif why is not None and newp != data:
                    replacement = newp
                    label = f"XMP chunk filtered ({why})"
        elif cid == b"ICCP":
            if strip_icc:
                drop, label = True, "ICCP colour profile chunk"
        # all other chunks (VP8 / VP8L / ALPH / ANIM / ANMF / unknown) survive

        if drop:
            removed.append(label)
        chunks.append((cid, pos, csize, seg_end, drop, replacement))
        pos = seg_end

    # --------------------------------------------------- flags pre-compute
    # Work out the *final* VP8X flags from all drop decisions BEFORE writing
    # anything, so the flags byte is correct no matter where VP8X sits.
    for cid, _s, _c, _e, drop, _r in chunks:
        if not drop or vp8x_flags is None:
            continue
        if cid == b"EXIF":
            vp8x_flags &= ~_FLAG_EXIF
        elif cid == b"XMP ":
            vp8x_flags &= ~_FLAG_XMP
        elif cid == b"ICCP":
            vp8x_flags &= ~_FLAG_ICC

    # ---------------------------------------------------------------- pass 2
    out = bytearray()
    out += RIFF_HEAD
    # RIFF size patched at the very end once we know the rebuilt length.
    out += struct.pack("<I", 0)
    out += WEBP_TAG

    for cid, start, csize, end, drop, replacement in chunks:
        if drop:
            continue
        if cid == b"VP8X" and vp8x_flags is not None:
            # patch the flags byte of the surviving header chunk
            data = bytearray(raw[start + 8:start + 8 + csize])
            if not data:
                continue
            data[0] = vp8x_flags
            out += cid
            out += struct.pack("<I", len(data))
            out += bytes(data)
            if len(data) & 1:
                out += b"\x00"
            continue
        if replacement is not None:
            body = cid + struct.pack("<I", len(replacement)) + replacement
            if len(replacement) & 1:
                body += b"\x00"
            out += body
            removed.append(label)
            continue
        out += raw[start:end]

    # fix RIFF size (total file size - 8)
    total = len(out) - 8
    out[4:8] = struct.pack("<I", total)
    return bytes(out), removed
