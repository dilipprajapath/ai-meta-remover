# -*- coding: utf-8 -*-
"""
ai_metadata.py — Central registry of "what counts as AI / C2PA metadata".

Every rule table in this file is the single source of truth used by the whole
scanner + stripper pipeline. Keeping them here means the removal logic and the
"what was found / removed" per-file report can never drift apart.

Why a registry instead of one big regex?
  1. XMP is case-insensitive and full of namespaced property names
     (e.g. `c2pa:claim_generator`, `xmp:CreatorTool`). We normalise the raw
     packet and then test *normalised* strings against *normalised* patterns,
     which kills the case problem completely.
  2. C2PA / Content Credentials live in dedicated binary structures (JUMBF,
     `dgi:`, `JUMBF` uuid atoms, PNG iTXt keyed `Raw profile type c2pa`,
     `PIL`-style prefixes...) and cannot be found by scanning the byte stream
     cheaply everywhere — some formats need targeted binary removal (see
     jpeg_clean.py / png_clean.py / others).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1) BINARY MARKERS — searched in raw bytes (case-insensitive where sensible).
#    These are format-independent signatures of provenance / AI-tool data.
# ---------------------------------------------------------------------------

# Byte signatures of the C2PA / JUMBF ecosystem (lowercased for matching).
BINARY_AI_SIGNATURES = (
    b"c2pa",           # C2PA manifest (also inside JPEG `dgi:` / `JUMBF` chunks)
    b"jumbf",          # JPEG Universal Metadata Box Format container
    b"content credentials",  # Microsoft/Adobe "Content Credentials"
    b"content authenticity", # Content Authenticity Initiative
    b"stable diffusion",      # model family signature
    b"midjourney",            # tool signature
    b"dall-e",                # OpenAI tool signature
    b"dalle",                 # (without dash, some tools embed this)
    b"firefly",               # Adobe Firefly
    b"generative ai",
    b"ai generated",
    b"generated with",        # "generated with Stable Diffusion" etc.
    b"generation parameters", # CompfyUI / A1111 style block
    b"positive prompt",       # A1111 / ComfyUI
    b"negative prompt",       # A1111 / ComfyUI
    b"steps:",                # sampling steps dumps
    b"cfg scale",             # A1111 sampler dumps
    b"sampler:",              # A1111 sampler dumps
    b"seed:",                 # A1111 sampler dumps
    b"denoising",             # inpainting params
    b"model hash",            # A1111
    b"adetailer",             # A1111 extension
    b"civitai",               # model-sharing service signature
    b"clip skip",
    b"provenance",            # general provenance (Content Credentials)
    b"c2pa.org",              # c2pa CTA namespace URL
)

# Tags that are *so* specific they only appear in AI pipelines (kept separate
# so a future version can make them optional / less aggressive).
AI_ONLY_SIGNATURES = (
    b"raw profile type c2pa",       # PNG iTXt key used for C2PA manifests
    b"raw profile type dgi",        # PNG iTXt key used for older C2PA dgi data
    b"cabx",                        # PNG C2PA chunk (OpenAI/ChatGPT/Adobe)
    b"caci",                        # PNG C2PA chunk (Claim Info)
    b"c2pa_manifest",               # HEIC box name / XMP fallback key
    b"comfyui",                     # ComfyUI workflow graph
    b"automatic1111",               # A1111 webui
    b"invokeai",                    # InvokeAI
    b"stable-diffusion",
    b"jumbf boxes",
)

# JPEG APP marker numbers that are *always* AI / C2PA containers when present
# in a JPEG stream. Marker numbers are 0-15; the JPEG spec allocates
# 0 (0xFFE0) JFIF, 1 (0xFFE1) EXIF/XMP, 2 (0xFFE2) ICC, 13 (0xFFED) IPTC,
# 14 (0xFFEE) Adobe. Everything else is "application use".
#   - APP11 (0xFFEB)  -> JUMBF / C2PA
#   - APP2 (0xFFE2)   -> carries `JUMBF` / `c2pa` as first bytes in C2PA files
JPEG_AI_APP_MARKERS = {11, 2}

# ---------------------------------------------------------------------------
# 2) XMP-side patterns. The raw XMP packet is first lowercased, so the
#    patterns here are lowercase. Each entry is either:
#        ("exact-string", "label")          -> substring match
#        (regex, "label")                   -> compiled regex match
#    where "label" is what gets reported in the per-file report.
# ---------------------------------------------------------------------------

# Property-name fragments (lowercase) that mark a node as AI/C2PA related.
# We match these inside `key` segments of XMP (after splitting out `dc:` etc).
XMP_KEY_AI_FRAGMENTS = (
    ("c2pa",              "C2PA manifest"),
    ("claim_generator",   "C2PA claim generator"),
    ("claim_signature",   "C2PA claim signature"),
    ("contentcredits",    "Content Credentials"),
    ("contentcredentials","Content Credentials"),
    ("credentials",       "Content Credentials"),
    ("manifest",          "C2PA manifest reference"),
    ("jumbf",             "JUMBF / C2PA box"),
    ("digitalsource",     "DigitalSourceType (AI/C2PA)"),
    ("digitalsourcetype", "DigitalSourceType (AI/C2PA)"),
    ("trainedalgorithmicmedia", "Trained Algorithmic Media flag"),
    ("trainedalgorithmic",       "Trained Algorithmic Media flag"),
    ("guid",              "C2PA guid"),
    ("activedigitalsource", "Active Digital Source"),
    ("ingredients",       "C2PA ingredients"),
    ("assertions",        "C2PA assertions"),
    ("softwareagent",     "SoftwareAgent (generator)"),
    ("prompt",            "generation prompt"),
    ("provenance",        "provenance record"),
    ("ai_tool",           "AI tool"),
    ("ai_generated",      "AI generated flag"),
    ("firefly",           "Adobe Firefly"),
    ("midjourney",        "Midjourney"),
    ("stablediffusion",   "Stable Diffusion"),
    ("stable_diffusion",  "Stable Diffusion"),
    ("dall-e",            "DALL-E"),
    ("dalle",             "DALL-E"),
    ("generative",        "generative metadata"),
    ("neural",            "neural / AI"),
    ("diffusion",         "diffusion model"),
    ("dreamstudio",       "DreamStudio"),
    ("comfy",             "ComfyUI"),
    ("invokeai",          "InvokeAI"),
    ("a1111",             "A1111 webui"),
    ("model_hash",        "model hash"),
    ("modelhash",         "model hash"),
    ("sampler",           "sampler parameter"),
    ("cfgscale",          "CFG scale parameter"),
    ("steps",             "steps parameter"),
    ("seed",              "seed value"),
)

# Whole-key exact matches that are AI-only. Shown lowercase.
XMP_KEY_AI_EXACT = {
    "c2pa":            "C2PA manifest",
    "jumbf":           "JUMBF container",
    "digital_source_type": "DigitalSourceType (AI/C2PA)",
    "trainedalgorithmicmedia": "Trained Algorithmic Media flag",
    "claimgenerator":  "C2PA claim generator",
}

# Value-side signatures (generator/tool names, model identifiers, service
# URLs). Lowercase substring test against the property value.
XMP_VALUE_AI_MARKERS = (
    "c2pa",                 # manifests embed this everywhere
    "jumbf",
    "content credentials",
    "contentauthenticity",
    "contentauthenticityinitiative",
    "stable diffusion",
    "stablediffusion",
    "midjourney",
    "dall-e",
    "dall e",
    "openai",
    "firefly",
    "adobe firefly",
    "generative ai",
    "ai generated",
    "generated by",
    "generated with",
    "gpt-image",
    "gpt image",
    "dreamstudio",
    "runwayml",
    "runway ml",
    "leonardo.ai",
    "synthesia",
    "deepai",
    "craiyon",
    "nightcafe",
    "playground ai",
    "wombo",
    "artbreeder",
    "hotpot.ai",
    "getimg.ai",
    "picfinder.ai",
    "clipdrop",
    "krea.ai",
    "lexica",
    "prompthero",
    "civitai",
    "huggingface",
    "tensor.art",
    "bing image creator",
    "microsoft designer",
    "imagen",
    "gemini",
    "procreate",
    "ai-tool",
    "aitool",
    "aipowered",
    "ai powered",
    "neural network",
    "diffusion model",
    "model hash",
    "cfg scale",
    "denoising strength",
    "clip skip",
    "positive prompt",
    "negative prompt",
    "sampler",
    "comfyui",
    "automatic1111",
    "invokeai",
    "stable-diffusion-webui",
    "novelai",
    "novel ai",
    "provenance",
    "c2pa.org",
    "caicite",
    "credential",
    "manifest",
)

# ---------------------------------------------------------------------------
# 3) IPTC side. IPTC field names that AI / camera pipelines populate with AI
#    provenance. Removed only in "all" mode (they are ordinary fields).
# ---------------------------------------------------------------------------
IPTC_AI_FIELD_NAMES = {
    "byline", "byline_title", "credit", "source", "copyright_notice",
    "keywords", "supplemental_categories", "caption",
}

# ---------------------------------------------------------------------------
# 4) Per-format container locations we know carry C2PA / JUMBF data.
#    Used by the targeted binary removers.
# ---------------------------------------------------------------------------

# HEIC/HEIF brands that must be probed for `mime` (C2PA) / `jumb` boxes.
HEIC_C2PA_BRANDS = (b"mime", b"jumb", b"c2pa", b"crl0", b"dgi0")
