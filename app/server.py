# -*- coding: utf-8 -*-
"""
server.py — The built-in web application (runs entirely on 127.0.0.1).

Design goals honoured here:
  * 100 % offline: no external CDN, no telemetry, no API calls anywhere.
  * Files are processed IN MEMORY and served back from memory. Uploaded
    originals are never written to disk; cleaned files are only saved when the
    user clicks "Save" / downloads them. This is the strongest privacy story
    we can offer and also keeps the app fully self-contained.
  * The server binds to 127.0.0.1 only, so nothing on the LAN can reach it.
  * Every request returns JSON; the UI (templates/index.html + static assets)
    is a normal web frontend talking to these endpoints.
"""

from __future__ import annotations

import base64
import io
import os
import re
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from flask import (Flask, jsonify, request, render_template, send_file,
                   Response, abort)

from app import __version__
from app.scrubber.scrubber import (clean_image, SUPPORTED_EXTS,
                                   FORMAT_LABEL)
from app.scrubber import MODE_LABELS, OPTION_DEFAULTS

# ---------------------------------------------------------------------------
# Resource path handling (PyInstaller one-file: assets live in sys._MEIPASS)
# ---------------------------------------------------------------------------

def _resource_root() -> Path:
    """Where templates/static live at runtime (frozen or source)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


RES = _resource_root()

# ---------------------------------------------------------------------------
# In-memory store of processed results (module level so the desktop host and
# the HTTP layer share the same data). Every entry:
#   {payload: bytes, type: str, name: str, ts: float, report_text: str}
# Uploaded originals are never written to disk — payloads live in RAM only
# and are dropped after APP_TTL_SECONDS.
# ---------------------------------------------------------------------------
STORE: Dict[str, dict] = {}
STORE_LOCK = threading.Lock()
APP_STARTED = time.time()
APP_TTL_SECONDS = float(os.environ.get("AI_STORE_TTL", str(30 * 60)))

# ---------------------------------------------------------------------------
# Serverless (Vercel) mode.
#
# On a serverless platform every request may be handled by a DIFFERENT, cold
# process, so the module-level STORE above cannot be relied on: a later
# GET /api/file/<rid> would land on an instance that never saw the upload and
# would 404. In this mode /api/process therefore returns each cleaned file
# INLINE (base64) in its JSON response and the frontend keeps the bytes in the
# browser — no server-side state, no follow-up round trip.
#
# Vercel always sets VERCEL=1 in the function environment; AI_SERVERLESS=1 is
# the manual override used for local testing of this exact code path.
# ---------------------------------------------------------------------------
SERVERLESS = bool(os.environ.get("VERCEL") or
                  os.environ.get("AI_SERVERLESS"))

# Set to True by the native desktop host (app/desktop.py) — lets the frontend
# know it can use the window.pywebview bridge instead of browser downloads.
IN_DESKTOP = False


def set_desktop_mode(on: bool = True) -> None:
    global IN_DESKTOP
    IN_DESKTOP = bool(on)


def _desktop_bridge_available() -> bool:
    return IN_DESKTOP


def store_put(payload: bytes, content_type: str, name: str, rid: str,
              report_text: str = "") -> None:
    """Add (or refresh) one processed result. Prunes stale entries."""
    with STORE_LOCK:
        STORE[rid] = {"payload": payload, "type": content_type,
                      "name": name, "ts": time.time(),
                      "report_text": report_text}
        cutoff = time.time() - APP_TTL_SECONDS
        for key in [k for k, v in STORE.items() if v["ts"] < cutoff]:
            del STORE[key]


def store_get(rid: str) -> Optional[dict]:
    with STORE_LOCK:
        return STORE.get(rid)


def store_snapshot() -> Dict[str, dict]:
    """Copy of every live result (used by the desktop native save dialog)."""
    with STORE_LOCK:
        return dict(STORE)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(RES / "templates"),
        static_folder=str(RES / "static"),
    )

    # ---- limits & settings -------------------------------------------------
    MAX_MB = int(os.environ.get("AI_MAX_MB", "300"))
    app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({
            "ok": True, "version": __version__,
            "uptime_s": int(time.time() - APP_STARTED),
            "processed_in_memory": True,
            "offline": not SERVERLESS,
            "serverless": SERVERLESS,
            "host": "127.0.0.1" if not SERVERLESS else "serverless",
            "desktop_bridge": _desktop_bridge_available(),
        })

    @app.get("/api/formats")
    def formats():
        return jsonify({
            "formats": sorted(FORMAT_LABEL),
            "labels": FORMAT_LABEL,
            "modes": MODE_LABELS,
            "options": OPTION_DEFAULTS,
            "exts": sorted(SUPPORTED_EXTS),
        })

    # -------------------------------------------------------------- processing
    @app.post("/api/process")
    def process():
        """Accepts multipart/form-data:
             files: <file> xN
             mode:  'ai' | 'all'
             options.location / .copyright / .icc : 'true'/'1'
        Returns one result object per uploaded file."""
        mode = request.form.get("mode", "ai")
        if mode not in MODE_LABELS:
            mode = "ai"

        options = {
            k: bool(request.form.get(f"options.{k}", "").lower()
                    in ("1", "true", "on", "yes"))
            for k in OPTION_DEFAULTS
        }
        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "No files uploaded."}), 400

        results = []
        for f in files:
            original_name = _safe_name(f.filename)
            raw = f.read()
            rid = uuid.uuid4().hex[:16]
            try:
                fr = clean_image(raw, filename=original_name, mode=mode,
                                 options=options)
            except Exception as exc:                     # never 500 the batch
                results.append({
                    "id": rid, "original_name": original_name,
                    "status": "error", "message": str(exc),
                })
                continue

            payload = fr.output_data or raw
            content_type = _guess_mime(original_name)
            report_text = _build_report(fr)
            out_name = fr.output_name or original_name

            d = fr.to_dict()
            d["id"] = rid
            if SERVERLESS:
                # Stateless: hand the cleaned bytes straight back to the
                # browser. Nothing is retained between requests.
                d["data"] = base64.b64encode(payload).decode("ascii")
                d["mime"] = content_type
                d["save_name"] = out_name
                d["report_text"] = report_text
            else:
                store_put(payload, content_type, out_name, rid,
                          report_text=report_text)
                d["download_url"] = f"/api/file/{rid}"
                d["report_url"] = f"/api/report/{rid}"
            results.append(d)

        return jsonify({"results": results, "mode": mode,
                        "options": options})

    # --------------------------------------------------------------- download
    @app.get("/api/file/<rid>")
    def download(rid):
        item = store_get(rid)
        if not item:
            abort(404)
        bio = io.BytesIO(item["payload"])
        return send_file(bio, mimetype=item["type"], as_attachment=True,
                         download_name=item["name"])

    @app.get("/api/report/<rid>")
    def report(rid):
        item = store_get(rid)
        if not item:
            abort(404)
        # keep a report payload attached at store time if present
        text = item.get("report_text") or ("No report stored for this item.")
        return Response(text, mimetype="text/plain; charset=utf-8")

    @app.get("/api/zip")
    def download_zip():
        """?ids=a,b,c  ->  one ZIP of every cleaned file + its report."""
        ids = request.args.get("ids", "").split(",")
        ids = [i for i in ids if store_get(i)]
        if not ids:
            abort(404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in ids:
                item = store_get(i)
                if not item:
                    continue
                z.writestr(item["name"], item["payload"])
                if item.get("report_text"):
                    z.writestr(Path(item["name"]).stem + "-report.txt",
                               item["report_text"])
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name="cleaned-images.zip")

    @app.get("/api/quit")
    def quit_server():
        """Graceful shutdown used by the UI's 'Stop' button.

        Meaningless (and harmful) on a shared serverless host, where the
        process is not "yours" to stop — so it is disabled there.
        """
        if SERVERLESS:
            abort(404)

        def _bye():
            time.sleep(0.2)
            func = request.environ.get("werkzeug.server.shutdown")
            if func:
                func()
        threading.Thread(target=_bye, daemon=True).start()
        return jsonify({"bye": True})

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": f"File too large (limit {MAX_MB} MB)."}), 413

    @app.errorhandler(404)
    def nf(_e):
        return jsonify({"error": "Not found."}), 404

    return app


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

_EXT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """Strip any path components so a hostile filename can't write outside
    the output folder later; keep Unicode letters intact for real names."""
    name = Path(name or "image").name
    name = name.replace("\\", "/")
    base = Path(name).name
    if not base or base in (".", ".."):
        return "image.bin"
    return base[:180]


def _build_report(fr) -> str:
    """Plain-text per-file report (same contract as the CLI report)."""
    lines = [
        "AI Metadata Remover — per-file report",
        "=" * 46,
        f"File   : {fr.original_name}",
        f"Type   : {fr.extension}",
        f"Mode   : {fr.mode}",
        f"Status : {fr.status}",
        f"Bytes  : {fr.original_bytes} -> {fr.output_bytes}",
        "",
        "Removed / changed:",
    ]
    if fr.removed:
        lines += [f"  - {r}" for r in fr.removed]
    else:
        lines.append("  (nothing to remove)")
    for w in fr.warnings:
        lines.append(f"  ! {w}")
    v = fr.verified_after or {}
    if v:
        lines += ["", "Verified after cleaning:",
                  f"  EXIF    : {'present' if v.get('has_exif') else 'absent'}",
                  f"  GPS     : {'present' if v.get('has_gps') else 'absent'}",
                  f"  XMP     : {'present' if v.get('has_xmp') else 'absent'}",
                  f"  IPTC    : {'present' if v.get('has_iptc') else 'absent'}",
                  f"  AI/C2PA : "
                  f"{'STILL PRESENT' if v.get('ai_metadata_found') else 'none detected'}"]
    return "\n".join(lines)


_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "jpe": "image/jpeg",
    "png": "image/png", "gif": "image/gif", "webp": "image/webp",
    "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff",
    "svg": "image/svg+xml", "heic": "image/heic", "heif": "image/heif",
    "avif": "image/avif",
}


def _guess_mime(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return _MIME.get(ext, "application/octet-stream")
