# -*- coding: utf-8 -*-
"""
jpeg_clean.py — LOSSESS metadata removal for JPEG.

Goal: produce an output JPEG whose pixel data is *byte-for-byte identical* to
the input (no re-compression, no quality loss), while surgically removing:

  * EXIF APP1 segments            (piexif.insert with an empty exif dict, or
                                   our own removal below)
  * XMP APP1 segments
  * IPTC APP13 segments
  * JFIF APP0                     (only when 'all' mode + strip_icc,
                                   otherwise preserved)
  * Adobe APP14                   (harmless, kept unless 'remove_all')
  * COM comment segments          ('all' mode only)
  * the C2PA / Content Credentials segments:
        APP11 "JUMBF" segments
        APP2  "JUMBF" / "c2pa" segments
        any other APPn whose payload begins with a known C2PA/JUMBF brand
  * ICC APP2 profiles             (only when the UI explicitly asks to drop
                                   colour profiles)

Mechanics
---------
JPEG is a container: marker segments (0xFFD8 SOI, ... ) wrap compressed scan
data (0xFFDA SOS ... EOI). We walk the markers, drop the ones we don't want,
and copy everything else (including the entropy-coded scan bytes and any
unknown APP markers such as those used by some RAW-derived JPEGs) verbatim.

The walker below follows the JPEG spec's variable-length segment rules. For
APPn/COM/DQT/DHT/SOFn/SOS etc it reads the 16-bit big-endian length after the
marker and skips that many bytes; for the lone ones (SOI, EOI, RSTn, TEM) it
does not. This is deliberately implemented by hand instead of via Pillow so we
have *exact* control and don't rely on Pillow re-emitting the file.
"""

from __future__ import annotations

import io
import struct
import zlib
from typing import List, Optional, Tuple

try:
    import piexif
except Exception:               # pragma: no cover
    piexif = None

from .ai_metadata import (
    XMP_VALUE_AI_MARKERS,
)
from . import xmp_clean

# Marker constants
M_SOI = 0xD8
M_EOI = 0xD9
M_SOS = 0xDA
M_COM = 0xFE
M_TEM = 0x01

_LEN_PREFIXED = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7,
    0xC8, 0xC9, 0xCA, 0xCB, 0xCC, 0xCD, 0xCE, 0xCF,
    0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7,
    0xD8, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF,
    0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7,
    0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF,
    0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7,
    0xF8, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF,
}

# APP numbers (0..15) we keep *unconditionally*. APP2 ICC is kept unless the
# user asks to strip it. APP0 JFIF and APP14 Adobe are needed by many decoders
# for correct colour interpretation (YCbCr etc.), so they stay unless 'all'.
_KEEP_DEFAULT = {0, 1, 2, 13, 14}     # JFIF, EXIF, ICC, IPTC, Adobe


def _chunk_at(raw: bytes, i: int) -> Tuple[int, int, int]:
    """At position i == marker byte FF, return (marker_code, seg_start, seg_end).
    seg_end excludes the two length bytes of variable-length segments and
    equals i for standalone markers."""
    marker = raw[i + 1]
    if marker in (M_SOI, M_EOI, M_SOS) or (0xD0 <= marker <= 0xD7) or marker == M_TEM:
        return marker, i, i + 2
    # variable length segment: 2-byte length includes itself
    length = struct.unpack(">H", raw[i + 2:i + 4])[0]
    return marker, i, i + 2 + length


def _inspect_appn(raw: bytes) -> Tuple[Optional[int], bool, bool, bool]:
    """Lightweight scan used by scan.py.

    Returns (app11_segment_bytes, app2_is_jumbf, has_xmp_app1, has_com).
    """
    if len(raw) < 4 or raw[0] != 0xFF or raw[1] != M_SOI:
        return None, False, False, False
    app11 = None
    app2j = False
    xmp = False
    com = False
    i = 2
    n = len(raw)
    while i < n - 1:
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        try:
            if marker == M_SOS:          # scan data until EOI — stop scan
                break
            code, start, end = _chunk_at(raw, i)
        except (struct.error, IndexError):
            break
        if code in _LEN_PREFIXED and marker not in (M_SOI, M_EOI, M_SOS) \
                and not (0xD0 <= marker <= 0xD7):
            payload = raw[start + 4:end]
            if marker == 0xEB:           # APP11
                app11 = len(payload) + 4
            elif marker == 0xE1:         # APP1 EXIF or XMP
                if payload[:32].lower().startswith(b"http://ns.adobe.com/") \
                        or payload[:4].lower() == b"xmp\x00":
                    xmp = True
            elif marker == 0xE2:         # APP2 — ICC or JUMBF/C2PA
                if payload[:5].lower() == b"jumbf" or payload[:4].lower() == b"c2pa" \
                        or payload[:12].lower().startswith(b"c2pa"):
                    app2j = True
            elif marker == M_COM:
                com = True
            i = end
        else:
            i += 2
    return app11, app2j, xmp, com


def _segment_is_ai(raw: bytes, marker: int, payload: bytes) -> Tuple[bool, str]:
    """True if an APPn/COM segment carries C2PA/AI payload (payload = segment
    bytes after the 2 length bytes)."""
    if marker == 0xEB:                    # APP11 -> JUMBF/C2PA
        return True, "APP11 JUMBF/C2PA segment"
    if marker == 0xE2:                    # APP2 -> could be ICC (keep) or JUMBF
        head = payload[:8].lower()
        if head.startswith(b"jumbf") or head.startswith(b"c2pa"):
            return True, "APP2 C2PA/JUMBF segment"
        return False, ""
    if 0xE0 <= marker <= 0xEF:            # other APPn: sniff payload brand
        head = payload[:8].lower()
        if head.startswith(b"jumbf") or head.startswith(b"c2pa"):
            return True, f"APP{marker - 0xE0} C2PA/JUMBF segment"
        return False, ""
    if marker == M_COM:
        text = payload.decode("latin-1", "ignore").lower()
        for m in XMP_VALUE_AI_MARKERS:
            if m in text:
                return True, f"AI comment segment (contains '{m}')"
    return False, ""


def clean_jpeg(raw: bytes, remove_all: bool = False,
               strip_exif: bool = True, strip_xmp: bool = True,
               strip_iptc: bool = True, strip_comment: bool = True,
               strip_icc: bool = False, remove_ai: bool = True,
               strip_jfif: bool = False) -> Tuple[bytes, List[str]]:
    """Lossless metadata scrub of a full JPEG byte stream.

    Returns (cleaned_bytes, removed_segments_report).
    """
    removed: List[str] = []
    if piexif is None:
        pass                      # fall through to pure-python walker below

    out = bytearray()
    out += b"\xff\xd8"            # SOI
    i = 2
    n = len(raw)
    saw_sos = False
    kept_scan = b""

    while i < n - 1:
        if raw[i] != 0xFF:
            # stray byte before next marker (shouldn't happen) — copy and move
            out.append(raw[i])
            i += 1
            continue
        marker = raw[i + 1]
        if saw_sos:
            # In scan data 0xFF is escaped as 0xFF00 — find the *unescaped*
            # marker end: the next marker after SOS is EOI (or RSTn during
            # scan). Copy everything up to it unchanged.
            j = i + 2
            while j < n:
                if raw[j] == 0xFF and j + 1 < n:
                    if raw[j + 1] in (M_SOI, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4,
                                      0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA):
                        break
                j += 1
            # Copy raw[i:j] verbatim; j points at the first byte of an
            # unescaped marker (or n).
            out += raw[i:j]
            i = j
            if i >= n:
                break
            marker = raw[i + 1]
            if marker == M_EOI:
                out += b"\xff\xd9"
                # sanity: nothing after EOI should exist
                break
            continue

        if marker == M_SOS:
            saw_sos = True
            length = struct.unpack(">H", raw[i + 2:i + 4])[0]
            seg = raw[i:i + 2 + length]
            out += seg
            i += 2 + length
            continue
        if marker == M_EOI:
            out += b"\xff\xd9"
            break

        # ---- standalone & length-prefixed segments ----
        if marker in (M_SOI, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7,
                      M_TEM):
            out += b"\xff" + bytes([marker])
            i += 2
            continue
        if marker not in _LEN_PREFIXED:
            # unknown standalone marker — copy two bytes and move on
            out += raw[i:i + 2]
            i += 2
            continue

        # length-prefixed segment
        length = struct.unpack(">H", raw[i + 2:i + 4])[0]
        seg_end = i + 2 + length
        if seg_end > n:
            seg_end = n
        payload = raw[i + 4:seg_end]

        # --- decide keep / drop -------------------------------------------
        drop = False
        label = ""
        rebuilt_segment: Optional[bytes] = None      # for XMP partial rewrite
        if 0xE0 <= marker <= 0xEF:                    # APPn
            appn = marker - 0xE0
            is_ai, label = _segment_is_ai(raw, marker, payload)
            if remove_ai and is_ai:
                drop = True
            elif appn == 1:                           # APP1: EXIF or XMP
                head = payload[:64].lower()
                if head.startswith(b"exif\x00\x00") or head[:4] == b"exif":
                    if remove_all or strip_exif:
                        drop, label = True, "EXIF APP1 segment"
                    else:
                        # Keep EXIF (mode: AI only). Still drop the JFIF
                        # thumbnail APP1 carried inside is not separate here.
                        pass
                elif head.startswith(b"http://ns.adobe.com/") or \
                        head.startswith(b"xmp\x00"):
                    if remove_all:
                        drop, label = True, "XMP APP1 segment"
                    elif strip_xmp and remove_ai:
                        # Rewrite the packet dropping only AI/C2PA properties,
                        # preserving dc:rights & other "important" XMP.
                        xmp_removed: List[str] = []
                        new_payload, why = xmp_clean.clean_xmp_payload(
                            payload, xmp_removed)
                        if new_payload is None:
                            drop, label = True, "XMP APP1 (all AI, emptied)"
                        elif why is not None and new_payload != payload:
                            seg_len = 2 + 2 + len(new_payload)  # + marker,len
                            rebuilt_segment = (b"\xff\xe1" +
                                               struct.pack(">H",
                                                           2 + len(new_payload)) +
                                               new_payload)
                            label = f"XMP APP1 filtered ({why})"
                        # else: unchanged, keep original bytes
            elif appn == 13:
                if remove_all or strip_iptc:
                    drop, label = True, "IPTC APP13 segment"
            elif appn == 0:                           # JFIF APP0
                if remove_all and strip_jfif:
                    drop, label = True, "JFIF APP0 segment"
            elif appn == 14:                          # Adobe APP14
                if remove_all:
                    drop, label = True, "Adobe APP14 segment"
            elif appn == 2 and strip_icc:
                if payload[:12].lower() == b"icc_profile\x00":
                    drop, label = True, "ICC APP2 profile segment"
        elif marker == M_COM:
            is_ai, label = _segment_is_ai(raw, marker, payload)
            if remove_ai and is_ai:
                drop = True
            elif remove_all and strip_comment:
                drop, label = True, "COM comment segment"

        if drop:
            removed.append(label)
        elif rebuilt_segment is not None:
            out += rebuilt_segment
            if label:
                removed.append(label)
        else:
            out += raw[i:seg_end]
        i = seg_end

    return bytes(out), removed


# ---------------------------------------------------------------------------
# Fallback helper used by formats handled through piexif or Pillow where the
# APPn strip must also be confirmed. (Small wrappers keep scrubber.py simple.)
# ---------------------------------------------------------------------------

def _drop_appn(raw: bytes, appn_number: int, label: str
               ) -> Tuple[bytes, List[str]]:
    """Rebuild a JPEG dropping every segment of APPn `appn_number`
    (e.g. 13 = IPTC/Photoshop). Used for fine-grained options."""
    marker_no = 0xE0 + appn_number
    if len(raw) < 4:
        return raw, []
    out = bytearray(b"\xff\xd8")
    i = 2
    n = len(raw)
    removed: List[str] = []
    while i < n - 1:
        if raw[i] != 0xFF:
            out.append(raw[i]); i += 1; continue
        marker = raw[i + 1]
        try:
            code, start, end = _chunk_at(raw, i)
        except Exception:
            out += raw[i:]; break
        if marker == marker_no:
            length = struct.unpack(">H", raw[i + 2:i + 4])[0]
            end = i + 2 + length
            removed.append(label)
            i = end
            continue
        if marker == M_SOS:
            # copy scan data verbatim to the end
            out += raw[i:]
            break
        if code in _LEN_PREFIXED and marker not in (M_SOI, M_EOI, M_SOS) \
                and not (0xD0 <= marker <= 0xD7) and marker != M_TEM:
            out += raw[i:end]
            i = end
        else:
            out += raw[i:i + 2]
            i += 2
    return bytes(out), removed


def strip_jpeg_with_piexif(raw: bytes) -> Tuple[bytes, List[str]]:
    """piexif-based EXIF removal (alternative path; keeps ICC/XMP bookkeeping
    to us). Used when piexif import is available and caller only wants EXIF
    out — normally we prefer clean_jpeg which is fully deterministic."""
    removed: List[str] = []
    if piexif is not None:
        try:
            raw = piexif.remove(raw)
            removed.append("EXIF (via piexif)")
        except Exception as exc:
            removed.append(f"piexif remove failed: {exc}")
    return raw, removed
