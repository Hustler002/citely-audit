#!/usr/bin/env python3
"""Localhost fixture server for E2E tests (no external egress required).

Serves tests/fixtures/ so the orchestrator's real fetch+render path can be exercised against a
"healthy" and a "broken" page on 127.0.0.1. Also serves a permissive robots.txt.

ARCHITECTURE SKELETON — wiring in place; routes to be finalized alongside the E2E test.

Usage:
  python fixture_server.py --port 8099
Routes (planned):
  /            -> fixtures/healthy_page.html
  /broken      -> fixtures/broken_page.html
  /robots.txt  -> allow-all
"""
from __future__ import annotations

import argparse
import http.server
import logging
from functools import partial
from pathlib import Path

log = logging.getLogger("fixture-server")
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        # TODO: map routes above to fixture files; serve robots.txt allow-all; 404 otherwise.
        raise NotImplementedError

    def log_message(self, *args) -> None:  # keep test output quiet
        pass


def serve(port: int) -> None:
    server = http.server.HTTPServer(("127.0.0.1", port), partial(FixtureHandler))
    log.info("Serving fixtures on http://127.0.0.1:%d", port)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
