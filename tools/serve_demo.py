"""Serve demo/ locally with UTF-8 pages (python -m http.server sends no charset): uv run python tools/serve_demo.py [port]"""

import functools
import http.server
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent / "demo"


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                      ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(DEMO))).serve_forever()
