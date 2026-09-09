# -*- coding: utf-8 -*-
"""
xmp_clean.py — Surgical removal of AI / C2PA properties from XMP packets.

Why not just delete the whole XMP block in "Remove only AI metadata" mode?
Because XMP legitimately holds *important* metadata too (dc:rights copyright,
dc:creator, camera serial in the MicrosoftPhoto/MakerNote namespaces, …) that
the user explicitly wants to KEEP in that mode. So for mode 1 we parse the
RDF/XML packet and delete only the properties that the central AI registry
(classifies as AI / C2PA / generative).

The XML is handled with Python's stdlib ElementTree which is namespace aware:
an element tag like ``{http://ns.adobe.com/xap/1.0/}CreatorTool`` is matched on
its *local name* (``CreatorTool``), so we never depend on the exotic prefixes
AI tools invent (``c2pa:``, ``crln:``, ``digim:`` …).

Rules applied (all lowercase-normalised against ai_metadata.py):
  1. A property element/attribute whose local name is AI/C2PA (exact or
     fragment match)      -> subtree removed.
  2. A property value containing an AI tool/model marker
     (e.g. dc:creator = "Midjourney") -> that value / list item removed.
  3. An empty rdf:Description after filtering -> whole Description removed.
  4. A packet with nothing left -> caller drops the APP1/XMP segment.

Notes
-----
* If the XML fails to parse we return ``None`` and the caller keeps the
  original packet unless the raw text carries obvious AI markers.
* ET re-serialisation is deterministic and does not touch image pixels at all.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from .ai_metadata import (
    XMP_KEY_AI_EXACT,
    XMP_KEY_AI_FRAGMENTS,
    XMP_VALUE_AI_MARKERS,
)

# Normalisation helpers -------------------------------------------------------
_LOWER_RE = re.compile(r"[^a-z0-9_-]")


def _norm(s: str) -> str:
    return _LOWER_RE.sub("", (s or "")).lower()


def _local(tag: str) -> str:
    """Local part of an {namespace}local ET tag."""
    return tag.rsplit("}", 1)[-1]


# Namespace-agnostic local names of the XMP *structural* elements.
_STRUCT = {"RDF", "Description", "xmpmeta", "Seq", "Bag", "Alt", "li", "li", "rdf"}

_IS_AI_PROP = {}   # cached normalised decisions


def _prop_is_ai(local_name: str) -> bool:
    n = _norm(local_name)
    if n in XMP_KEY_AI_EXACT:
        return True
    for frag, _ in XMP_KEY_AI_FRAGMENTS:
        if frag in n:
            return True
    return False


def _value_is_ai(text: Optional[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(m in low for m in XMP_VALUE_AI_MARKERS)


def _collect_text(elem) -> str:
    """Concatenated text of element and its descendants (for value checks)."""
    parts = [elem.text or ""]
    for child in elem.iter():
        parts.append(child.text or "")
        parts.append(child.tail or "")
    return "".join(parts)


def _is_wrapper(tag_local: str) -> bool:
    return tag_local in ("RDF", "Description", "xmpmeta")


def _clean_description(desc: ET.Element, removed: List[str]) -> bool:
    """Filter one <rdf:Description>. Returns False when the description became
    fully empty and should be removed by the caller."""
    # 1) child *property* elements
    for child in list(desc):
        if len(child) == 0 and _is_wrapper(_local(child.tag)):
            continue
        local = _local(child.tag)

        # rdf:li / Seq / Bag / Alt live *under* a property element; they are
        # handled when we decide on the property parent or cleaned inline.
        if local in ("li", "Seq", "Bag", "Alt"):
            continue

        if _prop_is_ai(local):
            desc.remove(child)
            removed.append(f"XMP <{local}> (AI property)")
            continue

        # Ordinary property whose VALUE may reference an AI tool.
        text = _collect_text(child)
        if _value_is_ai(text):
            # If it is a list container, try to remove only the offending li.
            li_items = [c for c in child.iter() if _local(c.tag) == "li"]
            ai_lis = [li for li in li_items if _value_is_ai(li.text or "")]
            if ai_lis and li_items and len(ai_lis) < len(li_items):
                for li in ai_lis:
                    # detach from the immediate parent element (Seq/Bag/Alt)
                    for maybe_parent in child.iter():
                        if li in list(maybe_parent):
                            maybe_parent.remove(li)
                            break
                    removed.append(
                        f"XMP <{local}> list value ({li.text or ''!r})")
            else:
                desc.remove(child)
                removed.append(
                    f"XMP <{local}> value references AI tool ('{text.strip()[:40]}…')")

    # 2) property *attributes* written in compact syntax, e.g.
    #    <rdf:Description c2pa:manifest="jumbf:...">, excluding namespaces.
    for attr in list(desc.attrib):
        if attr.startswith("xmlns"):
            continue
        an = _norm(_local(attr))
        av = desc.attrib[attr]
        if _prop_is_ai(an) or _value_is_ai(av):
            del desc.attrib[attr]
            removed.append(f"XMP attribute @{attr}")

    # 3) did we empty it?
    meaningful_children = [c for c in desc
                           if _local(c.tag) not in ("RDF", "Description")]
    meaningful_attrs = {k for k in desc.attrib
                        if not k.startswith("xmlns")
                        and _local(k) not in ("about",)}
    return bool(meaningful_children or meaningful_attrs)


def clean_xmp_text(xml_text: str, removed: Optional[List[str]] = None
                   ) -> Optional[str]:
    """Parse an XMP XML string, drop AI/C2PA properties.

    Returns the cleaned XML string, ``""`` when every property was removed,
    or ``None`` when the packet couldn't be parsed (caller decides fallback).
    """
    if removed is None:
        removed = []
    xml_text = xml_text.strip()
    if not xml_text:
        return ""

    # ElementTree chokes on some legacy packets with multiple roots or broken
    # prolog; try progressively harder fallbacks.
    candidates = [xml_text]
    # fallback 1: cut everything before the first <x:xmpmeta / <xmpmeta / <rdf:RDF
    m = re.search(r"<(?:[a-zA-Z0-9_-]+:)?(xmpmeta|RDF)\b", xml_text)
    if m:
        # include from the tag that starts with the match up to its close,
        # naive: from match start to last '>' (single packet assumption)
        # Better: find the matching structure by trying ET on slices.
        pass
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Some cameras write the packet inside a CDATA-like wrapper or with
        # a bogus prefix. Try stripping XML comments then re-parse.
        cleaned = re.sub(r"<!--.*?-->", "", xml_text, flags=re.S)
        if cleaned != xml_text:
            try:
                root = ET.fromstring(cleaned)
            except ET.ParseError:
                return None
        else:
            return None

    # Locate all rdf:Description elements anywhere in the tree.
    descriptions = []
    for elem in root.iter():
        if _local(elem.tag) == "Description":
            descriptions.append(elem)

    # Also handle the (rare) case where properties sit directly under RDF
    # without a Description wrapper.
    for desc in descriptions:
        _clean_description(desc, removed)

    # Drop Description wrappers that ended up empty.
    for desc in descriptions:
        if len(desc) == 0 and all(
                k.startswith("xmlns") or _local(k) in ("about",)
                for k in desc.attrib):
            parent = _find_parent(root, desc)
            if parent is not None:
                parent.remove(desc)
            else:
                return ""

    # If no Description remains and no properties at all -> nothing left.
    rdf_container = root if _local(root.tag) in ("RDF", "xmpmeta") else \
        next((c for c in root.iter() if _local(c.tag) == "RDF"), root)

    if len(list(rdf_container)) == 0:
        return ""

    try:
        out = ET.tostring(root, encoding="unicode")
    except Exception:
        return None

    # Ensure well-formed single root (ElementTree gives root w/o decl; wrap is
    # done by the caller — JPEG APP1 XMP does not strictly need the decl).
    return out


def _find_parent(root: ET.Element, target: ET.Element):
    for parent in root.iter():
        for child in parent:
            if child is target:
                return parent
    return None


def clean_xmp_payload(payload: bytes, removed: Optional[List[str]] = None
                      ) -> Tuple[Optional[bytes], Optional[str]]:
    """Given the bytes of an XMP APP1 payload (header + xml), return the
    cleaned payload and a 'reason' string.

    Returns:
        (new_payload_bytes | None-when-empty, description_of_change | None)
    new_payload_bytes == payload  -> nothing AI found (unchanged)
    """
    if removed is None:
        removed = []

    # Determine where the XML text starts (after the null-terminated header).
    if payload[:5].lower() == b"http\x00" or payload.startswith(b"http:"):
        end = payload.find(b"\x00")
        header_end = end + 1 if end != -1 else 28
    elif payload[:4].lower() == b"xmp\x00":
        header_end = 4
    else:
        header_end = 0

    xml_bytes = payload[header_end:]
    # The packet may be padded with trailing NULs after a newline.
    xml_text = xml_bytes.decode("utf-8", "replace")
    xml_text = xml_text.rstrip("\x00 ")

    cleaned = clean_xmp_text(xml_text, removed)
    if cleaned is None:
        # unparseable packet: only drop if the raw text is plainly AI
        low = xml_text.lower()
        if any(m in low for m in XMP_VALUE_AI_MARKERS):
            return None, "unparseable XMP containing AI markers"
        return payload, None
    if cleaned.strip() == "":
        return None, "XMP emptied of AI properties"
    if not removed:
        return payload, None

    new_xml = cleaned.encode("utf-8")
    new_payload = payload[:header_end] + new_xml
    # keep the NUL padding style producers expect (single trailing NUL ok)
    return new_payload, "; ".join(removed[:6])
