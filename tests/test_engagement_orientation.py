"""Unit tests for engagement-orientation-audit (offline). SKELETON.

Asserts:
  - broken_page.html -> weak/absent value-proposition + missing CTA findings.
  - healthy_page.html -> H1 + value prop + CTA present; no critical orientation finding.
  - Determinism: same input -> byte-identical findings across runs.
"""
import pytest

pytestmark = pytest.mark.skip(reason="skeleton — checks not implemented yet")


def test_broken_page_missing_value_prop():
    ...


def test_healthy_page_has_orientation():
    ...
