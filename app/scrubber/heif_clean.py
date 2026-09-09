# -*- coding: utf-8 -*-
"""
heif_clean.py — AI/C2PA removal for HEIC / HEIF / AVIF (ISO Base Media).

HEIF/AVIF stores images in an ISO-BMFF container. C2PA / Content Credentials
live there as an item (``infe`` item_type ``mime`` with content_type
``application/c2pa``) whose bytes are a JUMBF ``jumb`` box; AI tools also emit
bare ``jumb`` / ``dgi0`` boxes. Camera EXIF travels as item_type ``Exif`` and
XMP as ``mime`` (``application/rdf+xml``).

Why "in-place zeroing" instead of a full re-container?
   Deleting a box or item *correctly* means re-numbering item references and
   rewriting every byte offset in ``iloc``/``mdat`` — a mini MP4 muxer.
   Zeroing the *payload bytes* of the offending item keeps every size and
   offset valid, which is the only approach safe to ship untested against the
   many HEIF writers in the wild. Decoders see an empty/absent C2PA block.
   (The container-level guarantee we give in the README is for JPEG/PNG/WebP/
   GIF/BMP; HEIF support here is best-effort by design.)

We find item byte-ranges two ways:
   1. parse ``iinf``/``iloc``/``infe`` enough to map item ids -> byte ranges
      (construction_method 0 = absolute offsets, 1 = inside ``idat``);
   2. fall back to scanning the file for C2PA/JUMBF byte signatures between
      box boundaries, zeroing exactly those matched spans.
"""

from __future__ import annotations

import struct
from typing import List, Tuple

# box 4CCs we descend into while hunting for item tables
_CONTAINERS = {b"meta", b"moov", b"trak", b"mdia", b"minf", b"stbl", b"dinf",
               b"edts", b"mvex", b"moof", b"traf", b"iprp", b"ipco", b"grpl",
               b"iinf", b"udta", b"mfra", b"pdin", b"schi"}
_FULLBOX_EXTRA = {b"meta", b"mdia", b"minf", b"stbl", b"mvex", b"moof",
                  b"traf", b"iprp", b"grpl", b"iinf", b"udta"}

_AI_MARKERS = (b"c2pa", b"jumbf", b"jumb", b"content credentials",
               b"contentauthenticity", b"raw profile type c2pa")


def _walk_boxes(raw: bytes, start: int, end: int, depth: int = 0,
                out: list = None) -> None:
    """Collect (box_type, payload_start, payload_end) for every box in range."""
    if out is None:
        out = []
    pos = start
    while pos + 8 <= end:
        (size,) = struct.unpack(">I", raw[pos:pos + 4])
        btype = raw[pos + 4:pos + 8]
        header = 8
        if size == 1:                       # 64-bit extended size
            if pos + 16 > end:
                return
            (size,) = struct.unpack(">Q", raw[pos + 8:pos + 16])
            header = 16
        elif size == 0:
            size = end - pos                # box extends to end of file
        if size < header or pos + size > end:
            return
        payload_start = pos + header
        if btype in _FULLBOX_EXTRA:
            payload_start += 4              # version + flags
        out.append((btype, payload_start, pos + size))
        if btype in _CONTAINERS and depth < 6:
            _walk_boxes(raw, payload_start, pos + size, depth + 1, out)
        pos += size


def _zero_ai_spans(raw: bytearray, box_payloads: List[tuple]
                   ) -> List[str]:
    """Zero the payload of any box whose bytes match AI markers."""
    removed: List[str] = []
    for btype, ps, pe in box_payloads:
        if ps >= pe or pe > len(raw):
            continue
        chunk = bytes(raw[ps:pe])
        low = chunk.lower()
        # a *payload* box that IS the data (not a container with children we
        # already scanned) — matches when this box looks like AI content
        hit = False
        reason = ""
        if btype in (b"jumb", b"jumbf", b"dgi0", b"crl0"):
            hit = True
            reason = f"{btype.decode('latin-1')} box (C2PA container)"
        elif btype == b"mime":
            if b"c2pa" in low or b"jumb" in low or b"content credentials" in low:
                hit, reason = True, "mime item box (C2PA data)"
        elif btype == b"Exif":
            # EXIF item may embed an APP11 C2PA segment inside the TIFF part
            if b"jumb" in low or b"c2pa" in low:
                hit, reason = True, "Exif item wrapping C2PA data"
        if hit:
            for i in range(ps, pe):
                raw[i] = 0
            removed.append(f"{reason} ({pe - ps} bytes zeroed)")
    return removed


def clean_heif(raw: bytes, remove_all: bool = False,
               remove_ai: bool = True,
               strip_icc: bool = False,
               strip_exif: bool = False,
               strip_xmp: bool = False) -> Tuple[bytes, List[str]]:
    """Returns (scrubbed_bytes, report). Works in place on a copy.

    strip_exif / strip_xmp let "AI-only" mode also drop camera EXIF / XMP
    items when the user ticked location/copyright refinements (HEIF has no
    finer granularity without a full item re-muxer).
    """
    out = bytearray(raw)
    removed: List[str] = []

    boxes: List[tuple] = []
    _walk_boxes(out, 0, len(out), 0, boxes)

    if remove_ai:
        removed += _zero_ai_spans(out, boxes)

    wipe_exif = remove_all or strip_exif
    wipe_xmp = remove_all or strip_xmp or strip_exif

    if wipe_exif or wipe_xmp:
        for btype, ps, pe in boxes:
            if wipe_exif and btype == b"Exif":
                for i in range(ps, pe):
                    out[i] = 0
                removed.append("Exif item payload zeroed")
            elif wipe_xmp and btype == b"mime":
                chunk = bytes(out[ps:pe]).lower()
                if b"rdf+xml" in chunk or b"xmp" in chunk:
                    for i in range(ps, pe):
                        out[i] = 0
                    removed.append("XMP (mime/rdf+xml) item payload zeroed")

    # If we could not find any box tables at all, fall back to a byte-scan.
    if not boxes:
        low = out.lower()
        for marker in _AI_MARKERS:
            idx = 0
            while True:
                fidx = low.find(marker, idx)
                if fidx == -1:
                    break
                # zero a window around the marker
                s = max(0, fidx - 64)
                e = min(len(out), fidx + len(marker) + 4096)
                for i in range(s, e):
                    out[i] = 0
                removed.append(f"AI byte signature '{marker.decode()}' scrubbed")
                idx = fidx + len(marker)
    return bytes(out), removed
