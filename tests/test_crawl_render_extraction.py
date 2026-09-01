"""Unit tests for crawl-render-extraction-audit (offline, --html-file mode). SKELETON.

Asserts (to implement alongside the checks):
  - broken_page.html -> a critical crawl-ingestion finding (CSR shell / facts-in-images).
  - healthy_page.html -> no critical crawl-ingestion finding.
  - Every finding matches the partial-finding contract (title/severity/category/confidence/evidence/suggested_action).
"""
import pytest

pytestmark = pytest.mark.skip(reason="skeleton — checks not implemented yet")


def test_broken_page_flags_csr_shell():
    ...


def test_healthy_page_clean():
    ...
