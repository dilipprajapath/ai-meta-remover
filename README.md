# 🧹 AI Metadata Remover

**Strip AI / C2PA signatures from images — privately, offline, with zero quality loss.**

A Windows desktop application (`.exe`) that behaves like a **native Windows
application**: it opens its own desktop window (Microsoft Edge WebView2) with
the built-in web UI, native Save dialogs, an app icon, a Start Menu entry and
an uninstaller — or, if a native window can't run on a machine, it falls back
to your default browser tab. Everything runs **on your machine — no internet,
no servers, no telemetry.**

---

## ✨ What it does

| Upload | Removed (mode: AI only) | Removed (mode: All) | Output |
| --- | --- | --- | --- |
| JPEG, PNG, WebP, GIF, BMP, TIFF, RAW (CR2/NEF/ARW/DNG/ORF/RW2…), HEIC/HEIF/AVIF, SVG | C2PA / Content Credentials manifests, JUMBF blocks, generation parameters (A1111/ComfyUI style), AI-tool signatures (Stable Diffusion, Midjourney, DALL·E, Adobe Firefly, …), AI XMP/IPTC properties | **Everything**: EXIF, GPS, date/time, camera model, aperture, thumbnails, XMP, IPTC, comments, author/copyright, timestamps | Same format, **pixel-identical image data** |

Two removal modes (as in the UI):

- **🎯 Remove Only AI Metadata (recommended)** — removes AI-provenance
  metadata while **preserving your camera EXIF, GPS, copyright and other
  important metadata**.
- **🗑️ Remove All Metadata** — removes ALL metadata (EXIF, GPS, timestamps,
  camera settings, copyright). Results in a completely clean file.

Extra per-run options (checkboxes): remove **GPS location**, remove
**author/copyright fields**, remove the **ICC colour profile**.

> The key promise: **no re-compression, no re-encoding of pixels.** For the
> main formats (JPEG/PNG/WebP/GIF/BMP) the *encoded pixel stream is copied
> verbatim*; the tool only edits the container around it. Tests verify output
> is pixel-identical. Your image quality is never touched.

---

## 🖥️ Using the app

### Option A — run from source (developers)
```bash
pip install -r requirements.txt
python main.py                 # auto: native window if possible, else browser
python main.py --ui window     # force the native desktop window
python main.py --ui browser    # open in the default browser tab
python main.py --no-browser    # headless (for tests/CI)
```

### Option B — the installed application (recommended)
Install the Windows **installer** (`AI-Metadata-Remover-Setup-*.exe`, built as
described below). It installs a real application:

- Start Menu folder **AI Metadata Remover** (+ optional desktop icon),
- Add/Remove Programs entry and a full **uninstaller**,
- launching it opens a **native desktop window** (Edge WebView2) — an
  application window with its own icon and title bar. Upload images → choose
  mode/options → **Remove metadata** → **💾 Save cleaned image…** / **Save all
  to folder…** opens normal Windows save dialogs.

The same `.exe` can also be run standalone (double-click): it opens the native
window when WebView2 is available and otherwise falls back to the browser, so
it always starts. No runtime dependencies, 100% offline.

> ℹ️ The built-in WebView2 window needs the **Microsoft Edge WebView2
> Runtime** — preinstalled on Windows 11 and on Windows 10 with Edge. If it is
> missing, the app automatically opens in your default browser instead.

---

## 🔨 Building the Windows .exe (one click)

**Easiest way to get both the app and the installer: the GitHub Actions
build** (see "Building in CI" below) — download the **`windows-x64-py3.12`**
artifact: it contains `AI-Metadata-Remover.exe` **and**
`AI-Metadata-Remover-Setup-1.0.0.exe`, and the 3.12 build bundles the native
desktop window.

To build on your own PC (Python **3.9 – 3.13** recommended — 3.14 works for
everything except the native window; see note):

1. Open the project folder and **double-click** `scripts\build.bat`
   (or run it from cmd).
2. When it finishes you get:
   ```
   dist\AI-Metadata-Remover.exe
   ```
   — a single, self-contained, double-clickable Windows x64 executable
   (~50–70 MB, console-less windowed app with icon + version info).
3. To produce the **installer**, install
   [Inno Setup 6](https://jrsoftware.org/isdl.php) and double-click
   `scripts\build_installer.bat` → `dist\installer\AI-Metadata-Remover-Setup-1.0.0.exe`.

What `build.bat` does internally: creates a throwaway venv under `%TEMP%`,
installs the dependencies (incl. pywebview for the native window when
possible), then runs
`python -m PyInstaller AI-Metadata-Remover.spec` (one-file, windowed). Your
system Python stays untouched.

> **Python 3.14?** The build script detects your Python version and installs
> the matching dependency set automatically: Python 3.9–3.13 use the exact
> pinned builds (`Pillow 10.4.0`, `PyInstaller 6.10.0`, + `pywebview`);
> Python 3.14+ uses `Pillow 12.x` and `PyInstaller 6.15+`, because the older
> pins ship no Python-3.14 wheels. pywebview's native window needs `pythonnet`,
> which has **no stable Python-3.14 wheel yet** — on 3.14 `build.bat` tries to
> install it anyway and, if that fails, builds the browser-mode exe (the app
> opens in your browser) and prints a notice. For the **native desktop window**
> build locally, use Python 3.12 — or just use the CI artifact.

### Building in CI (no local Python needed)
Push the repo (or a `v*` tag) to GitHub and the included workflow
`.github/workflows/windows-build.yml` builds on `windows-latest` with
Python 3.12 and 3.14, runs the test suite, smoke-tests the frozen .exe, and
uploads one artifact per Python version:
- **`windows-x64-py3.12`** → exe **with native desktop window** + installer
  (recommended download),
- `windows-x64-py3.14` → exe (browser mode) + installer.

---

## 🧩 How the stripping works (engine overview)

All logic lives in `app/scrubber/`:

| Module | Responsibility |
| --- | --- |
| `ai_metadata.py` | **Single source of truth** for what counts as AI/C2PA: byte signatures (`c2pa`, `jumbf`, `content credentials`, `dgi0`…), XMP property/value markers (CreativeTool, claim_generator, prompts, samplers, seeds, model hashes…), AI-only XMP keys, JPEG APP markers (APP11/APP2-JUMBF). |
| `scan.py` | Non-destructive inspection of a file → `ScanResult` (EXIF/GPS/XMP/IPTC/ICC/PNG-text/comments present? AI found? per-field counts) used for the report + verification pass. |
| `jpeg_clean.py` | Hand-rolled **lossless JPEG segment walker** (spec-accurate marker parsing). Drops EXIF/XMP/ICC/IPTC/COM/APP11-JUMBF/APP2-C2PA segments and **rewrites XMP APP1 in place**, surgically deleting only AI properties while keeping `dc:rights`, `dc:creator`, … |
| `xmp_clean.py` | Namespace-agnostic XMP (RDF/XML) filter via ElementTree — matches property *local names* & values, so it defeats any prefix AI tools invent (`c2pa:`, `crln:`, `digim:`, …). |
| `png_clean.py` | Lossless PNG/APNG **chunk walker** — drops C2PA `iTXt` (`Raw profile type c2pa`), A1111 `parameters`/`prompt` tEXt, `eXIf`, `tIME`, `hIST`, ICC chunks; keeps colour/animation chunks byte-for-byte (CRC-validated). |
| `webp_clean.py` | Two-pass RIFF cleaner — removes `EXIF` / `XMP ` / `ICCP` chunks and **clears the matching VP8X flag bits**, preserving `VP8`/`VP8L` pixel chunks verbatim. |
| `gif_clean.py` | GIF block walker — drops AI comment extensions & XMP application extensions without touching LZW image data (animated GIFs stay intact). |
| `raw_clean.py` | TIFF/DNG/RAW: **(a)** for decodeable TIFFs, re-wraps the same pixels into a tag-free TIFF (same compression when possible); **(b)** in-place nulling of EXIF/GPS/XMP/IPTC **IFD pointers/tables** for CR2/NEF/ARW/ORF/RW2/PEF/SRW so no offsets break; **(c)** AI byte-signature window scrub as fallback. |
| `heif_clean.py` | HEIC/HEIF/AVIF/CR3 (ISO-BMFF): walks boxes and **zeroes the C2PA/JUMBF `mime`/`jumb`/`Exif` payloads** in place. Documented best-effort (full item re-muxing is out of scope for pure Python). |
| `scrubber.py` | Orchestrator: `clean_image()` routes by extension, applies mode + options, produces `FileResult` (removed items, warnings, pre/post verification) and the `pixels_equal()` lossless check used by tests. |

Every processed file runs a **post-clean scan** that is returned in the report
(“Verified after cleaning: EXIF gone · GPS gone · AI/C2PA none detected”), so
the UI shows you exactly what was removed, per file.

**Formats & honesty:** JPEG/PNG/WebP/GIF/BMP = fully lossless container edits.
TIFF/DNG = lossless pixel re-wrap. RAW (CR2/NEF/…) = pointer-null in place.
HEIC/AVIF/CR3 = payload zeroing (guarantee documented). SVG = comment/metadata
strip. Files with no removable metadata are reported as *already clean* and
left byte-identical.

---

## 🔬 Running the tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests -q          # 21 tests: JPEG/PNG/WebP/GIF/TIFF/XMP/scan
```

The suite fabricates images **with** realistic metadata (EXIF+GPS via piexif,
C2PA/JUMBF APP11, AI XMP, A1111 parameters, Firefly descriptions…) and
asserts: AI content gone, camera EXIF/GPS preserved in AI-only mode, pixels
identical after cleaning.

### Command-line (engine only)
```bash
python -m app.scrubber.cli photo.jpg --mode ai --location --report --out ./cleaned
```

---

## 🔐 Privacy & security notes
- The server binds to `127.0.0.1` **only** — nothing on your LAN can reach it.
- Uploads are processed **in memory**; cleaned files are only written when you
  choose to download/save them. Originals are never modified.
- No network calls, ever (no CDNs, no analytics, no model lookups).

## ⚠️ Known limitations
- **HEIC/AVIF/CR3 & vendor RAW**: removing C2PA there is done by in-place
  zeroing / pointer-null because a pure-Python re-muxer would be unsafe;
  files remain valid and decodable, but are best verified in your usual app.
- RAW files that are neither TIFF-like nor ISO-BMFF (e.g. some X3F/MRW
  layouts) only get AI byte-signature scrubbing — no EXIF/GPS guarantee.
- Extremely large files (> ~300 MB) are refused by default (configurable via
  the `AI_MAX_MB` environment variable).

## 📁 Project layout
```
main.py                     entry point (native window / browser / headless)
AI-Metadata-Remover.spec    PyInstaller spec (icon, version, pywebview data)
requirements.txt            pinned build deps
app/
  desktop.py                native WebView2 window + native Save dialogs
  server.py                 Flask web app (localhost, in-memory)
  templates/index.html      the UI
  static/                   style.css, app.js
  scrubber/                 the metadata engine (see table above)
tests/                      pytest suite + synthetic-image fixtures
assets/
  samples/                  try-me sample images (with GPS + AI metadata)
  icon.ico                  app icon (used by exe + installer + UI)
scripts/
  build.bat                 one-click single-file exe build
  build_installer.bat       one-click Inno Setup installer build
  AI-Metadata-Remover.iss   Inno Setup script
version_info.txt            Windows version resource for the exe
.github/workflows/          windows-latest CI build (exe + installer)
```
