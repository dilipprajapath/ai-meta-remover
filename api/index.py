# -*- coding: utf-8 -*-
"""
api/index.py — Vercel serverless entry point.

Vercel's Python runtime imports this module and looks for a WSGI callable
named `app`. Every route is funnelled here by vercel.json, so this one
function serves the UI, the static assets and the whole /api/* surface.

The engine itself is completely unchanged: `create_app()` is the same factory
the desktop build uses. The only difference is that VERCEL=1 is present in the
environment, which flips app/server.py into SERVERLESS mode — cleaned files
come back inline in the /api/process response instead of being parked in a
module-level dict that a later, cold invocation would not be able to see.
"""

import sys
from pathlib import Path

# The function's working directory is not guaranteed to be the repo root, so
# put it on sys.path explicitly before importing the `app` package.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.server import create_app  # noqa: E402

app = create_app()
