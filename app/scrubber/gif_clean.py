# -*- coding: utf-8 -*-
"""
gif_clean.py — Lossless metadata removal for GIF (incl. animated).

GIF block grammar (kept intact; only *metadata* sub-blocks are dropped):

    header("GIF8xa") + LSD + [global colour table]
      then a sequence of:
        0x2C image descriptor (…)   <- never touched (pixels)
        0x21 0xF9 graphic control   <- never touched (frame timing)
        0x21 0xFE comment ext       <- metadata (handled)
        0x21 0xFF application ext   <- NETSCAPE loop / XMP / AI (handled)
        0x3B trailer

Comments / XMP live inside 0x21 extensions whose sub-blocks (length-prefixed,
terminated by 0x00) we parse to find their text.

Removal policy:
  * always (AI mode):   comment extensions whose text carries AI markers;
                        XMP application extensions carrying AI markers
  * 'remove all':       every comment extension + XMP application extension
                        (NETSCAPE/loop and other app extensions are kept so
                         animation behaviour is unchanged)
"""

from __future__ import annotations

from typing import List, Tuple

from .ai_metadata import XMP_VALUE_AI_MARKERS

GIF_HEADS = (b"GIF87a", b"GIF89a")
_XMP_APP_ID = b"XMP Data"          # Photoshop/Adobe GIF-XMP marker (8 bytes)


def _read_sub_blocks(raw: bytes, pos: int, end: int
                     ) -> Tuple[bytes, int]:
    """Consume length-prefixed sub-blocks until 0x00 terminator.
    Returns (concatenated_payload, position_after_terminator)."""
    payload = bytearray()
    while pos < end:
        size = raw[pos]
        if size == 0:
            return bytes(payload), pos + 1
        pos += 1
        if pos + size > end:
            break
        payload += raw[pos:pos + size]
        pos += size
    return bytes(payload), pos


def _write_sub_blocks(data: bytes) -> bytes:
    """Re-chunk arbitrary bytes into 255-byte GIF sub-blocks."""
    out = bytearray()
    i = 0
    while i < len(data):
        chunk = data[i:i + 255]
        out.append(len(chunk))
        out += chunk
        i += 255
    out.append(0x00)
    return bytes(out)


def clean_gif(raw: bytes, remove_all: bool = False,
              remove_ai: bool = True) -> Tuple[bytes, List[str]]:
    removed: List[str] = []
    if raw[:6] not in GIF_HEADS:
        raise ValueError("Not a GIF file")

    out = bytearray()
    n = len(raw)

    # ---- header + LSD + global colour table -------------------------------
    if n < 13:
        raise ValueError("Truncated GIF")
    lsd_size = 7
    flags = raw[10]
    gct_size = 3 * (2 ** ((flags & 0x07) + 1)) if flags & 0x80 else 0
    header_end = 6 + lsd_size + gct_size
    out += raw[:header_end]

    pos = header_end
    while pos < n:
        b0 = raw[pos]
        if b0 == 0x3B:                       # trailer
            out += b"\x3b"
            break
        if b0 == 0x2C:                       # image descriptor (+pixels)
            # walk to the end of this image's LZW data, then copy the whole
            # block verbatim (descriptor + LCT + pixel sub-blocks untouched)
            if pos + 10 > n:
                break
            img_start = pos
            lct_flag = raw[pos + 9] & 0x80
            lct_size = 3 * (2 ** ((raw[pos + 9] & 0x07) + 1)) if lct_flag else 0
            pos += 10 + lct_size
            if pos >= n:
                break
            pos += 1                          # LZW minimum code size byte
            while pos < n:
                sz = raw[pos]
                if sz == 0:
                    pos += 1
                    break
                pos += 1 + sz
            out += raw[img_start:pos]
            continue
        if b0 == 0x21:                       # extension
            if pos + 2 > n:
                break
            label = raw[pos + 1]
            body_start = pos + 2
            data, next_pos = _read_sub_blocks(raw, body_start, n)
            drop = False
            why = ""
            if label == 0xFE:                # comment extension
                text = data.decode("utf-8", "replace").lower()
                if remove_all:
                    drop, why = True, "GIF comment extension"
                elif remove_ai and any(m in text for m in XMP_VALUE_AI_MARKERS):
                    drop, why = True, "GIF comment containing AI marker"
            elif label == 0xFF:              # application extension
                if data[:8] == _XMP_APP_ID:
                    rest = data[8:].decode("utf-8", "replace").lower()
                    if remove_all:
                        drop, why = True, "GIF XMP application extension"
                    elif remove_ai and any(m in rest
                                           for m in XMP_VALUE_AI_MARKERS):
                        drop, why = True, "GIF XMP carrying AI markers"
            if drop:
                removed.append(why)
            else:
                out += raw[pos:next_pos]
            pos = next_pos
            continue
        # unexpected byte — copy it and move on (defensive)
        out.append(b0)
        pos += 1

    return bytes(out), removed
