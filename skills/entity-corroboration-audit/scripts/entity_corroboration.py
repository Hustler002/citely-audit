#!/usr/bin/env python3
"""entity-corroboration-audit (mechanic 4) — ARCHITECTURE SKELETON.

Dual-mode consumer. On-page structured-data analysis needs no network. An OPTIONAL Wikidata
corroboration lookup is gated behind --allow-external and is soft-fail (never blocks/crashes).

Usage:
  python entity_corroboration.py --artifact crawl_artifact.json [--config scoring-config.json] [--allow-external]
  python entity_corroboration.py --html-file page.html          [--config scoring-config.json]

Output: JSON array of partial findings on stdout (no id). Logs to stderr.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("entity-corroboration-audit")

CATEGORY = "entity-corroboration"

DEFAULT_CONFIG = {
    "required_jsonld_types": ["Organization", "Person", "LocalBusiness", "WebSite"],
    "expect_sameas": True,
    "wikidata_lookup_enabled_by_default": False,
}


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg.update(data.get("entity_corroboration", {}))
    return cfg


def load_html(args) -> str:
    """Return the rendered HTML (artifact) or local file contents. TODO."""
    raise NotImplementedError


def extract_entity_graph(html: str) -> dict:
    """Parse JSON-LD / Open Graph / microdata into a normalized entity view. TODO."""
    raise NotImplementedError


def check_on_page(graph: dict, cfg: dict) -> list[dict]:
    """Require an Organization/Person entity; check sameAs / cross-web mappings. TODO."""
    raise NotImplementedError


def corroborate_wikidata(graph: dict, cfg: dict) -> list[dict]:
    """OPTIONAL, --allow-external only. Soft-fail Wikidata lookup; findings => confidence:heuristic.

    TODO: honor SSRF/timeout guards; on ANY failure log to stderr and return [] (never raise).
    Wikidata requires a descriptive User-Agent.
    """
    raise NotImplementedError


def run(html: str, cfg: dict, allow_external: bool) -> list[dict]:
    findings: list[dict] = []
    try:
        graph = extract_entity_graph(html)
        findings.extend(check_on_page(graph, cfg))
        if allow_external:
            try:
                findings.extend(corroborate_wikidata(graph, cfg))
            except Exception as exc:  # soft-fail by contract
                log.warning("Wikidata corroboration failed (soft-fail): %s", exc)
    except NotImplementedError:
        log.warning("entity extraction not implemented yet")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--artifact")
    src.add_argument("--html-file")
    parser.add_argument("--config")
    parser.add_argument("--allow-external", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    cfg = load_config(args.config)
    try:
        findings = run(load_html(args), cfg, args.allow_external)
    except NotImplementedError:
        findings = []
    json.dump(findings, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
