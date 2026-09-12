# -*- coding: utf-8 -*-
"""
desktop.py — Native Windows desktop window host (Microsoft Edge WebView2).

Purpose
-------
"Must be like an application inside Windows." Instead of opening the web UI in
a system browser tab, this module opens the *same* local web app inside a real
native window (title bar, app icon, resize, taskbar entry) powered by
**Edge WebView2** through the tiny ``pywebview`` wrapper. WebView2 ships with
Windows 11 and with Edge on Windows 10, so there is nothing to install.

Because WebView2 has no visible "download" bar, saving is handled through a
Python<->JS bridge: the page calls ``window.pywebview.api.save_one(id)`` /
``save_all()`` and this side opens **native Windows Save/Folder dialogs**
and writes the cleaned bytes to disk directly from the in-memory store.

Resilience
----------
Importing/starting a native window can fail (WebView2 runtime missing,
pywebview/pythonnet unavailable on the build Python). Every function here
fails *softly*: callers (main.py) catch the result and fall back to the
normal browser mode, so the app always runs.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

log = logging.getLogger("aimr.desktop")


def native_available() -> bool:
    """True when pywebview can be imported (i.e. a window may be shown)."""
    try:
        import webview  # noqa: F401
        return True
    except Exception as exc:                     # pragma: no cover
        log.info("pywebview not available: %s", exc)
        return False


def _unique_name(folder: Path, name: str) -> Path:
    """photo-clean.jpg -> photo-clean.jpg / photo-clean (1).jpg / ..."""
    candidate = folder / name
    stem = candidate.stem
    suffix = candidate.suffix
    i = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({i}){suffix}"
        i += 1
    return candidate


class DesktopApi:
    """Exposed to the page as ``window.pywebview.api``.

    All return values are JSON-serialisable dicts the frontend turns into
    status messages.
    """

    def __init__(self, store_get, store_snapshot):
        self._store_get = store_get
        self._store_snapshot = store_snapshot
        self._window = None

    # pywebview attaches the Window object after creation.
    def attach(self, window) -> None:
        self._window = window

    def platform(self) -> str:
        return "desktop"

    # ------------------------------------------------------------------ save
    def save_one(self, rid: str) -> dict:
        """Native 'Save As' dialog for a single processed file."""
        try:
            import webview
        except Exception as exc:
            return {"ok": False, "error": f"Native dialogs unavailable: {exc}"}
        item = self._store_get(rid)
        if not item:
            return {"ok": False, "error": "That result is no longer in "
                                          "memory (session too old)."}
        if self._window is None:
            return {"ok": False, "error": "Desktop window not attached."}
        # Offer the file's OWN type first. With a bare "(*.*)" filter Windows
        # has no extension to fall back on, so a user who edits the name in
        # the dialog ends up with an extension-less file; naming the real type
        # makes the dialog re-apply it automatically.
        ext = Path(item["name"]).suffix
        if ext:
            label = ext.lstrip(".").upper()
            file_types = (f"{label} image (*{ext})", "All files (*.*)")
        else:
            file_types = ("All files (*.*)",)

        try:
            chosen = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=item["name"],
                file_types=file_types,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not chosen:
            return {"ok": False, "cancelled": True}
        path = Path(chosen if isinstance(chosen, str) else chosen[0])
        # Belt and braces: if the dialog still handed back a name with no
        # extension, keep the uploaded file's one rather than writing a file
        # Windows cannot open. A deliberate different extension is respected.
        if ext and not path.suffix:
            path = path.with_suffix(ext)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item["payload"])
        except Exception as exc:
            return {"ok": False, "error": f"Could not write file: {exc}"}
        return {"ok": True, "path": str(path), "name": path.name}

    def save_report(self, rid: str) -> dict:
        """Native 'Save As' dialog for a single per-file report (.txt)."""
        try:
            import webview
        except Exception as exc:
            return {"ok": False, "error": f"Native dialogs unavailable: {exc}"}
        item = self._store_get(rid)
        if not item:
            return {"ok": False, "error": "That result is no longer in memory."}
        if self._window is None:
            return {"ok": False, "error": "Desktop window not attached."}
        report = item.get("report_text") or "No report stored for this item."
        default = Path(item["name"]).stem + "-report.txt"
        try:
            chosen = self._window.create_file_dialog(
                webview.SAVE_DIALOG, save_filename=default,
                file_types=("Text files (*.txt)",),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not chosen:
            return {"ok": False, "cancelled": True}
        path = Path(chosen if isinstance(chosen, str) else chosen[0])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report, encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": f"Could not write report: {exc}"}
        return {"ok": True, "path": str(path), "name": path.name}

    def save_all(self) -> dict:
        """Native folder picker; writes every cleaned file (+ report)."""
        try:
            import webview
        except Exception as exc:
            return {"ok": False, "error": f"Native dialogs unavailable: {exc}"}
        items = self._store_snapshot()
        if not items:
            return {"ok": False, "error": "Nothing to save yet — process "
                                          "some images first."}
        if self._window is None:
            return {"ok": False, "error": "Desktop window not attached."}
        try:
            folder = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not folder:
            return {"ok": False, "cancelled": True}
        folder = Path(folder if isinstance(folder, str) else folder[0])
        saved: list = []
        errors: list = []
        try:
            for rid, item in items.items():
                name = Path(item["name"]).name or "image.bin"
                dest = _unique_name(folder, name)
                try:
                    dest.write_bytes(item["payload"])
                    saved.append(dest.name)
                    if item.get("report_text"):
                        rp = dest.with_name(dest.stem + "-report.txt")
                        rp.write_text(item["report_text"], encoding="utf-8")
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "folder": str(folder), "saved": saved,
                "errors": errors}

    def stop(self) -> None:
        """Close the native window (equivalent of the UI's Stop button)."""
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass


def launch(url: str, title: str = "AI Metadata Remover",
           icon: Optional[str] = None,
           on_closed=None) -> Tuple[bool, str]:
    """Open a native WebView2 window pointed at ``url``. Blocks until the
    window is closed.

    Returns (True, "") on success or (False, reason) when a native window
    cannot be created (caller falls back to the system browser).
    """
    try:
        import webview
    except Exception as exc:                     # pragma: no cover
        return False, f"pywebview not installed: {exc}"

    from app.server import store_get, store_snapshot
    api = DesktopApi(store_get, store_snapshot)

    kwargs = dict(
        url=url, title=title,
        width=1180, height=820, min_size=(900, 620),
        js_api=api, easy_drag=False,
        # Painted by the native window before the page renders — keep it in
        # step with --bg in static/style.css or the window flashes the old
        # colour on every launch.
        background_color="#0b0e14",
    )
    try:
        window = webview.create_window(**kwargs)
        api.attach(window)
        if icon and Path(icon).is_file():
            try:                              # optional, backend-dependent
                window.icon = str(Path(icon).resolve())
            except Exception:
                pass
    except Exception as exc:                     # pragma: no cover
        return False, f"Could not create native window: {exc}"

    try:
        # Blocks until every window is closed (the app's main loop). pywebview
        # runs func *when the loop starts*, not when windows close, so the
        # on_closed callback is invoked below, right after the loop ends
        # (i.e. the user closed the window).
        webview.start(gui=None)
    except Exception as exc:                     # pragma: no cover
        return False, f"Native window failed at runtime: {exc}"
    if on_closed is not None:
        try:
            on_closed()
        except Exception:
            pass
    return True, ""
