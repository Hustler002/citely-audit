#!/usr/bin/env python3
"""Audit orchestrator entrypoint.

ARCHITECTURE SKELETON — pipeline stages and I/O contract in place; stage bodies are TODO.

Usage:
  python run_audit.py --url https://example.com [--config ../../../config/scoring-config.json]
  python run_audit.py --html-file page.html      # offline mode, zero network

Contract:
  - Emits the final report as JSON on STDOUT ONLY. All logs go to STDERR.
  - The report validates against references/report-schema.json.
  - Never crashes: failures are recorded in diagnostics.errors[] and a schema-valid report is still emitted.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("orchestrator")

HERE = Path(__file__).resolve().parent
REFERENCES = HERE.parent / "references"
SKILLS_DIR = HERE.parent.parent
REPORT_SCHEMA = REFERENCES / "report-schema.json"
DEFAULT_CONFIG = SKILLS_DIR.parent / "config" / "scoring-config.json"

# Consumer sub-skills run as subprocesses against the shared artifact. Order is part of the
# deterministic tie-break for finding IDs.
CONSUMER_SKILLS = [
    ("crawl-render-extraction-audit", "crawl_render_extract.py"),
    ("quotability-density-audit", "quotability_density.py"),
    ("entity-corroboration-audit", "entity_corroboration.py"),
    ("engagement-orientation-audit", "engagement_orientation.py"),
]

GLOBAL_DEADLINE_S = 270  # margin under the 5-minute budget


# --- Pipeline stages ------------------------------------------------------------------------

def build_crawl_artifact(url: str | None, html_file: str | None) -> dict:
    """Stage 1-2: safe fetch robots + raw HTML, render once (Tier A/B), assemble the artifact.

    TODO: use _safe_fetch.safe_get / check_robots; if robots disallows root -> return artifact
    flagged so main() emits the single stop finding; probe Playwright, render or fall back to
    the browserless heuristic. Validate against crawl-artifact-schema.json.
    """
    raise NotImplementedError


def run_consumer(skill_dir: str, script: str, artifact_path: Path, config_path: Path) -> list[dict]:
    """Invoke one sub-skill as a subprocess; parse its JSON array of partial findings from stdout.

    TODO: subprocess.run([...python..., --artifact, --config]); on failure record a diagnostics
    error and return [] (never raise). Enforce per-stage soft timeout against the global deadline.
    """
    raise NotImplementedError


def assign_ids_and_summarize(findings: list[dict], config: dict) -> tuple[list[dict], dict]:
    """Deterministic sort (severity -> category -> skill order -> evidence hash), assign F-00N,
    compute summary counts + discoverability_score. Enforce total == critical+high+medium.

    TODO.
    """
    raise NotImplementedError


def validate_report(report: dict) -> None:
    """jsonschema-validate against REPORT_SCHEMA and assert the summary invariant in code. TODO."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Brand AI-readiness audit orchestrator.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Homepage URL to audit.")
    src.add_argument("--html-file", help="Local HTML file to audit offline (no network).")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to scoring-config.json.")
    parser.add_argument("--allow-external", action="store_true",
                        help="Permit entity-corroboration's optional Wikidata lookup.")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    started = time.monotonic()

    # TODO: orchestrate stages, assemble report dict, validate_report(report).
    # Placeholder so the CLI contract is exercisable before implementation:
    report = {"_status": "not_implemented", "elapsed_ms": int((time.monotonic() - started) * 1000)}
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
