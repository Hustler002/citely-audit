"""Unit tests for entity-corroboration-audit (offline; no --allow-external). SKELETON.

Asserts:
  - broken_page.html -> missing-entity-graph finding (no JSON-LD Organization).
  - healthy_page.html -> Organization + sameAs detected; no critical entity finding.
  - Wikidata lookup stays disabled without --allow-external (no network in unit tests).
"""
import pytest

pytestmark = pytest.mark.skip(reason="skeleton — checks not implemented yet")


def test_broken_page_missing_entity_graph():
    ...


def test_healthy_page_has_sameas():
    ...
