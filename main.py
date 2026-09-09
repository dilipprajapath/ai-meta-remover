#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — Entry point for "AI Metadata Remover".

UI modes (pick with --ui, default is 'auto'):

  --ui window   Native desktop window (Edge WebView2 via pywebview). The app
                looks and behaves like a normal Windows application: its own
                window, icon, taskbar entry, and native Save dialogs.
  --ui browser  Opens the local web UI in your default browser tab.
  --ui auto     (default) window when a native window can be created, else
                browser. The app can never fail to start.

Running:
  * from source:   python main.py [--ui window|browser] [--port N]
  * as .exe:       double-click AI-Metadata-Remover.exe  (tries native window)
  * headless test: python main.py --no-browser --port 8799
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _free_port() -> int:
    """Ask the OS for a free ephemeral port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _setup_logging() -> logging.Logger:
    log = logging.getLogger("aimr")
    log.setLevel(logging.INFO)
    try:
        logdir = Path(os.environ.get("TEMP") or "/tmp")
        handler = RotatingFileHandler(logdir / "ai-metadata-remover.log",
                                      maxBytes=1_000_000, backupCount=2)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    except Exception:
        pass
    return log


def _open_browser(url: str, log: logging.Logger) -> None:
    def _open():
        time.sleep(0.5)
        try:
            webbrowser.open(url)
        except Exception as exc:                   # pragma: no cover
            log.warning("could not open browser: %s", exc)
    threading.Thread(target=_open, daemon=True).start()


def _run_browser_mode(app, host: str, port: int, url: str,
                      log: logging.Logger) -> int:
    """Legacy flow: Flask on the main thread + a browser tab."""
    log.info("browser mode at %s", url)
    try:
        app.run(host=host, port=port, threaded=True,
                use_reloader=False, debug=False)
    except KeyboardInterrupt:                      # console Ctrl+C
        pass
    return 0


def _run_window_mode(app, host: str, port: int, url: str, icon: str,
                     log: logging.Logger) -> Optional[bool]:
    """Native window flow: Flask on a background thread, WebView2 window on
    the main thread. Returns None if the window could not be created (caller
    may fall back to browser), True if the window ran and closed."""
    from werkzeug.serving import make_server
    from app.desktop import native_available, launch

    if not native_available():
        log.info("native window unavailable -> fall back to browser")
        return None

    server = make_server(host, port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever,
                              daemon=True, name="aimr-http")
    thread.start()
    log.info("window mode at %s", url)

    def _stop() -> None:
        # runs when the window closes; stop the background HTTP server
        try:
            server.shutdown()
        except Exception:
            pass

    ok, reason = launch(url, icon=icon, on_closed=_stop)
    if not ok:
        log.warning("native window failed (%s) -> fall back to browser", reason)
        _stop()
        return None
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI Metadata Remover")
    ap.add_argument("--ui", choices=("auto", "window", "browser"),
                    default="auto",
                    help="window = native desktop window (WebView2); "
                         "browser = open in default browser; "
                         "auto = window when possible (default)")
    ap.add_argument("--no-browser", action="store_true",
                    help="start headless (no window/browser); for tests/CI")
    ap.add_argument("--port", type=int, default=None,
                    help="port for the local web UI (default: random free)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (keep 127.0.0.1 for privacy)")
    args = ap.parse_args(argv)

    log = _setup_logging()
    log.info("starting AI Metadata Remover (ui=%s)", args.ui)

    port = args.port or int(os.environ.get("AI_PORT", "0"))
    if port == 0:
        port = _free_port()

    from app.server import create_app, set_desktop_mode
    app = create_app()

    url = f"http://{args.host}:{port}/"
    banner = (
        "\n" + "=" * 62 + "\n"
        "  AI Metadata Remover\n"
        f"  Local address: {url}\n"
        "  Everything runs on this computer — nothing is uploaded anywhere.\n"
        "  Close the window (or click 'Stop' in the app) to quit.\n"
        + "=" * 62
    )
    print(banner, flush=True)

    # ---- headless (tests / CI) -------------------------------------------
    if args.no_browser:
        log.info("headless at %s", url)
        try:
            app.run(host=args.host, port=port, threaded=True,
                    use_reloader=False, debug=False)
        except KeyboardInterrupt:
            pass
        return 0

    # ---- decide the UI -----------------------------------------------------
    ui = args.ui or os.environ.get("AI_UI", "auto")
    if ui == "browser":
        return _run_browser_mode(app, args.host, port, url, log)

    icon = None
    if getattr(sys, "frozen", False):              # icon next to the exe/data
        base = Path(getattr(sys, "_MEIPASS",
                            Path(sys.executable).parent))
        cand = Path(sys.executable).parent / "icon.ico"
        if not cand.exists():
            cand = base / "icon.ico"
        if cand.exists():
            icon = str(cand)
    else:
        cand = Path(__file__).resolve().parent / "assets" / "icon.ico"
        if cand.exists():
            icon = str(cand)

    if ui == "window":
        set_desktop_mode(True)
        ran = _run_window_mode(app, args.host, port, url, icon, log)
        if ran is True:
            return 0
        # window failed and user explicitly wanted a window: fall back to
        # browser rather than refusing to start.
        print("[i] Native window unavailable — opening in your browser.")
        set_desktop_mode(False)
        return _run_browser_mode(app, args.host, port, url, log)

    # auto: try the native window, otherwise browser
    from app.desktop import native_available
    if native_available():
        set_desktop_mode(True)
        ran = _run_window_mode(app, args.host, port, url, icon, log)
        if ran is True:
            return 0
        set_desktop_mode(False)
    _open_browser(url, log)
    return _run_browser_mode(app, args.host, port, url, log)


if __name__ == "__main__":
    sys.exit(main())
