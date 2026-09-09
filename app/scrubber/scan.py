# -*- coding: utf-8 -*-
"""
scan.py — Non-destructive metadata *inspection* for any uploaded image.

Every file passes through `scan_file()` before anything is rewritten. It
answers three questions that drive the whole pipeline and the UI:

  1. What metadata exists at all?            (exif / xmp / iptc / icc / text /
                                              png-ancillary / makernote / gps …)
  2. Is any of it AI / C2PA / provenance?    (so the default "Remove only AI
                                              metadata" mode knows what to do)
  3. What byte-level AI signatures exist?    (C2PA/JUMBF blocks buried in the
                                              container, independent of which
                                              tag parser reports them)

The output of a scan is a dict that is also what the HTTP layer serialises to
the frontend for the "found / will be removed" preview table.

RULES OF THE ROAD
-----------------
* `scan_file()` NEVER writes anything. It only opens the file for reading.
* Every decision uses the central registries from `ai_metadata.py`, so the
  report and the remover agree about what "AI metadata" means.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any

# Pillow lazy-imported inside functions on purpose: importing Image at module
# top costs ~300ms and the server may want to start fast. Pillow's Exif tags
# used here are safe in both Pillow 10 and 11.
from PIL import Image

from .ai_metadata import (
    BINARY_AI_SIGNATURES,
    AI_ONLY_SIGNATURES,
    XMP_KEY_AI_EXACT,
    XMP_KEY_AI_FRAGMENTS,
    XMP_VALUE_AI_MARKERS,
    JPEG_AI_APP_MARKERS,
    IPTC_AI_FIELD_NAMES,
)

# Pillow tag tables ----------------------------------------------------------
try:
    from PIL.ExifTags import Base as _BASE  # Pillow >= 9.2
    EXIF_IFD_TAG_NAMES = {v: k for k, v in vars(_BASE).items()
                          if isinstance(v, int)}
    GPS_IFD_NAME = "GPSInfo"
except Exception:                       # pragma: no cover (very old Pillow)
    EXIF_IFD_TAG_NAMES = {}
    GPS_IFD_NAME = "GPSInfo"


_LOWER_RE = re.compile(r"[^a-z0-9_-]")
_XMP_TAG_START = re.compile(r'<([a-z0-9_]+:)?([a-z0-9_\-]+)\b',
                            re.IGNORECASE)


def _norm(text: str) -> str:
    """Lowercase + strip non-alphanumeric so 'AI Generated', 'AI-Generated'
    and 'Ai_generated' all compare equal."""
    return _LOWER_RE.sub("", text or "").lower()


def _tag_normalised(tag: str) -> str:
    """Split 'c2pa:claim_generator' -> 'claimgenerator' (norm)."""
    base = tag.split(":")[-1] if ":" in tag else tag
    return _norm(base)


@dataclass
class ScanResult:
    """Everything discovered about one file (serialised to the frontend)."""
    filename: str
    extension: str
    size_bytes: int
    dimensions: Optional[str] = None    # e.g. "1920 x 1080" (filled by scan)
    mode: str = ""                      # Pillow mode, informational
    ai_metadata_found: bool = False     # headline boolean for the UI
    # ---- counts / flags of classic metadata -------------------------------
    has_exif: bool = False
    exif_fields: int = 0
    has_gps: bool = False
    gps_fields: int = 0
    has_xmp: bool = False
    xmp_tags: int = 0
    has_iptc: bool = False
    iptc_fields: int = 0
    has_icc: bool = False
    has_thumbnail: bool = False
    has_text_chunks: bool = False       # PNG tEXt/iTXt/zTXt present
    text_chunks: int = 0
    has_comments: bool = False          # GIF comment extension / JPEG COM
    # ---- AI / C2PA specific ----------------------------------------------
    ai_signatures_found: List[str] = field(default_factory=list)
    xmp_ai_tags: List[str] = field(default_factory=list)
    binary_hits: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # human-readable findings

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Trim anything that isn't useful on the wire.
        return d


# ---------------------------------------------------------------------------
# Low level helpers
# ---------------------------------------------------------------------------

def _pillow_exif_breakdown(exif_ifd) -> tuple:
    """Return (field_count, gps_count, has_thumbnail)."""
    fields = 0
    gps = 0
    thumb = False
    if exif_ifd is None:
        return fields, gps, thumb
    try:
        # Some producers store a nested thumbnail tag dict inside Exif IFD.
        for key in exif_ifd:
            fields += 1
            if key == 37500:            # MakerNote — just counted, not AI
                continue
        gps_ifd = exif_ifd.get_ifd(0x8825) if hasattr(exif_ifd, "get_ifd") else None
        if gps_ifd:
            gps = len(gps_ifd)
        # Thumbnail?
        if hasattr(exif_ifd, "get_ifd"):
            try:
                ifd1 = exif_ifd.get_ifd(1)      # thumbnail IFD
                if ifd1:
                    thumb = True
            except Exception:
                pass
    except Exception:
        pass
    return fields, gps, thumb


def _xmp_scan(im: Image.Image) -> tuple:
    """Return (xmp_tag_count, list_of_ai_xmp_tags)."""
    xmp = None
    # Pillow 10.4: getxmp() requires 'xmp' extra. Installed via requirements.
    try:
        xmp = im.getxmp()
    except Exception:
        try:
            import pillow_heif  # noqa: F401  (optional extra for .heic scans)
            xmp = im.getxmp()
        except Exception:
            return 0, []
    if not xmp:
        return 0, []

    ai_tags: List[str] = []
    total = 0

    def walk(node, depth=0):
        nonlocal total
        if depth > 8:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                total += 1
                key = _tag_normalised(str(k))
                if key in XMP_KEY_AI_EXACT:
                    ai_tags.append(f"{k} ({XMP_KEY_AI_EXACT[key]})")
                elif key:
                    for frag, label in XMP_KEY_AI_FRAGMENTS:
                        if frag in key:
                            ai_tags.append(f"{k} ({label})")
                            break
                # Value based detection (keys can be garbage; tool values live
                # in *values* e.g. dc:creator -> "Midjourney").
                val_hits = _xmp_value_markers(v)
                for vh in val_hits:
                    ai_tags.append(f"{k} -> value contains '{vh}'")
                walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(xmp)
    # De-duplicate while preserving order.
    seen = set()
    dedup = []
    for t in ai_tags:
        if t not in seen:
            seen.add(t)
            dedup.append(t)
    return total, dedup


def _xmp_value_markers(value) -> List[str]:
    """Look for AI tool/model signatures inside an XMP property *value*."""
    hits: List[str] = []
    if isinstance(value, str):
        low = value.lower()
        for marker in XMP_VALUE_AI_MARKERS:
            if marker in low:
                hits.append(marker)
                # keep it short: 1-2 markers per value is plenty
                if len(hits) >= 2:
                    break
    elif isinstance(value, (list, tuple)):
        for v in value:
            hits.extend(_xmp_value_markers(v))
            if len(hits) >= 4:
                break
    return hits


def _binary_signature_scan(raw: bytes) -> List[str]:
    """Case-insensitive byte search for C2PA/JUMBF/AI-tool signatures.

    Implemented as a lower-cased whole-buffer scan. Buffers are image files
    only (a few MB); a single pass is fine.
    """
    low = raw.lower()
    hits: List[str] = []
    for sig in BINARY_AI_SIGNATURES:
        if sig in low:
            hits.append(sig.decode("ascii"))
    for sig in AI_ONLY_SIGNATURES:
        if sig in low:
            hits.append(sig.decode("ascii"))
    return hits


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_bytes(raw: bytes, filename: str = "", ext: str = "") -> ScanResult:
    """Scan raw image bytes. Returns a ScanResult (no writes)."""
    ext = (ext or Path(filename).suffix).lower().lstrip(".")
    sr = ScanResult(filename=filename, extension=ext or "bin",
                    size_bytes=len(raw))
    if not raw:
        sr.notes.append("Empty file.")
        return sr

    # 1) Byte-level signature scan (format independent).
    try:
        sr.binary_hits = _binary_signature_scan(raw)
    except Exception:
        sr.binary_hits = []

    # 2) Container-level scan via Pillow where possible.
    fmt = None
    try:
        im = Image.open(io.BytesIO(raw))
        fmt = (im.format or "").upper()
        sr.mode = f"{fmt} / {im.mode}"
        try:
            w, h = im.size
            sr.dimensions = f"{w} x {h}"
        except Exception:
            pass

        # ---- EXIF --------------------------------------------------------
        try:
            exif = im.getexif()
            fields, gps, thumb = _pillow_exif_breakdown(exif)
            sr.exif_fields = fields
            sr.has_gps = gps > 0
            sr.gps_fields = gps
            sr.has_thumbnail = thumb
            sr.has_exif = (fields > 0) or (gps > 0)
        except Exception:
            pass

        # ---- XMP ----------------------------------------------------------
        try:
            n, ai = _xmp_scan(im)
            sr.xmp_tags = n
            sr.xmp_ai_tags = ai[:12]
            sr.has_xmp = n > 0
        except Exception:
            pass

        # ---- PNG chunks (C2PA / text / EXIF) -----------------------------
        if fmt == "PNG" or ext in ("png", "apng"):
            try:
                from .png_clean import _chunk_iter, _text_content, _C2PA_CHUNK_TYPES
                for ctype, data, off, length in _chunk_iter(raw):
                    if ctype in _C2PA_CHUNK_TYPES:
                        sr.ai_signatures_found.append(
                            f"PNG '{ctype}' chunk ({_C2PA_CHUNK_TYPES[ctype]})")
                    elif len(data) >= 8 and (data[4:8] == b"jumb" or (b"jumb" in data[:64].lower() and b"c2pa" in data[:64].lower())):
                        sr.ai_signatures_found.append(
                            f"PNG '{ctype}' chunk (JUMBF / C2PA container)")
                    elif ctype == "eXIf":
                        sr.has_exif = True
                    elif ctype in ("tEXt", "zTXt", "iTXt"):
                        sr.text_chunks += 1
                        sr.has_text_chunks = True
                        key, val = _text_content(data, ctype)
                        kk = _tag_normalised(str(key or ""))
                        low_val = (val or "").lower()
                        if any(frag in kk for frag, _ in XMP_KEY_AI_FRAGMENTS) \
                                or (key and key.lower() in ("c2pa", "dgi", "jumbf", "parameters", "prompt", "workflow", "cabx", "caci")):
                            sr.ai_signatures_found.append(f"PNG text chunk '{key}' (AI-related)")
                        elif any(m in low_val for m in XMP_VALUE_AI_MARKERS):
                            sr.ai_signatures_found.append(f"PNG text chunk '{key}' contains AI marker")
                        elif ("xmp" in kk or "xml" in kk) and any(m in low_val for m in XMP_VALUE_AI_MARKERS):
                            sr.ai_signatures_found.append(f"PNG XMP chunk contains AI metadata")
                if sr.text_chunks:
                    sr.notes.append(f"PNG text chunks present: {sr.text_chunks}")
            except Exception:
                pass

        # ---- GIF comment extension ---------------------------------------
        if fmt == "GIF":
            try:
                info = im.info or {}
                comment = info.get("comment")
                if comment:
                    sr.has_comments = True
                    low = (comment.decode("utf-8", "ignore")
                           if isinstance(comment, (bytes, bytearray))
                           else str(comment)).lower()
                    for marker in XMP_VALUE_AI_MARKERS:
                        if marker in low:
                            sr.ai_signatures_found.append(
                                f"GIF comment contains '{marker}'")
                            break
            except Exception:
                pass

        # ---- ICC profile ---------------------------------------------------
        try:
            icc = im.info.get("icc_profile")
            sr.has_icc = bool(icc)
        except Exception:
            pass

        # ---- Everything else (TIFF pages, WebP chunks) via raw scan -------
    except Exception as exc:                     # not an image Pillow knows
        sr.notes.append(f"Pillow could not decode: {exc}")

    # 3) JPEG APP-marker analysis (needs raw bytes, done here for all fmts to
    #    keep a single pipeline).
    if ext in ("jpg", "jpeg", "jpe", "jfif"):
        from . import jpeg_clean as _jc
        app11, app2_jumbf, xmp_present, com_present = _jc._inspect_appn(raw)
        if app11:
            sr.ai_signatures_found.append(
                f"JPEG APP11 marker (JUMBF/C2PA container, {app11} bytes)")
        if app2_jumbf:
            sr.ai_signatures_found.append("JPEG APP2 'JUMBF' segment (C2PA)")
        if xmp_present:
            sr.has_xmp = True
        if com_present:
            sr.has_comments = True
        # Count EXIF APP1 -> recheck has_exif from markers (Pillow may miss).
        if b"\xff\xe1" in raw:
            sr.has_exif = True

    # 4) Final AI verdict.
    ai_names = list(sr.ai_signatures_found)
    ai_names += sr.xmp_ai_tags
    c2pa_binary = [b for b in sr.binary_hits if b in (
        "c2pa", "jumbf", "content credentials", "cai",
        "raw profile type c2pa", "raw profile type dgi",
        "c2pa_manifest", "jumbf boxes", "cabx", "caci"
    )]
    if c2pa_binary:
        sr.notes.append(f"C2PA / Content Credentials provenance block detected ({', '.join(c2pa_binary[:3])}).")
        if not ai_names:
            ai_names.append(f"C2PA provenance signature ({c2pa_binary[0]})")
    sr.ai_metadata_found = bool(ai_names or sr.xmp_ai_tags)
    return sr


def scan_file(path) -> ScanResult:
    """Open a file path and scan it. Raises OSError if unreadable."""
    p = Path(path)
    raw = p.read_bytes()
    return scan_bytes(raw, filename=p.name)


def report_markdown(sr: ScanResult) -> str:
    """Human-readable summary (used in tests / console)."""
    lines = [
        f"File     : {sr.filename} ({sr.size_bytes} bytes)",
        f"Type     : .{sr.extension}",
    ]
    if sr.dimensions:
        lines.append(f"Size     : {sr.dimensions}")
    lines.append(f"EXIF     : {'yes (' + str(sr.exif_fields) + ' fields)' if sr.has_exif else 'no'}"
                 + (f", GPS: yes ({sr.gps_fields})" if sr.has_gps else ""))
    lines.append(f"XMP      : {'yes (' + str(sr.xmp_tags) + ' nodes)' if sr.has_xmp else 'no'}")
    lines.append(f"IPTC     : {'yes' if sr.has_iptc else 'no'}")
    lines.append(f"ICC      : {'yes' if sr.has_icc else 'no'}")
    lines.append(f"Thumbnail: {'yes' if sr.has_thumbnail else 'no'}")
    lines.append(f"Text     : {'yes (' + str(sr.text_chunks) + ' chunks)' if sr.has_text_chunks else 'no'}")
    ai = sr.ai_signatures_found + sr.xmp_ai_tags
    lines.append(f"AI/C2PA  : {'YES -> ' + '; '.join(ai[:10]) if ai else 'none found'}")
    return "\n".join(lines)
