# -*- coding: utf-8 -*-
"""
tests/fixtures.py — builders that fabricate images *with* the metadata that
AI/C2PA pipelines and cameras write, so the test-suite can prove removal.

Every helper here is synthetic (generated in memory) — no copyrighted or real
user data is involved.
"""

from __future__ import annotations

import io
import struct
import zlib

from PIL import Image

import piexif

# ---------------------------------------------------------------------------
# low level splicing helpers
# ---------------------------------------------------------------------------

def seg(marker_code: int, payload: bytes) -> bytes:
    """A complete JPEG segment: FF <code> <be16 len> <payload>."""
    return b"\xff" + bytes([marker_code]) + struct.pack(">H", 2 + len(payload)) + payload


def jpeg_insert_before_sos(raw: bytes, inserts: list) -> bytes:
    """Insert full segments (bytes) just before the first SOS marker."""
    i = 2
    n = len(raw)
    out = bytearray(raw[:2])
    while i < n - 1:
        if raw[i] != 0xFF:
            out.append(raw[i]); i += 1; continue
        m = raw[i + 1]
        if m == 0xDA:                       # SOS
            for ins in inserts:
                out += ins
            out += raw[i:]
            return bytes(out)
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            out += raw[i:i + 2]; i += 2; continue
        length = struct.unpack(">H", raw[i + 2:i + 4])[0]
        out += raw[i:i + 2 + length]
        i += 2 + length
    return bytes(out)


def png_insert_before_idat(raw: bytes, chunks: list) -> bytes:
    """chunks = list of (4-byte-type, data). CRC computed automatically."""
    out = bytearray(raw[:8])
    i = 8
    n = len(raw)
    while i < n:
        (ln,) = struct.unpack(">I", raw[i:i + 4])
        ctype = raw[i + 4:i + 8]
        if ctype == b"IDAT":
            for t, d in chunks:
                body = t + d
                out += struct.pack(">I", len(d)) + body + \
                    struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            out += raw[i:]
            return bytes(out)
        out += raw[i:i + 12 + ln]
        i += 12 + ln
    raise ValueError("no IDAT")


def txt_chunk(kind: str, data: bytes):
    return (kind.encode("latin-1"), data)


# ---------------------------------------------------------------------------
# Sample metadata payloads
# ---------------------------------------------------------------------------

XMP_AI_AND_CAMERA = b"""<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:c2pa="http://c2pa.org/ns#3"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    c2pa:manifest="jumbf:urn:c2pa:1:xxxx"
    xmp:CreatorTool="Stable Diffusion WebUI">
   <dc:creator>
    <rdf:Seq><rdf:li>Alice Photographer</rdf:li><rdf:li>Midjourney</rdf:li></rdf:Seq>
   </dc:creator>
   <dc:rights>
    <rdf:Alt><rdf:li xml:lang="x-default">(c) 2025 Alice</rdf:li></rdf:Alt>
   </dc:rights>
   <dc:description>
    <rdf:Alt><rdf:li xml:lang="x-default">A sunset</rdf:li></rdf:Alt>
   </dc:description>
   <c2pa:claim_generator>stable diffusion</c2pa:claim_generator>
   <c2pa:assertions><rdf:Bag><rdf:li>training</rdf:li></rdf:Bag></c2pa:assertions>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"

JUMBF_PAYLOAD = (b"JUMBF" + b"\x00\x00\x00\x1e" + b"jumb" +
                 b'\x00\x00\x00\x0e' + b"c2pa" + b"manifest-c2pa.org-sample")

IPTC_PAYLOAD = (b"Photoshop 3.0\x00" + b"8BIM\x04\x04\x00\x00\x00\x00" +
                b"\x1c\x02\x05\x00\x01AI" + b"\x1c\x02\x50\x00\x01X")

AI_COMMENT = b"Generated with Midjourney v6, prompt foo"
PLAIN_COMMENT = b"looks nice"


# ---------------------------------------------------------------------------
# Format builders
# ---------------------------------------------------------------------------

def base_jpeg(width=320, height=200, color=(200, 120, 40)):
    im = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _piexif_insert(raw: bytes, exif_bytes: bytes) -> bytes:
    """piexif.insert(..., new_file=BytesIO) returns bytes."""
    bio = io.BytesIO()
    piexif.insert(exif_bytes, raw, bio)
    bio.seek(0)
    return bio.getvalue()


def jpeg_full_metadata() -> bytes:
    """A JPEG carrying: camera EXIF w/ GPS, AI XMP, JUMBF APP11, IPTC,
    AI comment, camera model etc."""
    raw = base_jpeg()

    # --- EXIF with GPS via piexif -----------------------------------------
    exif = {
        "0th": {
            piexif.ImageIFD.Make: "TestCamera Inc",
            piexif.ImageIFD.Model: "X-300 Pro",
            piexif.ImageIFD.Software: "Firmware 1.2",
            piexif.ImageIFD.Artist: "Alice",
            piexif.ImageIFD.Copyright: "(c) 2025 Alice",
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2025:06:01 12:00:00",
            piexif.ExifIFD.LensMake: "PrimeLens",
        },
        "GPS": {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((10, 1), (20, 1), (3000, 100)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((77, 1), (30, 1), (0, 1)),
        },
    }
    raw = _piexif_insert(raw, piexif.dump(exif))

    inserts = [
        seg(0xE1, XMP_HEADER + XMP_AI_AND_CAMERA),   # APP1 XMP
        seg(0xEB, JUMBF_PAYLOAD),                     # APP11 JUMBF/C2PA
        seg(0xED, IPTC_PAYLOAD),                      # APP13 IPTC
        seg(0xFE, AI_COMMENT),                        # COM (AI text)
        seg(0xFE, PLAIN_COMMENT),                     # COM (plain)
    ]
    return jpeg_insert_before_sos(raw, inserts)


def base_png() -> bytes:
    im = Image.new("RGBA", (64, 48), (10, 200, 100, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def png_full_metadata() -> bytes:
    raw = base_png()
    c2pa_json = (b'{"c2pa": {"manifest": {"claim_generator": "Stable Diffusion",'
                 b'"title": "content credentials sample"}}}')
    chunks = [
        txt_chunk("tEXt", b"Raw profile type c2pa\x00" + c2pa_json),
        txt_chunk("tEXt", b"parameters\x00Steps: 25, CFG scale: 7, "
                          b"Sampler: Euler a, Seed: 1234, Model hash: abc"),
        txt_chunk("tEXt", b"Software\x00GIMP 2.10"),
        txt_chunk("tEXt", b"Author\x00Alice"),
        txt_chunk("tEXt", b"Description\x00My image"),
    ]
    return png_insert_before_idat(raw, chunks)


def png_cabx_metadata() -> bytes:
    raw = base_png()
    jumbf = b"\x00\x00\x00\x1ejumb\x00\x00\x00\x12jumdc2pa\x00\x11\x00\x10\x80\x00\x00\xaa\x008\x9bq\x03c2pa"
    chunks = [
        txt_chunk("caBX", jumbf),
    ]
    return png_insert_before_idat(raw, chunks)


def webp_full_metadata() -> bytes:
    """A standards-valid *extended* lossless WebP with EXIF (incl. GPS) and an
    XMP packet that mixes AI/C2PA provenance with normal dc: rights/creator.
    Built via Pillow/libwebp itself so decoders accept the fixture."""
    import re
    im = Image.new("RGB", (96, 72), (30, 60, 200))
    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Make: "TestCam",
                piexif.ImageIFD.Model: "X-300"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2025:06:01 12:00:00"},
        "GPS": {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((10, 1), (20, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((77, 1), (30, 1), (0, 1)),
        },
    })
    xmp = re.sub(rb"<\?xpacket[^>]*\?>", b"", XMP_AI_AND_CAMERA).strip()
    buf = io.BytesIO()
    im.save(buf, "WEBP", lossless=True, exif=exif, xmp=xmp)
    return buf.getvalue()


def base_webp() -> bytes:
    im = Image.new("RGB", (96, 72), (30, 60, 200))
    buf = io.BytesIO()
    im.save(buf, "WEBP", lossless=True)
    return buf.getvalue()


def _riff_chunk(cid: bytes, data: bytes) -> bytes:
    out = cid + struct.pack("<I", len(data)) + data
    if len(data) & 1:
        out += b"\x00"
    return out


def gif_full_metadata() -> bytes:
    im = Image.new("P", (40, 30))
    im.putpalette([0, 0, 0] * 256)
    for x in range(40):
        for y in range(30):
            im.putpixel((x, y), (x * 3) % 256)
    buf = io.BytesIO()
    im.save(buf, "GIF")
    raw = buf.getvalue()
    assert raw.rstrip(b"\x00").endswith(b"\x3b"), "trailer expected"
    # insert extensions before the trailer
    def comment(text: bytes) -> bytes:
        return b"\x21\xfe" + bytes([len(text)]) + text + b"\x00"
    ext = comment(AI_COMMENT) + comment(PLAIN_COMMENT)
    return raw[:-1] + ext + b"\x3b"


def tiff_with_ai_description() -> bytes:
    im = Image.new("RGB", (50, 40), (5, 90, 170))
    buf = io.BytesIO()
    tiffinfo = {270: "AI Generated with Firefly", 305: "Firmware",
                315: "Alice", 33432: "TestCam"}
    im.save(buf, "TIFF", compression="tiff_lzw", tiffinfo=tiffinfo)
    return buf.getvalue()


def minimal_raw_tiff() -> bytes:
    """Hand-built little-endian TIFF with EXIF/GPS IFD pointers at 200/300."""
    out = bytearray(400)
    out[0:2] = b"II"
    out[2:4] = b"\x2a\x00"
    out[4:8] = struct.pack("<I", 8)          # IFD0 at offset 8
    ifd0 = 8
    entries = [
        (0x0100, 4, 1, 100),                 # ImageWidth
        (0x0101, 4, 1, 50),                  # ImageLength
        (0x8769, 4, 1, 200),                 # ExifIFD pointer
        (0x8825, 4, 1, 300),                 # GPS IFD pointer
    ]
    out[ifd0:ifd0 + 2] = struct.pack("<H", len(entries))
    p = ifd0 + 2
    for tag, typ, cnt, val in entries:
        out[p:p + 2] = struct.pack("<H", tag)
        out[p + 2:p + 4] = struct.pack("<H", typ)
        out[p + 4:p + 8] = struct.pack("<I", cnt)
        out[p + 8:p + 12] = struct.pack("<I", val)   # value/offset
        p += 12
    out[p:p + 4] = struct.pack("<I", 0)      # no next IFD
    # EXIF table at 200
    out[200:202] = struct.pack("<H", 1)
    out[202:214] = struct.pack("<HHII", 0x9000, 7, 4, 0x30303230)
    # GPS table at 300
    out[300:302] = struct.pack("<H", 2)
    out[302:314] = struct.pack("<HHII", 0x0001, 2, 2, 0x4E00)     # 'N\0'
    out[314:326] = struct.pack("<HHII", 0x0002, 5, 3, 0x313233)
    return bytes(out)
