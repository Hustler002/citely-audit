"""End-to-end orchestrator tests against the localhost fixture server. SKELETON.

Asserts (plan §9.3 / §9.4):
  - Full audit of the healthy + broken pages produces a report valid against report-schema.json.
  - summary invariant holds: total_findings == critical + high + medium.
  - Finding IDs are deterministic (stable across repeated runs).
  - The broken page scores materially lower than the healthy page.
  - REGENERATES skills/audit-orchestrator/references/sample-report.json from the broken-page run
    and diffs it (keeps the checked-in sample in sync).
  - Tier-B path: with Playwright disabled -> diagnostics.render_mode == "heuristic",
    playwright_available is False, hybrid informational finding present, audit still completes.
  - Budget: run completes well under 5 minutes.
"""
import pytest

pytestmark = pytest.mark.skip(reason="skeleton — pipeline not implemented yet")


def test_report_validates_against_schema():
    ...


def test_summary_invariant_and_deterministic_ids():
    ...


def test_broken_scores_lower_than_healthy():
    ...


def test_regenerates_sample_report():
    ...


def test_tier_b_heuristic_fallback():
    ...
