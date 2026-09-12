"""Three measurement defects, each of which reported a problem the page did not have.

Found by auditing dev.to, which returned five findings of which three were wrong. The site is a
server-rendered Rails application with a clear hero, a normal title and no charts, and it was told
its facts were locked in pictures, that it had no value proposition, and — twice, both `critical` —
that its identity was undeclared.

  1. **Geometry counted `<script>` as page text.** A TreeWalker over SHOW_TEXT returns the contents
     of `<script>` and `<style>`, and those elements report top = 0, so inline JavaScript read as
     the first thing a visitor sees. 3,301 of dev.to's 6,214 reported above-the-fold characters
     were source code, and the value-proposition check was handed
     `if (navigator.userAgent === 'ForemWebView/1')` instead of the site's own hero copy.
  2. **"A digit in the filename" meant "this image carries a fact."** Every modern build pipeline
     fingerprints assets, so that matched almost everything: 79 of 108 images on dev.to, 59 of them
     18x18 reaction icons named `exploding-head-daceb38d....svg`.
  3. **Severity ignored state.** A `partial` was announced at the check's full declared severity,
     so a half-credit Open Graph identity was reported as `critical` beside a genuine total
     failure, and a title two characters under the floor was reported as `high`.

A note on what was NOT changed. The reported root cause for (1) was that `wait_until: load`
snapshotted the page before hydration. Measured instead of assumed: `domcontentloaded`, `load` and
`networkidle` return a byte-identical 263,269-character DOM on dev.to, which is server-rendered.
`test_render_resilience.py` covers the wait strategy, and the hydrating-SPA fixture proves the
settle window still catches real hydration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for sub in ("audit-orchestrator", "crawl-render-extraction-audit"):
    sys.path.insert(0, str(REPO_ROOT / "skills" / sub / "scripts"))

import _render as R  # noqa: E402
import crawl_render_extract as CRE  # noqa: E402
import run_audit as RA  # noqa: E402

REGISTRY = {c["id"]: c
            for c in json.loads((REPO_ROOT / "config" / "checks.json").read_text(encoding="utf-8"))["checks"]}
IMAGE_CHECK = "extraction.facts_not_image_only"


# =================================================================================================
# 1. Above-the-fold text must be text a visitor can read
# =================================================================================================
NOISY_PAGE = """
<html lang="en"><head><title>Kettleby Cycles</title></head><body>
  <script>
    if (navigator.userAgent === 'ForemWebView/1' || window.frameElement) {
      document.body.classList.add("hidden-shell");
    }
    window.dataLayer = window.dataLayer || []; function gtag(){dataLayer.push(arguments);}
  </script>
  <style>.masthead{display:flex;align-items:center;padding:12px 24px;font-size:15px}</style>
  <div hidden><p>Hidden panel copy that never renders on arrival at all.</p></div>
  <h1>Kettleby Cycles</h1>
  <p>We build steel touring frames by hand in Lincolnshire and fit them to you in person.</p>
</body></html>
"""


@pytest.mark.slow
def test_script_and_style_text_is_not_counted_as_above_fold_text():
    """The defect, measured in a real browser because it is a layout fact."""
    playwright_available, _reason = R.probe_playwright()
    if not playwright_available:
        pytest.skip("Tier A needs a provisioned Chromium")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-dev-shm-usage"])
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            page.set_content(NOISY_PAGE)
            geometry = page.evaluate(R.GEOMETRY_JS) or {}
        finally:
            browser.close()

    text = geometry.get("above_fold_text") or ""
    assert "steel touring frames" in text, "real page copy went missing"
    assert "navigator.userAgent" not in text, "inline JavaScript was read as page text"
    assert "dataLayer" not in text
    assert "align-items" not in text, "inline CSS was read as page text"
    assert "Hidden panel copy" not in text, "a node with no layout box was read as visible"


def test_the_geometry_script_declares_the_exclusion():
    """A cheap guard for environments with no browser, so the rule cannot be quietly deleted."""
    assert "SCRIPT|STYLE|NOSCRIPT|TEMPLATE" in R.GEOMETRY_JS
    assert "rect.width === 0 && rect.height === 0" in R.GEOMETRY_JS


# =================================================================================================
# 2. A build fingerprint is not a fact
# =================================================================================================
@pytest.mark.parametrize("filename", [
    # Content hashes, the shape every modern bundler emits.
    "exploding-head-daceb38d627e6ae9b730f36a1e390fca556a4289d5a41abb2c35068ad3e2c4b5.svg",
    "multi-unicorn-b44d6f8c23cdd00964192bedc38af3e82463978aa611b4365bd33a0f1f.svg",
    "app.4f3c2b1a9e8d.css.png",
    # Opaque identifiers.
    "3535771.jpg",
    "e22860d5-274b-43c9-819b-56b162e5bd5a.jpeg",
    # A proxied asset path, URL-encoded.
    "https%3A%2F%2Fdev-to-uploads.s3.us-east-2.amazonaws.com%2Fuploads%2Fuser%2F3535771%2Fx.jpeg",
    # No number at all.
    "logo.svg", "hero-image.png",
])
def test_fingerprints_and_decoration_do_not_look_like_data(filename):
    assert not CRE.looks_like_data_filename(filename)


@pytest.mark.parametrize("filename", [
    "revenue-2024.png", "q3-results-chart.svg", "market-share-2023-q1.jpg",
    "chart_2019_growth.png", "uptime-99-percent.png",
])
def test_a_named_data_image_still_looks_like_data(filename):
    """The check must keep the detection it exists for."""
    assert CRE.looks_like_data_filename(filename)


def _image_state(html, url="https://e.test/"):
    page = {"url": url, "role": "homepage", "status": "ok",
            "raw": {"status": 200, "html": html, "content_type": "text/html"}}
    return CRE.check_facts_not_image_only(
        page, url, REGISTRY[IMAGE_CHECK].get("threshold") or {})


def test_ui_icons_are_excluded_by_their_declared_size():
    """59 of dev.to's 79 flagged images were 18x18 reaction icons. The page itself says so."""
    icons = "".join(
        f'<img src="/assets/icon-{i}-a1b2c3d4e5f6a7b8c9d0.svg" width="18" height="18" alt="">'
        for i in range(12))
    row = _image_state(f"<html><body>{icons}</body></html>")
    assert row["state"] == "not_applicable"
    assert "large enough" in (row["reason"] or "")


def test_a_page_of_fingerprinted_photos_is_not_a_page_of_hidden_charts():
    photos = "".join(
        f'<img src="/img/photo-{i}-9f8e7d6c5b4a3928.jpg" width="600" height="400" alt="">'
        for i in range(10))
    assert _image_state(f"<html><body>{photos}</body></html>")["state"] == "pass"


def test_genuinely_undescribed_charts_still_fail():
    """The regression that matters most: precision must not have cost the detection."""
    charts = "".join(
        f'<img src="/img/revenue-202{i}.png" width="800" height="600" alt="">' for i in range(5))
    row = _image_state(f"<html><body>{charts}</body></html>")
    assert row["state"] == "fail"
    assert "revenue-2020.png" in row["evidence"]


def test_a_described_chart_is_not_flagged():
    charts = "".join(
        f'<img src="/img/revenue-202{i}.png" width="800" height="600" '
        f'alt="Revenue grew 42 percent in 202{i}">' for i in range(5))
    assert _image_state(f"<html><body>{charts}</body></html>")["state"] == "pass"


def test_a_large_chart_among_small_icons_is_still_judged():
    """The size filter narrows the population; it must not let a real chart escape with it."""
    icons = "".join(f'<img src="/i/x-{i}-abcdef1234567890.svg" width="16" height="16" alt="">'
                    for i in range(40))
    charts = "".join(f'<img src="/img/q{i}-results-2025.png" width="900" height="500" alt="">'
                     for i in range(4))
    row = _image_state(f"<html><body>{icons}{charts}</body></html>")
    assert row["state"] == "fail"
    assert "4" in str(row["measurement"])


# -------------------------------------------------------------------------------------------------
# An inline asset has no filename, and its payload is not evidence
#
# Same defect family as the fingerprint rule above, one layer earlier: the fingerprint fix made the
# NAME harder to satisfy, while `data:` URLs have no name at all. `urlparse(src).path` on a data URL
# returns its base64 payload, and splitting that on "/" — a character in the base64 alphabet —
# yields a random chunk carrying both letters and digits, which is exactly the shape the rule reads
# as `revenue-2024`. Found on a government homepage with a single inline PNG.
# -------------------------------------------------------------------------------------------------
def _inline_png(seed: int, size: int = 900) -> str:
    import base64
    import random
    return "data:image/png;base64," + base64.b64encode(
        random.Random(seed).randbytes(size)).decode()


@pytest.mark.parametrize("seed", range(25))
def test_an_inline_base64_image_is_never_read_as_a_fact_image(seed):
    """Its payload is bytes, not a name. Measured before the fix: 84% of random payloads matched."""
    assert not CRE.looks_like_data_filename(CRE.asset_filename(_inline_png(seed)))


@pytest.mark.parametrize("src", [
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg",
    "data:image/svg+xml;utf8,<svg viewBox='0 0 24 24'><path d='M3 12h18'/></svg>",
    "DATA:image/gif;base64,R0lGODlhAQABAIAAAP",          # scheme is case-insensitive
    "  data:image/png;base64,iVBORw0KGgo",               # and may be padded by the author
    "blob:https://example.test/9f1c2e4a-2025-11",
])
def test_an_opaque_asset_url_yields_no_filename_to_judge(src):
    assert CRE.asset_filename(src) == ""
    assert not CRE.looks_like_data_filename(CRE.asset_filename(src))


@pytest.mark.parametrize("src,expected", [
    ("https://s.test/a/revenue-2024.png", "revenue-2024.png"),
    ("/img/q3-results-chart.svg", "q3-results-chart.svg"),
    ("https://s.test/img/hero.jpg?v=2", "hero.jpg"),
])
def test_an_ordinary_asset_url_still_yields_its_filename(src, expected):
    """The fix must narrow nothing except the case that has no name."""
    assert CRE.asset_filename(src) == expected


def test_a_page_of_inline_images_is_not_a_page_of_hidden_charts():
    """The end-to-end shape: inline icons are ordinary, and must not manufacture a finding."""
    inline = "".join(f'<img src="{_inline_png(i)}" width="120" height="120" alt="">'
                     for i in range(8))
    assert _image_state(f"<html><body>{inline}</body></html>")["state"] == "pass"


def test_a_real_chart_beside_inline_icons_is_still_caught():
    """The contrasting case: excluding inline assets must not hide a genuine defect next to them."""
    inline = "".join(f'<img src="{_inline_png(i)}" width="120" height="120" alt="">'
                     for i in range(2))
    charts = "".join(f'<img src="/img/revenue-202{i}.png" width="800" height="600" alt="">'
                     for i in range(6))
    row = _image_state(f"<html><body>{inline}{charts}</body></html>")
    assert row["state"] == "fail"
    assert "revenue-2020.png" in row["evidence"]
    assert "base64" not in row["evidence"], "a payload must never reach the report as evidence"


def test_an_unjudgeable_image_counts_as_present_not_as_suspect():
    """Where an inline asset lands in the ratio, asserted rather than left to chance.

    It stays in the DENOMINATOR — it is an image on the page, above the size floor — and out of the
    numerator, because we have no name to read. That is exactly how `hero.jpg` is already treated:
    a name carrying no fact signal is not evidence of a hidden chart. The consequence is that
    inline assets dilute the ratio, which is the same dilution any ordinary photo produces.
    """
    inline = "".join(f'<img src="{_inline_png(i)}" width="120" height="120" alt="">'
                     for i in range(6))
    charts = "".join(f'<img src="/img/revenue-202{i}.png" width="800" height="600" alt="">'
                     for i in range(4))
    row = _image_state(f"<html><body>{inline}{charts}</body></html>")
    assert row["state"] == "partial"
    assert "4/10" in str(row["measurement"]), "inline assets must be counted, not dropped"


# =================================================================================================
# 3. Severity must describe what was measured
# =================================================================================================
def test_a_partial_is_reported_one_tier_below_the_declared_severity():
    assert RA.severity_for_state("critical", "partial") == "high"
    assert RA.severity_for_state("high", "partial") == "medium"


def test_medium_is_the_floor_because_there_is_no_low_tier():
    """The strict 3-tier set is locked, and the summary invariant depends on it."""
    assert RA.severity_for_state("medium", "partial") == "medium"
    assert set(RA.SEVERITY_ORDER) == {"critical", "high", "medium"}


@pytest.mark.parametrize("state", ["fail", "pass", "unknown"])
def test_only_a_partial_is_demoted(state):
    assert RA.severity_for_state("critical", state) == "critical"


def test_the_double_critical_shape_is_resolved_without_suppressing_anything():
    """dev.to was told twice, in `critical`, that its identity was undeclared: once for having no
    JSON-LD, once for having only Open Graph — which is a partial already awarded half credit.

    The reported fix was to suppress the second check when the first fails. Measured, that costs
    24 points of coverage to gain 5 of score, and it reinstates the collateral damage removed on
    2026-09-08, where one verified failure wiped 44% of the category. Demoting the partial instead
    keeps both findings, keeps full coverage, and leaves the score untouched.
    """
    declared = REGISTRY["entity.organization_declared"]["severity"]
    assert declared == "critical"
    assert RA.severity_for_state(declared, "partial") == "high"
    assert not any(d.startswith("entity.")
                   for d in REGISTRY["entity.organization_declared"]["depends_on"])


def test_severity_demotion_preserves_the_summary_invariant():
    """`total_findings == critical + high + medium` is the mandated floor; demotion must not move a
    finding outside the counted set."""
    import _scoring as S
    findings = [{"id": "F-001", "severity": RA.severity_for_state(sev, state)}
                for sev in RA.SEVERITY_ORDER for state in ("fail", "partial")]
    summary = S.summarize(findings, {}, 50.0, 1.0)
    assert summary["total_findings"] == len(findings)
    assert summary["critical"] + summary["high"] + summary["medium"] == len(findings)
    # Demotion redistributes across the tiers rather than dropping anything.
    assert (summary["critical"], summary["high"], summary["medium"]) == (1, 2, 3)
