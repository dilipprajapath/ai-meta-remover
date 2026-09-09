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

Path handling
-------------
A Vercel rewrite replaces the path the function receives: with a plain
`/(.*) -> /api/index` rule, Flask sees "/api/index" for EVERY request, matches
no route, and returns 404 for the whole site. So vercel.json appends the real
path as `__vpath` and the middleware below restores it before Flask routes.
"""

import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode

# The function's working directory is not guaranteed to be the repo root, so
# put it on sys.path explicitly before importing the `app` package.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.server import create_app  # noqa: E402


class RestoreOriginalPath:
    """Move `__vpath` out of the query string and back into PATH_INFO.

    Any other query parameters are preserved untouched (/api/zip?ids=... still
    works). If `__vpath` is absent the request passes through unchanged, so
    this is a no-op when the app is run directly rather than behind a rewrite.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        pairs = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        vpath = None
        rest = []
        for key, value in pairs:
            if key == "__vpath" and vpath is None:
                vpath = value
            else:
                rest.append((key, value))

        if vpath is not None:
            if not vpath.startswith("/"):
                vpath = "/" + vpath
            environ["PATH_INFO"] = unquote(vpath)
            environ["QUERY_STRING"] = urlencode(rest)

        return self.wsgi_app(environ, start_response)


app = create_app()
app.wsgi_app = RestoreOriginalPath(app.wsgi_app)
