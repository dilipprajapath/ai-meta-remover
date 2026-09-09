# -*- coding: utf-8 -*-
"""
scrubber.py — Orchestrator: one entry point for every supported image format.

Modes
-----
mode="ai"   Remove ONLY AI / C2PA / generative provenance metadata.
            Camera EXIF, GPS, copyright and other "important" metadata are
            preserved (unless the matching extra options are switched on).
mode="all"  Remove ALL metadata: EXIF, GPS, timestamps, XMP, IPTC, comments,
            thumbnails — a completely clean file.

Extra options (booleans, applied on top of mode="ai")
    location   : additionally remove GPS / geolocation data
    copyright  : additionally remove author / copyright / creator fields
    icc        : additionally remove the ICC colour profile
    thumbnail  : additionally remove embedded preview thumbnails

Result object (FileResult) is serialised per file for the web UI and for the
optional per-file .txt report that can be downloaded alongside the images.

Losslessness contract
---------------------
JPEG/PNG/WebP/GIF/BMP are edited structurally (segments/chunks dropped or
rewritten) — the encoded pixel stream is copied verbatim, so the decoded image
is pixel-identical and no re-compression ever happens. TIFF/RAW-family files
are either re-wrapped with the same pixel data + compression (no metadata) or
have their metadata IFD pointers nulled in place. Re-encoding is *never* used.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import (
    gif_clean,
    heif_clean,
    jpeg_clean,
    png_clean,
    raw_clean,
    scan as scanner,
    webp_clean,
)
from . import xmp_clean  # (indirect helper, kept for clarity)

# ---------------------------------------------------------------------------
# Format tables
# ---------------------------------------------------------------------------

JPEG_EXTS = {"jpg", "jpeg", "jpe", "jfif"}
PNG_EXTS = {"png", "apng"}
WEBP_EXTS = {"webp"}
GIF_EXTS = {"gif"}
BMP_EXTS = {"bmp", "dib"}
TIFF_EXTS = {"tif", "tiff", "dng"}
RAW_TIFF_EXTS = {"cr2", "nef", "arw", "orf", "rw2", "pef", "srw",
                 "erf", "3fr", "iiq", "mef", "nrw"}
RAW_OTHER_EXTS = {"cr3", "raf", "mrw", "x3f", "srw", "kdc", "dcr", "raw",
                  "raf"}                       # cr3 is ISO-BMFF (see heif)
HEIF_EXTS = {"heic", "heif", "avif", "heics", "heifs", "hif", "cr3"}
SVG_EXTS = {"svg", "svgz"}
# Formats we accept uploads for but treat with care (no metadata containers
# guaranteed): they get scanned + AI window scrub only when structural.
BEST_EFFORT_EXTS = RAW_OTHER_EXTS | {"ico", "jfif"}
SUPPORTED_EXTS = (JPEG_EXTS | PNG_EXTS | WEBP_EXTS | GIF_EXTS | BMP_EXTS |
                  TIFF_EXTS | RAW_TIFF_EXTS | RAW_OTHER_EXTS | HEIF_EXTS |
                  SVG_EXTS | {"jfif", "ico"})

# Human readable format name per extension
FORMAT_LABEL = {
    "jpg": "JPEG", "jpeg": "JPEG", "jpe": "JPEG", "jfif": "JPEG",
    "png": "PNG", "apng": "PNG (animated)",
    "webp": "WebP", "gif": "GIF", "bmp": "BMP", "dib": "BMP",
    "tif": "TIFF", "tiff": "TIFF", "dng": "DNG (RAW)",
    "cr2": "CR2 (Canon RAW)", "nef": "NEF (Nikon RAW)",
    "arw": "ARW (Sony RAW)", "orf": "ORF (Olympus RAW)",
    "rw2": "RW2 (Panasonic RAW)", "pef": "PEF (Pentax RAW)",
    "srw": "SRW (Samsung RAW)", "erf": "ERF (Epson RAW)",
    "3fr": "3FR (Hasselblad RAW)", "iiq": "IIQ (Phase One RAW)",
    "mef": "MEF (Mamiya RAW)", "nrw": "NRW (Nikon RAW)",
    "cr3": "CR3 (Canon RAW)", "raf": "RAF (Fujifilm RAW)",
    "mrw": "MRW (Minolta RAW)", "x3f": "X3F (Sigma RAW)",
    "heic": "HEIC", "heif": "HEIF", "avif": "AVIF", "hif": "HEIF",
    "heics": "HEIC", "heifs": "HEIF", "svg": "SVG", "svgz": "SVG (gzipped)",
    "ico": "ICO", "raw": "RAW",
}

# IPTC/EXIF/XMP field *local names* considered author/copyright (option).
_COPYRIGHT_LOCAL = {"rights", "copyright", "creator", "credit", "artist",
                    "owner", "byline", "source", "webstatement", "usage",
                    "usageterms", "licensor", "iptc", "copyrightowner",
                    "creatorworkemail"}


@dataclass
class FileResult:
    """One processed file's full outcome (serialised to JSON for the UI)."""
    id: str = ""
    original_name: str = ""
    extension: str = ""
    status: str = "ok"                 # ok | unchanged | skipped | error
    message: str = ""
    mode: str = "ai"
    output_name: str = ""
    output_bytes: int = 0
    original_bytes: int = 0
    # Cleaned payload. Excluded from JSON on purpose (binary, can be big);
    # the caller (server/CLI) writes it to the download/save location.
    output_data: bytes = b""
    # scan summary (before)
    scan: dict = field(default_factory=dict)
    # per-format removal detail
    removed: List[str] = field(default_factory=list)
    preserved: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    verified_after: dict = field(default_factory=dict)  # scan of output
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if k == "output_data":          # never ship binary over JSON
                continue
            if isinstance(v, set):
                d[k] = list(v)
            elif isinstance(v, (bytes, bytearray)):
                d[k] = "<binary>"
            else:
                d[k] = v
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_ext(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return ext


def _suffix_output(name: str, tag: str = "-clean") -> str:
    """photo.jpg -> photo-clean.jpg (unique collision handled by caller)."""
    p = Path(name)
    return f"{p.stem}{tag}{p.suffix}"


# ---------------------------------------------------------------------------
# Format implementations (each returns bytes + removal report)
# ---------------------------------------------------------------------------

def _clean_jpeg(raw, mode, opts):
    remove_all = mode == "all"
    out, removed = jpeg_clean.clean_jpeg(
        raw,
        remove_all=remove_all,
        remove_ai=True,
        strip_exif=False,           # handled below (EXIF kept in 'ai' mode)
        strip_xmp=True,
        strip_iptc=False,           # handled below
        strip_comment=True,
        strip_icc=opts.get("icc", False),
    )
    warnings: List[str] = []

    if remove_all:
        # clean_jpeg above keeps EXIF/XMP/IPTC unless remove_all also strips —
        # it does (remove_all is passed to the walker). Nothing else to do.
        pass

    # ---- AI-only refinements ------------------------------------------------
    if not remove_all:
        do_exif_edit = opts.get("location", False) or opts.get("copyright", False)
        if do_exif_edit:
            try:
                import piexif
                exif_dict = piexif.load(out)
                changed = False
                if opts.get("location") and exif_dict.get("GPS"):
                    exif_dict["GPS"] = {}
                    changed = True
                    removed.append("GPS IFD removed (location option)")
                if opts.get("copyright"):
                    # strip EXIF 0th: Artist(0x013B), Copyright(0x8298),
                    #  Exif: UserComment is *not* copyright so kept.
                    for ifd_name in ("0th", "Exif"):
                        tbl = exif_dict.get(ifd_name) or {}
                        for tag in (0x013B, 0x8298):
                            if tag in tbl:
                                del tbl[tag]
                                changed = True
                    if changed:
                        removed.append("EXIF Artist/Copyright removed (option)")
                    # XMP dc:rights/creator handled in walker via opts below
                if changed:
                    import io as _io
                    exif_bytes = piexif.dump(exif_dict)
                    bio = _io.BytesIO()
                    piexif.insert(exif_bytes, out, bio)
                    bio.seek(0)
                    out = bio.getvalue()
            except Exception as exc:
                warnings.append(f"EXIF refinement skipped: {exc}")
        if opts.get("copyright"):
            # IPTC APP13 -> drop whole block (documented behaviour)
            out, r2 = jpeg_clean._drop_appn(out, 13, "IPTC APP13 (copyright option)")
            removed += r2

    return out, removed, warnings


def _clean_png(raw, mode, opts):
    out, removed = png_clean.clean_png(
        raw,
        remove_all=(mode == "all"),
        remove_ai=True,
        strip_icc=opts.get("icc", False),
        strip_author=opts.get("copyright", False) and mode != "all",
        strip_location=opts.get("location", False) and mode != "all",
    )
    return out, removed, []


def _clean_webp(raw, mode, opts):
    remove_all = mode == "all"
    # when location/copyright requested in AI-only mode we must drop the EXIF
    # chunk wholly (WebP stores EXIF as one block) — same as EXIF removal.
    strip_exif = remove_all or opts.get("location", False) or opts.get(
        "copyright", False)
    out, removed = webp_clean.clean_webp(
        raw, remove_all=remove_all, remove_ai=True,
        strip_icc=opts.get("icc", False))
    if strip_exif and not remove_all:
        # second pass dropping the EXIF chunk that survived pass 1
        from . import webp_clean as wc
        try:
            out, r2 = wc.clean_webp(out, remove_all=True, remove_ai=False)
            removed += r2
        except Exception:
            pass
    return out, removed, []


def _clean_gif(raw, mode, opts):
    out, removed = gif_clean.clean_gif(raw, remove_all=(mode == "all"),
                                       remove_ai=True)
    return out, removed, []


def _clean_bmp(raw, mode, opts):
    # BMP has no standard metadata container. Informational only.
    warnings: List[str] = []
    removed: List[str] = []
    if mode == "all":
        warnings.append("BMP stores no standard EXIF/XMP metadata — nothing to "
                        "strip (only file header + DIB retained as-is).")
    return raw, removed, warnings


# ---------------------------------------------------------------------------
# Lossless pixel check helper
# ---------------------------------------------------------------------------

def pixels_equal(a: bytes, b: bytes) -> bool:
    """True when two image byte streams decode to identical pixel matrices.
    Used by tests to prove no quality/compression change."""
    import io
    from PIL import Image, ImageChops
    try:
        ia = Image.open(io.BytesIO(a)); ib = Image.open(io.BytesIO(b))
        if ia.size != ib.size or ia.mode != ib.mode:
            # try converting modes for fair compare
            ia = ia.convert("RGBA"); ib = ib.convert("RGBA")
        diff = ImageChops.difference(ia.convert("RGBA"),
                                     ib.convert("RGBA"))
        return diff.getbbox() is None
    except Exception:
        return False


def _clean_svg(raw, mode, opts):
    """Strip AI generator comments / metadata from SVG source, preserving the
    vector drawing. Best-effort (vector file, not raster)."""
    removed: List[str] = []
    try:
        import xml.etree.ElementTree as ET
    except Exception:
        ET = None
    text = raw.decode("utf-8", "replace")
    orig = text

    # 1) remove XML comments that mention AI tools/generators
    def _ai_comment(m):
        c = m.group(0).lower()
        from .ai_metadata import XMP_VALUE_AI_MARKERS
        return m.group(0) if not any(x in c for x in XMP_VALUE_AI_MARKERS) else ""
    cleaned = re.sub(r"<!--.*?-->", lambda m: _ai_comment(m), text,
                     flags=re.S)
    if cleaned != orig:
        removed.append("AI generator XML comments removed")
        text = cleaned

    # 2) remove <metadata> element contents that are AI-only
    if ET is not None and cleaned.strip():
        try:
            root = ET.fromstring(cleaned)
            ns = re.match(r"\{[^}]+\}", root.tag)
            meta_tag = (ns.group(0) + "metadata") if ns else "metadata"
            for meta in list(root.iter(meta_tag)):
                low = ET.tostring(meta, encoding="unicode").lower()
                from .ai_metadata import XMP_VALUE_AI_MARKERS
                if any(m in low for m in XMP_VALUE_AI_MARKERS):
                    root.remove(meta) if meta in list(root) else None
                    removed.append("<metadata> with AI markers removed")
            out_xml = ET.tostring(root, encoding="unicode")
            # keep the <?xml?> declaration & doctype by prepending if existed
            decl = re.match(r"^\s*(<\?xml[^>]*\?>)", orig)
            if decl:
                out_xml = decl.group(1) + "\n" + out_xml
            text = out_xml
        except Exception:
            pass

    if mode == "all":
        removed.append("SVG: comments/metadata removed (best effort)")
    return text.encode("utf-8"), removed, []


def _clean_tiff_family(raw, ext, mode, opts):
    """TIFF + DNG + TIFF-derived RAW."""
    warnings: List[str] = []
    removed: List[str] = []
    if mode == "all":
        try:
            out, r = raw_clean.clean_tiff(raw, remove_all=True,
                                          remove_ai=True,
                                          strip_icc=opts.get("icc", False))
            removed += r
            return out, removed, warnings
        except Exception as exc:
            warnings.append(f"TIFF re-wrap unavailable ({exc}); "
                            "falling back to in-place IFD nulling")
    # in-place null of metadata pointers (mode ai or rewrap failed)
    which = {"xmp", "iptc"}
    if opts.get("location", False) or mode == "all":
        which |= {"exif", "gps", "printim"}
    out, r = raw_clean.scrub_tiff_like(raw, which=which)
    removed += r
    if mode == "ai" and not r:
        removed.append("No AI/C2PA or (option-selected) metadata pointers found")
    return out, removed, warnings


def _clean_heif(raw, mode, opts):
    loc = opts.get("location", False)
    cop = opts.get("copyright", False)
    out, removed = heif_clean.clean_heif(
        raw, remove_all=(mode == "all"), remove_ai=True,
        strip_icc=opts.get("icc", False),
        strip_exif=(mode == "all" or loc or cop),
        strip_xmp=(mode == "all" or cop))
    warnings: List[str] = []
    if mode == "ai" and loc:
        warnings.append("HEIF stores EXIF as one item — removing GPS also "
                        "removes that camera-EXIF item (documented limit).")
    return out, removed, warnings


def _clean_raw_other(raw, mode, opts):
    """RAF/MRW/X3F — byte-signature scrub only (documented)."""
    removed: List[str] = []
    warnings: List[str] = [
        "This RAW layout has no public pure-Python metadata parser; only "
        "C2PA/AI byte signatures were scrubbed in place."]
    out, r = raw_clean.scrub_tiff_like(raw, which=set())
    removed += r
    return out, removed, warnings


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def clean_image(raw: bytes, filename: str = "", mode: str = "ai",
                options: Optional[Dict[str, bool]] = None,
                out_tag: str = "-clean") -> FileResult:
    """Clean one in-memory image. Pure function — never touches disk itself
    (the caller decides where output bytes go)."""
    import time
    opts = dict(options or {})
    fr = FileResult(
        id=uuid.uuid4().hex[:12],
        original_name=filename,
        extension=_safe_ext(filename),
        mode=mode,
        original_bytes=len(raw),
    )
    t0 = time.perf_counter()
    ext = fr.extension

    try:
        pre = scanner.scan_bytes(raw, filename, ext)
        fr.scan = pre.to_dict()

        if ext not in SUPPORTED_EXTS:
            fr.status = "skipped"
            fr.message = (f"'.{ext}' is not in the supported image list. "
                          "No changes were made.")
            fr.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return fr

        # route to per-format cleaner
        if ext in JPEG_EXTS:
            out, removed, warns = _clean_jpeg(raw, mode, opts)
        elif ext in PNG_EXTS:
            out, removed, warns = _clean_png(raw, mode, opts)
        elif ext in WEBP_EXTS:
            out, removed, warns = _clean_webp(raw, mode, opts)
        elif ext in GIF_EXTS:
            out, removed, warns = _clean_gif(raw, mode, opts)
        elif ext in BMP_EXTS:
            out, removed, warns = _clean_bmp(raw, mode, opts)
        elif ext in SVG_EXTS:
            out, removed, warns = _clean_svg(raw, mode, opts)
        elif ext in TIFF_EXTS or ext in RAW_TIFF_EXTS:
            out, removed, warns = _clean_tiff_family(raw, ext, mode, opts)
        elif ext in HEIF_EXTS:
            out, removed, warns = _clean_heif(raw, mode, opts)
        elif ext in RAW_OTHER_EXTS:
            out, removed, warns = _clean_raw_other(raw, mode, opts)
        else:
            out, removed, warns = raw, [], [f"'{ext}' handled conservatively"]

        fr.removed = removed
        fr.warnings = warns
        fr.output_data = bytes(out) if isinstance(out, (bytes, bytearray)) else out

        if out == raw and not removed:
            fr.status = "unchanged"
            fr.message = ("No matching metadata found — the file is already "
                          "clean (copied through untouched).")
        elif out == raw:
            fr.status = "unchanged"
            fr.message = "No removable metadata located."
        else:
            fr.status = "ok"

        # ---- verify with an independent re-scan of the output -------------
        try:
            post = scanner.scan_bytes(out, filename, ext)
            fr.verified_after = {
                "has_exif": post.has_exif,
                "has_gps": post.has_gps,
                "has_xmp": post.has_xmp,
                "has_iptc": post.has_iptc,
                "has_text_chunks": post.has_text_chunks,
                "ai_metadata_found": post.ai_metadata_found,
                "binary_hits": post.binary_hits,
                "notes": post.notes[:6],
            }
        except Exception:
            fr.verified_after = {}

        # Build preserved list for the report line.
        preserved = []
        if pre.has_exif and not fr.verified_after.get("has_exif"):
            preserved.append("EXIF removed")
        if pre.has_exif and fr.verified_after.get("has_exif"):
            preserved.append("camera EXIF preserved")
        if pre.has_gps and not fr.verified_after.get("has_gps"):
            preserved.append("GPS removed")
        fr.preserved = preserved

        fr.output_name = _suffix_output(filename, out_tag)
        fr.output_bytes = len(out)
        fr.message = fr.message or "Processed."
    except Exception as exc:
        fr.status = "error"
        fr.message = f"Processing failed: {exc}"
    fr.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return fr
