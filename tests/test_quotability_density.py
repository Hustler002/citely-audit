"""Unit tests for quotability-density-audit (offline). SKELETON.

Asserts:
  - broken_page.html -> low quotable-sentence count finding.
  - healthy_page.html -> quotable factual sentences detected; no critical quotability finding.
  - Vague-marketing-token detection flags configured tokens.
"""
import pytest

pytestmark = pytest.mark.skip(reason="skeleton — checks not implemented yet")


def test_broken_page_low_quotability():
    ...


def test_healthy_page_has_quotable_facts():
    ...
