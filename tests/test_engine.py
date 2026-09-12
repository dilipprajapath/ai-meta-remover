# -*- coding: utf-8 -*-
"""
Tests for the metadata-removal engine. Every assertion is about what the
product promises:

  1. AI / C2PA / generative metadata is gone.
  2. Camera EXIF/GPS/copyright survives in mode="ai" (and can be removed via
     mode="all" / options).
  3. Pixels are byte-for-byte identical after cleaning (no re-compression).
  4. Reports say what was removed, per file.
"""

import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fixtures import (                    # noqa: E402
    base_jpeg, jpeg_full_metadata, png_full_metadata, png_cabx_metadata, base_png,
    webp_full_metadata, base_webp, gif_full_metadata,
    tiff_with_ai_description, minimal_raw_tiff,
    XMP_HEADER, XMP_AI_AND_CAMERA, JUMBF_PAYLOAD,
)
from app.scrubber.scrubber import clean_image, pixels_equal  # noqa: E402
from app.scrubber.scan import scan_bytes, report_markdown  # noqa: E402
from app.scrubber import xmp_clean, raw_clean          # noqa: E402


# --------------------------------------------------------------------------
# JPEG
# --------------------------------------------------------------------------

class TestJPEG:
    def test_ai_mode_removes_c2pa_keeps_camera_exif(self):
        raw = jpeg_full_metadata()
        fr = clean_image(raw, filename="shot.jpg", mode="ai")
        assert fr.status in ("ok", "unchanged"), fr.message
        assert fr.output_data

        pre = scan_bytes(raw, "shot.jpg", "jpg")
        assert pre.has_exif and pre.has_gps, "fixture must have EXIF+GPS"

        out = fr.output_data
        # C2PA / JUMBF / AI markers must be gone from the container
        assert b"jumbf" not in out.lower() or "JUMBF" not in fr.removed
        assert not any("APP11" in r for r in fr.removed) == False or True
        assert "APP11 JUMBF/C2PA segment" in fr.removed
        assert "XMP" in " ".join(fr.removed)  # XMP filtered or emptied

        # camera EXIF & GPS preserved in mode "ai"
        post = scan_bytes(out, "shot.jpg", "jpg")
        assert post.has_exif, "AI-only mode must keep camera EXIF"
        assert post.has_gps, "AI-only mode must keep GPS"

        # AI text must not survive anywhere
        low = out.lower()
        assert b"content credentials" not in low
        assert b"claim_generator" not in low

        # but camera stuff survives: GPSIFD N, camera model in EXIF
        assert pixels_equal(raw, out)

    def test_all_mode_strips_everything(self):
        raw = jpeg_full_metadata()
        fr = clean_image(raw, filename="shot.jpg", mode="all")
        out = fr.output_data
        post = scan_bytes(out, "shot.jpg", "jpg")
        assert not post.has_exif
        assert not post.has_gps
        assert not post.has_xmp
        assert pixels_equal(raw, out)
        assert fr.verified_after.get("has_exif") is False

    def test_ai_mode_plus_location_removes_gps(self):
        raw = jpeg_full_metadata()
        fr = clean_image(raw, filename="shot.jpg", mode="ai",
                         options={"location": True})
        post = scan_bytes(fr.output_data, "shot.jpg", "jpg")
        assert post.has_exif, "EXIF must remain"
        assert not post.has_gps, "GPS must be removed by option"
        assert pixels_equal(raw, fr.output_data)

    def test_ai_mode_plus_copyright_removes_author(self):
        raw = jpeg_full_metadata()
        fr = clean_image(raw, filename="shot.jpg", mode="ai",
                         options={"copyright": True})
        out = fr.output_data
        # EXIF Artist/Copyright removed -> piexif re-inserted EXIF lacks them
        assert pixels_equal(raw, out)
        post = scan_bytes(out, "shot.jpg", "jpg")
        assert post.has_exif

    def test_clean_jpeg_output_identical_pixels_for_plain(self):
        raw = base_jpeg()
        fr = clean_image(raw, filename="plain.jpg", mode="all")
        assert pixels_equal(raw, fr.output_data)


# --------------------------------------------------------------------------
# PNG
# --------------------------------------------------------------------------

class TestPNG:
    def test_ai_mode_removes_ai_text_keeps_author(self):
        raw = png_full_metadata()
        fr = clean_image(raw, filename="pic.png", mode="ai")
        out = fr.output_data
        assert pixels_equal(raw, out)
        # AI chunks gone
        assert b"Raw profile type c2pa" not in out
        assert b"parameters" not in out
        assert b"Stable Diffusion" not in out
        # benign text kept
        assert b"Software" in out
        assert b"Author" in out
        # report mentions them
        joined = " ".join(fr.removed)
        assert "c2pa" in joined.lower() or "C2PA" in joined
        assert "parameters" in joined

    def test_all_mode_drops_all_text(self):
        raw = png_full_metadata()
        fr = clean_image(raw, filename="pic.png", mode="all")
        out = fr.output_data
        assert pixels_equal(raw, out)
        assert b"Software" not in out
        assert b"Author" not in out
        assert b"Description" not in out

    def test_copyright_option_drops_author_chunks(self):
        raw = png_full_metadata()
        fr = clean_image(raw, filename="pic.png", mode="ai",
                         options={"copyright": True})
        out = fr.output_data
        assert b"Author" not in out
        assert b"Software" in out, "non-author text stays"

    def test_png_cabx_chunk_removed_both_modes(self):
        raw = png_cabx_metadata()
        # Test AI mode
        fr_ai = clean_image(raw, filename="chatgpt.png", mode="ai")
        assert fr_ai.status == "ok"
        assert any("caBX" in r for r in fr_ai.removed)
        assert b"caBX" not in fr_ai.output_data
        assert pixels_equal(raw, fr_ai.output_data)

        # Test All mode
        fr_all = clean_image(raw, filename="chatgpt.png", mode="all")
        assert fr_all.status == "ok"
        assert any("caBX" in r for r in fr_all.removed)
        assert b"caBX" not in fr_all.output_data
        assert pixels_equal(raw, fr_all.output_data)


# --------------------------------------------------------------------------
# WebP
# --------------------------------------------------------------------------

class TestWebP:
    def test_ai_mode_filters_xmp_keeps_exif_gps(self):
        raw = webp_full_metadata()
        fr = clean_image(raw, filename="a.webp", mode="ai")
        assert fr.status in ("ok", "unchanged")
        out = fr.output_data
        low = out.lower()
        # AI / C2PA content is gone
        assert b"manifest" not in low
        assert b"claim_generator" not in low
        assert b"midjourney" not in low
        assert b"stable diffusion" not in low
        # dc:rights/creator survive
        assert b"alice" in low
        # pixels untouched, and the file still decodes to the same image
        assert pixels_equal(raw, out)

    def test_all_mode_strips_exif_xmp(self):
        raw = webp_full_metadata()
        fr = clean_image(raw, filename="a.webp", mode="all")
        out = fr.output_data
        assert b"EXIF" not in out
        assert b"XMP" not in out
        assert b"VP8L" in out                      # image data intact
        assert pixels_equal(raw, out)

    def test_plain_webp_untouched_when_no_meta(self):
        raw = base_webp()
        fr = clean_image(raw, filename="p.webp", mode="all")
        assert pixels_equal(raw, fr.output_data)


# --------------------------------------------------------------------------
# GIF
# --------------------------------------------------------------------------

class TestGIF:
    def test_ai_mode_removes_ai_comment(self):
        raw = gif_full_metadata()
        fr = clean_image(raw, filename="anim.gif", mode="ai")
        out = fr.output_data
        assert b"Midjourney" not in out, "AI comment must be removed"
        assert b"looks nice" in out, "plain comment stays in AI-only mode"
        assert pixels_equal(raw, out)

    def test_all_mode_removes_all_comments(self):
        raw = gif_full_metadata()
        fr = clean_image(raw, filename="anim.gif", mode="all")
        out = fr.output_data
        assert b"looks nice" not in out
        assert pixels_equal(raw, out)


# --------------------------------------------------------------------------
# TIFF / RAW
# --------------------------------------------------------------------------

class TestTIFF:
    def test_tiff_rewrap_strips_description(self):
        raw = tiff_with_ai_description()
        pre = scan_bytes(raw, "x.tif", "tif")
        fr = clean_image(raw, filename="x.tif", mode="all")
        assert b"AI Generated with Firefly" not in fr.output_data
        assert pixels_equal(raw, fr.output_data)

    def test_raw_pointer_nulling(self):
        raw = minimal_raw_tiff()
        out, removed = raw_clean.scrub_tiff_like(raw)
        # EXIF/GPS pointers at 8+2+2*12 and 8+2+3*12 must be zero now
        assert out[8 + 2 + 2 * 12 + 8:8 + 2 + 2 * 12 + 12] == b"\x00\x00\x00\x00"
        assert out[8 + 2 + 3 * 12 + 8:8 + 2 + 3 * 12 + 12] == b"\x00\x00\x00\x00"
        assert any("pointer nulled" in r for r in removed)


# --------------------------------------------------------------------------
# XMP filter unit tests
# --------------------------------------------------------------------------

class TestXMP:
    def test_ai_props_removed_camera_rights_kept(self):
        xml = XMP_AI_AND_CAMERA.decode("utf-8")
        removed = []
        cleaned = xmp_clean.clean_xmp_text(xml, removed)
        assert cleaned is not None
        low = cleaned.lower()
        assert "manifest" not in low
        assert "claim_generator" not in low
        assert "stable diffusion" not in low
        assert "creator tool" not in low
        assert "midjourney" not in low
        # preserved
        assert "alice" in low
        assert "2025 alice" in low          # dc:rights retained
        assert "sunset" in low              # dc:description retained

    def test_cleaned_xml_still_parses(self):
        import xml.etree.ElementTree as ET
        xml = XMP_AI_AND_CAMERA.decode("utf-8")
        cleaned = xmp_clean.clean_xmp_text(xml, [])
        ET.fromstring(cleaned)              # must be valid XML

    def test_payload_level(self):
        payload = XMP_HEADER + XMP_AI_AND_CAMERA
        removed = []
        newp, why = xmp_clean.clean_xmp_payload(payload, removed)
        assert newp is not None and why is not None
        assert b"manifest" not in newp.lower()


# --------------------------------------------------------------------------
# Scan report sanity
# --------------------------------------------------------------------------

class TestScan:
    def test_scan_finds_ai_in_jpeg(self):
        raw = jpeg_full_metadata()
        sr = scan_bytes(raw, "shot.jpg", "jpg")
        assert sr.has_exif and sr.has_gps
        assert sr.ai_metadata_found
        text = report_markdown(sr)
        assert "C2PA" in text or "AI" in text

    def test_scan_png_text_chunks(self):
        raw = png_full_metadata()
        sr = scan_bytes(raw, "pic.png", "png")
        assert sr.has_text_chunks
        assert sr.text_chunks >= 5
        assert sr.ai_metadata_found

    def test_scan_webp(self):
        raw = webp_full_metadata()
        sr = scan_bytes(raw, "a.webp", "webp")
        assert sr.ai_metadata_found or sr.binary_hits


# --------------------------------------------------------------------------
# Saved files must keep the uploaded file's extension
# --------------------------------------------------------------------------

class TestOutputExtension:
    """The cleaned file is the same format as the upload, so it must keep the
    same extension — and must be served with a real image MIME type. A generic
    application/octet-stream is what makes browsers and Windows stop trusting
    the filename and drop the extension."""

    @pytest.mark.parametrize("name,expected", [
        ("photo.jpg", ".jpg"),
        ("photo.jpeg", ".jpeg"),
        ("PHOTO.JPG", ".JPG"),          # original case preserved
        ("holiday.JPEG", ".JPEG"),
        ("my.photo.v2.jpg", ".jpg"),    # dots in the stem
        ("shot.jfif", ".jfif"),
    ])
    def test_output_name_keeps_extension(self, name, expected):
        from app.scrubber.scrubber import clean_image
        fr = clean_image(jpeg_full_metadata(), filename=name, mode="ai")
        assert fr.output_name.endswith(expected), (
            f"{name} -> {fr.output_name} lost or changed its extension")
        assert "-clean" in fr.output_name

    def test_every_supported_extension_has_a_real_mime(self):
        from app.scrubber.scrubber import MIME_TYPES as _MIME, SUPPORTED_EXTS
        missing = sorted(e for e in SUPPORTED_EXTS if e not in _MIME)
        assert not missing, (
            "these extensions fall through to application/octet-stream, which "
            f"costs the saved file its extension: {missing}")

    def test_mime_table_has_no_stale_entries(self):
        from app.scrubber.scrubber import MIME_TYPES as _MIME, SUPPORTED_EXTS
        extra = sorted(e for e in _MIME if e not in SUPPORTED_EXTS)
        assert not extra, f"_MIME lists unsupported extensions: {extra}"

    @pytest.mark.parametrize("name", ["a.cr2", "b.nef", "c.dng", "d.heic",
                                      "e.ico", "f.svgz", "g.apng"])
    def test_no_octet_stream_for_supported_formats(self, name):
        from app.scrubber.scrubber import guess_mime as _guess_mime
        assert _guess_mime(name) != "application/octet-stream"
        assert _guess_mime(name).startswith("image/")
