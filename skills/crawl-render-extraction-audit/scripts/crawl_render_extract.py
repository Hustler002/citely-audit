#!/usr/bin/env python3
"""crawl-render-extraction-audit (mechanic 1) — ARCHITECTURE SKELETON.

Dual-mode consumer. Reads the shared crawl artifact (or a local HTML file), never touches the network.

Usage:
  python crawl_render_extract.py --artifact crawl_artifact.json [--config scoring-config.json]
  python crawl_render_extract.py --html-file page.html          [--config scoring-config.json]

Output: JSON array of partial findings on stdout (no id). Logs to stderr.
Partial finding shape: {title, severity, category, confidence, evidence, suggested_action:{summary, priority, snippet?}}
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("crawl-render-extraction-audit")

CATEGORY = "crawl-ingestion"

# Safe defaults so the skill runs standalone; overridden by --config.
DEFAULT_CONFIG = {
    "spa_shell_max_visible_chars": 500,
    "spa_script_to_text_ratio": 3.0,
    "spa_mount_node_selectors": ["#root", "#app", "#__next", "[data-reactroot]"],
    "spa_framework_markers": ["__NEXT_DATA__", "window.__NUXT__", "ng-version", "data-server-rendered"],
    "render_diff_significant_text_delta_chars": 400,
    "fact_extraction_min_text_to_node_ratio": 1.0,
    "fact_extraction_flag_missing_alt": True,
}


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg.update(data.get("crawl_render_extraction", {}))
    return cfg


def load_inputs(args) -> dict:
    """Return {raw_html, rendered_html_or_none, robots, http_status, render_mode} from artifact/file. TODO."""
    raise NotImplementedError


def check_access(inp: dict, cfg: dict) -> list[dict]:
    """HTTP status, robots directive, bot-hostile headers. TODO."""
    raise NotImplementedError


def check_render_readability(inp: dict, cfg: dict) -> list[dict]:
    """Tier A raw-vs-rendered diff, or Tier B browserless SPA heuristic. TODO."""
    raise NotImplementedError


def check_fact_extraction(inp: dict, cfg: dict) -> list[dict]:
    """Facts trapped in images/SVG/canvas without semantic HTML. TODO."""
    raise NotImplementedError


def run(inp: dict, cfg: dict) -> list[dict]:
    findings: list[dict] = []
    for check in (check_access, check_render_readability, check_fact_extraction):
        try:
            findings.extend(check(inp, cfg))
        except NotImplementedError:
            log.warning("%s not implemented yet", check.__name__)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--artifact")
    src.add_argument("--html-file")
    parser.add_argument("--config")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    cfg = load_config(args.config)
    try:
        inp = load_inputs(args)
        findings = run(inp, cfg)
    except NotImplementedError:
        findings = []  # skeleton: emit an empty, contract-valid result
    json.dump(findings, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
