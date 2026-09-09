# -*- coding: utf-8 -*-
"""
png_clean.py — Lossless metadata removal for PNG (and APNG).

PNG = sequence of length-prefixed chunks; every chunk has a CRC-32 of
(type + data). We walk the chunk stream and *drop whole chunks* we don't
want, recomputing CRCs only for chunks we rebuild (there are none in the
lossless path — every surviving chunk is copied verbatim, so its CRC stays
valid). The IDAT image data is copied byte-for-byte: zero pixel change.

Chunks removed:
  * AI / C2PA always:
      - iTXt  key  "Raw profile type c2pa" / "Raw profile type dgi" (C2PA)
      - iTXt/zTXt/tEXt whose key *or* value carries AI markers
      - tEXt key "parameters"/"prompt" (A1111-style generation parameters)
  * Mode 'remove all' additionally:
      - every tEXt / zTXt / iTXt text chunk
      - tIME   (last-modification time)
      - hIST   (palette histogram — purely informational)
  * Optionally (checkbox): iCCP / sRGB / gAMA colour-management chunks.

Colour/rendering chunks (pHYs, sBIT, bKGD, cHRM, sPLT, acTL/fcTL for APNG)
are always preserved so the picture renders exactly as before.
"""

from __future__ import annotations

import struct
import zlib
from typing import List, Optional, Tuple

from .ai_metadata import XMP_VALUE_AI_MARKERS, XMP_KEY_AI_FRAGMENTS

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# chunks we *never* touch (critical + needed for correct rendering/animation)
_KEEP_COLOR = {"IHDR", "PLTE", "IDAT", "IEND",
               "pHYs", "sBIT", "bKGD", "cHRM", "sPLT",
               "acTL", "fcTL", "fdAT",            # APNG animation
               "tRNS"}
_TEXT_CHUNKS = {"tEXt", "zTXt", "iTXt"}

_C2PA_CHUNK_TYPES = {
    "caBX": "C2PA / Content Authenticity Box (caBX chunk)",
    "caCI": "C2PA / Content Authenticity Claim Info (caCI chunk)",
    "c2pa": "C2PA manifest chunk",
    "C2PA": "C2PA manifest chunk",
    "jumb": "JUMBF metadata box chunk",
    "JUMB": "JUMBF metadata box chunk",
    "crl0": "C2PA crl0 chunk",
    "dgi0": "C2PA dgi0 chunk",
    "prVc": "C2PA provenance chunk",
    "prvC": "C2PA provenance chunk",
}

_AUTHOR_KEYS = {"author", "copyright", "artist", "credit", "owner",
                "rights", "license", "licensor", "byline", "source"}

AI_PNG_KEYS = {
    "raw profile type c2pa": "C2PA manifest (iTXt)",
    "raw profile type dgi":  "C2PA dgi (iTXt)",
    "cabx":                  "C2PA caBX key",
    "caci":                  "C2PA caCI key",
    "c2pa":                  "C2PA key (text chunk)",
    "jumbf":                 "JUMBF key (text chunk)",
    "parameters":            "A1111 generation parameters",
    "prompt":                "generation prompt chunk",
    "positive prompt":       "generation prompt chunk",
    "negative prompt":       "generation prompt chunk",
    "workflow":              "ComfyUI workflow",
}


def _chunk_iter(raw: bytes):
    """Yield (type, data, start_offset, total_len) for every PNG chunk."""
    off = 8
    n = len(raw)
    while off + 8 <= n:
        (length,) = struct.unpack(">I", raw[off:off + 4])
        ctype = raw[off + 4:off + 8].decode("latin-1")
        data = raw[off + 8:off + 8 + length]
        total = 12 + length
        yield ctype, data, off, total
        off += total
        if ctype == "IEND":
            break


def _text_content(data: bytes, ctype: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (key, text-value) decoded for tEXt/zTXt/iTXt (best effort)."""
    try:
        if ctype == "tEXt":
            key, _, val = data.partition(b"\x00")
            return key.decode("latin-1", "replace"), val.decode("latin-1", "replace")
        if ctype == "zTXt":
            key, _, rest = data.partition(b"\x00")
            if not rest:
                return key.decode("latin-1", "replace"), ""
            method = rest[0]
            raw_val = rest[1:] if method == 0 else b""
            try:
                return key.decode("latin-1", "replace"), \
                    zlib.decompress(raw_val).decode("utf-8", "replace")
            except Exception:
                try:
                    return key.decode("latin-1", "replace"), \
                        zlib.decompress(raw_val).decode("latin-1", "replace")
                except Exception:
                    return key.decode("latin-1", "replace"), ""
        if ctype == "iTXt":
            # keyword\0 compflag compmethod lang\0 translated\0 text
            idx0 = data.find(b"\x00")
            if idx0 == -1 or idx0 + 3 > len(data):
                return None, None
            key = data[:idx0].decode("latin-1", "replace")
            comp_flag = data[idx0 + 1]
            rest = data[idx0 + 3:]
            idx1 = rest.find(b"\x00")
            if idx1 == -1:
                return key, ""
            rest2 = rest[idx1 + 1:]
            idx2 = rest2.find(b"\x00")
            if idx2 == -1:
                return key, ""
            text_raw = rest2[idx2 + 1:]
            if comp_flag == 1:
                try:
                    text_bytes = zlib.decompress(text_raw)
                except Exception:
                    text_bytes = text_raw
            else:
                text_bytes = text_raw
            try:
                return key, text_bytes.decode("utf-8", "replace")
            except Exception:
                return key, text_bytes.decode("latin-1", "replace")
    except Exception:
        return None, None
    return None, None


def _norm_key(key: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_-]", "", key.lower())


def clean_png(raw: bytes, remove_all: bool = False,
              remove_ai: bool = True,
              strip_icc: bool = False,
              keep_color: bool = True,
              strip_location: bool = False,
              strip_author: bool = False) -> Tuple[bytes, List[str]]:
    """Returns (cleaned_png, removed_chunks_report).

    strip_location: PNG may embed an ``eXIf`` chunk (EXIF incl. GPS) — drop it.
    strip_author:   drop text chunks whose keyword is an author/copyright
                    field (Author, Copyright, Artist, …).
    """
    removed: List[str] = []
    if not raw.startswith(PNG_SIG):
        raise ValueError("Not a PNG file (bad signature)")
    if raw.startswith(b"8BIM"):          # never happens; guard
        raise ValueError("Not a PNG file")

    out = bytearray(PNG_SIG)
    for ctype, data, off, total in _chunk_iter(raw):
        drop = False
        label = ""

        if ctype in ("IDAT", "IHDR", "IEND"):
            # critical chunks are always copied verbatim (pixels!)
            out += raw[off:off + total]
        elif ctype in _C2PA_CHUNK_TYPES:
            # C2PA chunks (e.g. caBX used by ChatGPT/OpenAI, caCI, c2pa, jumb)
            if remove_ai or remove_all:
                drop, label = True, _C2PA_CHUNK_TYPES[ctype]
            else:
                out += raw[off:off + total]
        elif len(data) >= 8 and (data[4:8] == b"jumb" or (b"jumb" in data[:64].lower() and b"c2pa" in data[:64].lower())):
            # JUMBF/C2PA box container chunk
            if remove_ai or remove_all:
                drop, label = True, f"{ctype} chunk (JUMBF / C2PA container)"
            else:
                out += raw[off:off + total]
        elif ctype == "eXIf":
            # PNG EXIF chunk (may hold GPS/date). Dropped when the user asks
            # for location removal or full metadata removal.
            if remove_all or strip_location:
                drop, label = True, "eXIf chunk (EXIF/GPS)"
            else:
                out += raw[off:off + total]
        elif ctype in _TEXT_CHUNKS:
            key, val = _text_content(data, ctype)
            low_key = _norm_key(key or "")
            low_val = (val or "").lower()

            # 1) explicit AI keys (only when AI removal is enabled)
            if remove_ai and key and key.lower() in AI_PNG_KEYS:
                drop, label = True, AI_PNG_KEYS[key.lower()]
            # 2) AI fragment keys (c2pa, provenance, prompt …)
            elif remove_ai and low_key and any(frag in low_key for frag, _ in XMP_KEY_AI_FRAGMENTS):
                drop, label = True, f"{ctype} chunk key '{key}' (AI-related)"
            # 3) AI marker inside the value
            elif remove_ai and any(m in low_val for m in XMP_VALUE_AI_MARKERS):
                drop, label = True, f"{ctype} chunk '{key}' value contains AI marker"
            # 4) XMP XML packet inside iTXt/tEXt/zTXt
            elif remove_ai and ("xmp" in low_key or "xml" in low_key) and (
                    any(m in low_val for m in XMP_VALUE_AI_MARKERS) or
                    any(frag in low_val for frag, _ in XMP_KEY_AI_FRAGMENTS)):
                drop, label = True, f"{ctype} chunk '{key}' contains AI provenance"
            # 5) remove-all: drop every text chunk (even non-AI, e.g. Author)
            elif remove_all:
                drop, label = True, f"{ctype} chunk '{key}' (all metadata)"
            # 6) author / copyright keyword option (AI-only mode refinements)
            elif strip_author and low_key in _AUTHOR_KEYS:
                drop, label = True, f"{ctype} chunk '{key}' (author/copyright)"
            else:
                out += raw[off:off + total]
        elif ctype == "tIME":
            if remove_all:
                drop, label = True, "tIME chunk (timestamp)"
            else:
                out += raw[off:off + total]
        elif ctype == "hIST":
            if remove_all:
                drop, label = True, "hIST chunk (palette histogram)"
            else:
                out += raw[off:off + total]
        elif ctype in ("iCCP", "sRGB", "gAMA"):
            if strip_icc:
                drop, label = True, f"{ctype} colour-management chunk"
            elif remove_all and not keep_color:
                drop, label = True, f"{ctype} colour chunk (all metadata mode)"
            else:
                out += raw[off:off + total]
        elif ctype in _KEEP_COLOR:
            out += raw[off:off + total]
        else:
            # All other ancillary chunks (in remove_all mode drop unknown metadata chunks)
            if remove_all:
                drop, label = True, f"{ctype} ancillary chunk (all metadata mode)"
            else:
                out += raw[off:off + total]

        if drop and label:
            removed.append(label)

    return bytes(out), removed
