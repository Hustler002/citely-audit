#!/usr/bin/env python3
"""Localhost fixture server — lets the full pipeline be exercised with zero external egress.

Serves the archetype fixtures plus the adversarial routes the security and generalization suites
need: robots variants, sitemaps (including an index and a gzipped one), redirect chains and loops,
a compression bomb, and interstitial pages.

Usage:
    python fixture_server.py --port 8099
    with fixture_server.running() as base_url: ...     # context manager, used by tests

Note: the SSRF guard correctly refuses 127.0.0.1, so tests reaching this server must enable the
documented `fetch.allow_private_hosts` escape hatch in their config copy. That flag is test-only
and must never be set from CLI input.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import http.server
import logging
import threading
import zlib
from pathlib import Path

log = logging.getLogger("fixture-server")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

ROBOTS_ALLOW_ALL = b"User-agent: *\nDisallow:\n"
ROBOTS_BLOCK_AI = (
    b"User-agent: *\nDisallow:\n\n"
    b"User-agent: GPTBot\nDisallow: /\n\n"
    b"User-agent: ClaudeBot\nDisallow: /\n"
)
ROBOTS_BLOCK_US = b"User-agent: CitelyAuditBot\nDisallow: /\n"
ROBOTS_WITH_SITEMAP = (
    b"User-agent: *\nDisallow:\nCrawl-delay: 0\n"
    b"Sitemap: http://127.0.0.1:{port}/sitemap.xml\n"
)

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://127.0.0.1:{port}/</loc></url>
  <url><loc>http://127.0.0.1:{port}/deep/nested/page</loc></url>
  <url><loc>http://127.0.0.1:{port}/pricing</loc></url>
  <url><loc>http://127.0.0.1:{port}/asset.pdf</loc></url>
</urlset>
"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>http://127.0.0.1:{port}/sitemap.xml</loc></sitemap>
</sitemapindex>
"""

CONSENT_WALL = """<!DOCTYPE html><html lang="en"><head><title>Cookies</title></head>
<body><div id="banner"><h2>We use cookies</h2>
<p>Please accept all cookies to continue. Manage preferences.</p>
<button>Accept all cookies</button></div></body></html>"""

BOT_CHALLENGE = """<!DOCTYPE html><html lang="en"><head><title>Just a moment...</title></head>
<body><h1>Checking your browser</h1><p>Enable JavaScript and cookies to continue</p></body></html>"""

NAV_PAGE = """<!DOCTYPE html><html lang="en"><head><title>Nav fixture</title></head><body>
<header><nav>
  <a href="/">Home</a>
  <a href="/pricing">Pricing</a>
  <a href="/company">Company</a>
  <a href="/deep/nested/page">Deep</a>
  <a href="/brochure.pdf">Brochure</a>
  <a href="https://external.example.com/x">External</a>
  <a href="#section">Anchor</a>
</nav></header>
<main><h1>Nav fixture</h1><p>Body text for the nav selection fixture.</p></main>
</body></html>"""

SIMPLE_PAGE = """<!DOCTYPE html><html lang="en"><head><title>{title}</title></head>
<body><main><h1>{title}</h1><p>Placeholder content for {title}.</p></main></body></html>"""


def _bomb(size_mb: int = 60) -> bytes:
    """Highly compressible payload: tiny on the wire, large once decoded."""
    return zlib.compress(b"A" * (size_mb * 1024 * 1024), 9)


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # --- helpers -------------------------------------------------------------------------------
    def _send(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8",
              extra_headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _fixture(self, name: str):
        path = FIXTURES / name
        if not path.exists():
            self._send(b"fixture not found", 404, "text/plain")
            return
        self._send(path.read_bytes())

    @property
    def _port(self) -> int:
        return self.server.server_address[1]

    # --- routing -------------------------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        port = self._port

        routes = {
            "/": lambda: self._send(NAV_PAGE.encode()),
            "/healthy": lambda: self._fixture("healthy_page.html"),
            "/broken": lambda: self._fixture("broken_page.html"),
            "/spa": lambda: self._fixture("broken_page.html"),
            "/spa-hydrating": lambda: self._fixture("spa_hydrating.html"),
            "/pricing": lambda: self._send(SIMPLE_PAGE.format(title="Pricing").encode()),
            "/company": lambda: self._send(SIMPLE_PAGE.format(title="Company").encode()),
            "/deep/nested/page": lambda: self._send(SIMPLE_PAGE.format(title="Deep").encode()),
            "/consent-wall": lambda: self._send(CONSENT_WALL.encode()),
            "/bot-challenge": lambda: self._send(BOT_CHALLENGE.encode()),

            "/robots.txt": lambda: self._send(ROBOTS_ALLOW_ALL, content_type="text/plain"),
            "/robots-block-ai": lambda: self._send(ROBOTS_BLOCK_AI, content_type="text/plain"),
            "/robots-block-us": lambda: self._send(ROBOTS_BLOCK_US, content_type="text/plain"),
            "/robots-sitemap": lambda: self._send(
                ROBOTS_WITH_SITEMAP.decode().format(port=port).encode(), content_type="text/plain"),

            "/sitemap.xml": lambda: self._send(
                SITEMAP.format(port=port).encode(), content_type="application/xml"),
            "/sitemap-index.xml": lambda: self._send(
                SITEMAP_INDEX.format(port=port).encode(), content_type="application/xml"),
            "/sitemap.xml.gz": lambda: self._send(
                gzip.compress(SITEMAP.format(port=port).encode()), content_type="application/gzip"),
            "/sitemap-malformed.xml": lambda: self._send(
                b"<urlset><loc>unclosed", content_type="application/xml"),

            "/bomb": lambda: self._send(
                _bomb(), content_type="text/html",
                extra_headers={"Content-Encoding": "deflate", "Content-Length-Hint": "small"}),

            "/redirect-loop": lambda: self._send(
                b"", 302, extra_headers={"Location": f"http://127.0.0.1:{port}/redirect-loop"}),
            "/redirect-chain": lambda: self._send(
                b"", 302, extra_headers={"Location": f"http://127.0.0.1:{port}/redirect-chain-2"}),
            "/redirect-chain-2": lambda: self._send(
                b"", 302, extra_headers={"Location": f"http://127.0.0.1:{port}/healthy"}),
            "/redirect-no-location": lambda: self._send(b"", 302),
            "/redirect-internal": lambda: self._send(
                b"", 302, extra_headers={"Location": "http://169.254.169.254/latest/meta-data/"}),

            "/status-404": lambda: self._send(b"missing", 404, "text/plain"),
            "/status-500": lambda: self._send(b"boom", 500, "text/plain"),
            "/noindex": lambda: self._send(
                b"<html><head><meta name='robots' content='noindex'></head><body>x</body></html>"),
            "/x-robots": lambda: self._send(
                b"<html><body>x</body></html>", extra_headers={"X-Robots-Tag": "noindex"}),
            "/not-html": lambda: self._send(b'{"a":1}', content_type="application/json"),
        }

        handler = routes.get(path)
        if handler is None:
            self._send(b"not found", 404, "text/plain")
            return
        try:
            handler()
        except Exception as exc:  # a fixture bug must not hang the suite
            log.warning("fixture route %s failed: %s", path, exc)
            with contextlib.suppress(Exception):
                self._send(b"fixture error", 500, "text/plain")

    def log_message(self, *args):  # keep pytest output readable
        pass


@contextlib.contextmanager
def running(port: int = 0):
    """Start the server on a background thread; yields the base URL.

    Port 0 lets the OS pick a free port, so parallel or repeated test runs never collide.
    """
    # Threading is REQUIRED: with HTTP/1.1 keep-alive a single-threaded server blocks on the
    # held-open connection, so following a redirect (a second connection) deadlocks.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    with running(args.port) as base:
        log.info("Serving fixtures at %s (Ctrl-C to stop)", base)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
