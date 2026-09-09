# -*- coding: utf-8 -*-
"""
scrubber package — the metadata removal engine.

Public API (used by the web server, the CLI, and the test-suite):

    from app.scrubber.scan import scan_file, scan_bytes, report_markdown
    from app.scrubber.scrubber import clean_image, FileResult
    from app.scrubber.scrubber import MODES, OPTION_DEFAULTS
"""

from . import ai_metadata            # registry of AI/C2PA signatures
from . import xmp_clean, png_clean, jpeg_clean, gif_clean, webp_clean
from . import heif_clean, raw_clean
from .scan import scan_file, scan_bytes, report_markdown, ScanResult
from .scrubber import clean_image, FileResult, SUPPORTED_EXTS, FORMAT_LABEL

# Mode ids used across UI + backend + CLI
MODES = ("ai", "all")
MODE_LABELS = {
    "ai": "Remove only AI / C2PA metadata",
    "all": "Remove all metadata (EXIF, GPS, timestamps, copyright, ...)",
}
# Option names (+ defaults) exposed as checkboxes in the UI
OPTION_DEFAULTS = {
    "location": False,   # strip GPS when mode == 'ai'
    "copyright": False,  # strip author/copyright fields when mode == 'ai'
    "icc": False,        # strip ICC colour profile
}

__all__ = [
    "ai_metadata", "xmp_clean", "png_clean", "jpeg_clean", "gif_clean",
    "webp_clean", "heif_clean", "raw_clean",
    "scan_file", "scan_bytes", "report_markdown", "ScanResult",
    "clean_image", "FileResult", "SUPPORTED_EXTS", "FORMAT_LABEL",
    "MODES", "MODE_LABELS", "OPTION_DEFAULTS",
]
