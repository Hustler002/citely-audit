"""Acquisition- and fallback-layer resilience, found by auditing python.org.

Two defects, one in each tier, which between them turned a well-built site into a bad report:

  1. **The `networkidle` trap.** The renderer waited on a milestone that requires 500 ms with at
     most two connections in flight. Analytics beacons, chat widgets and long-polling hold a modern
     page above that line forever, so it never fires. Measured on python.org: `load` 1.2 s,
     `domcontentloaded` 3.9 s, `networkidle` 20.0 s and a timeout. Worse, the timeout DISCARDED a
     complete 68 KB DOM that Chromium was holding, and the audit fell back to Tier B.

  2. **The DOM-order proxy measured markup, not layout.** Tier B treated the first 2,500 characters
     of `<body>` as the first viewport. On python.org those 2,500 characters are mostly tag
     characters and mega-menu text: the first heading sits 16,151 characters in. The check reported
     "No heading near the top of the document" about a page whose hero is a heading, and the value
     proposition failed for the same reason. Human Orientation read 41.7.

Both fixes are asserted here against the shapes that caused them, plus the neighbouring shapes that
would break the same logic: inline SVG sprites, critical-CSS blocks, consent dialogs, hidden nodes,
logo-only headings, and a page that tries to farm the chrome discount.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for sub in ("audit-orchestrator", "engagement-orientation-audit", "quotability-density-audit",
            "crawl-render-extraction-audit", "entity-corroboration-audit"):
    sys.path.insert(0, str(REPO_ROOT / "skills" / sub / "scripts"))

import _render as R  # noqa: E402
import engagement_orientation as ENG  # noqa: E402
import quotability_density as QD  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
CONFIG = json.loads((REPO_ROOT / "config" / "scoring-config.json").read_text(encoding="utf-8"))
CHECKS = json.loads((REPO_ROOT / "config" / "checks.json").read_text(encoding="utf-8"))
REGISTRY = {c["id"]: c for c in CHECKS["checks"]}

HEADING = "orientation.heading_first_viewport"
CTA = "orientation.primary_cta"
VALUE = "orientation.value_proposition"


def thresholds(check_id):
    return REGISTRY[check_id].get("threshold") or {}


def states(rows):
    return {r["check_id"]: r["state"] for r in rows}


def artifact_for(html, *, lang="en", supported=True):
    return {"requested_url": "https://e.test/",
            "language": {"detected": lang, "source": "html_lang", "supported": supported},
            "pages": [{"url": "https://e.test/", "role": "homepage", "status": "ok",
                       "raw": {"status": 200, "content_type": "text/html", "byte_size": len(html),
                               "html_sha256": "x", "html": html},
                       "meta_robots": None, "x_robots_tag": None}]}


def orientation(html, **kw):
    return states(ENG.analyze(artifact_for(html, **kw), REGISTRY))


# =================================================================================================
# 1. The networkidle trap
# =================================================================================================
class FakePage:
    """The parts of a Playwright page `drive_page` actually touches."""

    def __init__(self, *, goto_raises=None, idle_raises=None, content="<html><body>"
                 + "Real content that a visitor can read. " * 8 + "</body></html>",
                 content_raises_times=0):
        self.goto_raises = goto_raises
        self.idle_raises = idle_raises
        self._content = content
        self._content_raises_times = content_raises_times
        self.calls = []

    def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(("goto", wait_until, timeout))
        if self.goto_raises:
            raise self.goto_raises

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append(("wait_for_load_state", state, timeout))
        if self.idle_raises:
            raise self.idle_raises

    def wait_for_timeout(self, ms):
        self.calls.append(("wait_for_timeout", ms))

    def content(self):
        self.calls.append(("content",))
        if self._content_raises_times > 0:
            self._content_raises_times -= 1
            raise RuntimeError(
                "Page.content: Unable to retrieve content because the page is navigating "
                "and changing the content.")
        return self._content


class PWTimeout(Exception):
    """Stands in for playwright's TimeoutError, which is matched by class name."""


PWTimeout.__name__ = "TimeoutError"


def drive(page, **over):
    kwargs = {"wait_until": "load", "nav_timeout_ms": 15000, "quiet_ms": 3000,
              "settle_ms": 1200, "budget_left_ms": lambda: 9999}
    kwargs.update(over)
    return R.drive_page(page, "https://e.test/", **kwargs)


def test_networkidle_is_not_the_configured_wait():
    """The regression itself. `networkidle` almost never fires, so it must not be what we wait on.

    Reverting this single config value reproduces the python.org failure in full, which is why it
    is asserted rather than left to review.
    """
    assert CONFIG["render"]["wait_until"] != "networkidle"
    assert CONFIG["render"]["wait_until"] in R.NAV_MILESTONES
    assert R.DEFAULT_WAIT_UNTIL != "networkidle"


def test_quiescence_is_bounded_separately_from_navigation():
    """Quiescence is still pursued — as a small bonus with its own budget, not as the gate."""
    render = CONFIG["render"]
    assert render["network_quiet_ms"] < render["nav_timeout_ms"] <= render["max_render_ms"]


def test_unknown_wait_until_falls_back_to_the_default():
    assert R.resolve_wait_until({"wait_until": "whenever"}) == R.DEFAULT_WAIT_UNTIL
    assert R.resolve_wait_until({}) == R.DEFAULT_WAIT_UNTIL


def test_operator_can_still_opt_into_networkidle():
    """Config keeps the final say; the change is to the default, not to what is permitted."""
    assert R.resolve_wait_until({"wait_until": "networkidle"}) == "networkidle"


def test_navigation_timeout_salvages_the_dom():
    """The 68 KB that python.org threw away.

    A navigation timeout means the milestone did not fire, not that there is no page.
    """
    page = FakePage(goto_raises=PWTimeout("Timeout 20000ms exceeded."))
    html, nav_state = drive(page)
    assert nav_state == "salvaged"
    assert "Real content" in html


def test_salvage_waits_for_a_harvestable_state_before_reading():
    """A timeout can land mid-navigation, where the DOM cannot be read at all.

    Found by forcing a 700 ms navigation timeout against a live python.org, which a stub could not
    have reproduced: `content()` raised "the page is navigating and changing the content" and the
    salvage was worth nothing. Reaching `domcontentloaded` first gives it something to read.
    """
    page = FakePage(goto_raises=PWTimeout("Timeout 700ms exceeded."))
    drive(page)
    waits = [c for c in page.calls if c[:2] == ("wait_for_load_state", "domcontentloaded")]
    assert waits, "salvage read the DOM without giving it a harvestable state"
    assert 0 < waits[0][2] <= 15000, "the settle wait must stay inside the render budget"


def test_a_clean_navigation_does_not_wait_for_domcontentloaded_again():
    page = FakePage()
    drive(page)
    assert not [c for c in page.calls if c[:2] == ("wait_for_load_state", "domcontentloaded")]


def test_dom_read_is_retried_while_the_page_is_still_navigating():
    page = FakePage(content_raises_times=2)
    html, _ = drive(page)
    assert "Real content" in html
    assert len([c for c in page.calls if c == ("content",)]) == 3


def test_dom_read_gives_up_rather_than_retrying_forever():
    page = FakePage(content_raises_times=99)
    with pytest.raises(RuntimeError):
        drive(page)
    assert len([c for c in page.calls if c == ("content",)]) == R.HARVEST_ATTEMPTS


def test_dom_read_stops_retrying_when_the_budget_is_gone():
    page = FakePage(content_raises_times=99)
    with pytest.raises(RuntimeError):
        R.harvest_dom(page, lambda: 0)
    assert len([c for c in page.calls if c == ("content",)]) == 1


def test_a_page_that_never_goes_quiet_is_recorded_not_failed():
    page = FakePage(idle_raises=PWTimeout("Timeout 3000ms exceeded."))
    html, nav_state = drive(page)
    assert nav_state == "busy"
    assert "Real content" in html


def test_clean_navigation_reports_ok():
    assert drive(FakePage())[1] == "ok"


def test_real_navigation_failure_is_not_salvaged():
    """A refused connection or an unresolvable host leaves nothing worth keeping, so it degrades
    to Tier B rather than being dressed up as a successful render."""
    page = FakePage(goto_raises=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
    with pytest.raises(RuntimeError):
        drive(page)


def test_blank_document_is_never_passed_off_as_a_render():
    page = FakePage(goto_raises=PWTimeout("Timeout exceeded."),
                    content="<html><head></head><body></body></html>")
    with pytest.raises(RuntimeError):
        drive(page)


@pytest.mark.parametrize("html", [None, "", "   ", "<html><body></body></html>"])
def test_blank_document_detection(html):
    assert R.is_blank_document(html)


def test_real_document_is_not_blank():
    assert not R.is_blank_document(
        "<html><body><h1>Northwind Freight</h1><p>" + "Pallets. " * 30 + "</p></body></html>")


def test_timeout_classification_distinguishes_timeouts_from_failures():
    assert R.is_nav_timeout(PWTimeout("Timeout 20000ms exceeded."))
    assert R.is_nav_timeout(Exception("Timeout 20000ms exceeded."))
    assert not R.is_nav_timeout(Exception("net::ERR_CONNECTION_REFUSED"))
    assert not R.is_nav_timeout(Exception("Target page, context or browser has been closed"))


def test_navigation_never_receives_a_zero_timeout():
    """Playwright reads `timeout=0` as "wait forever" — the one value that must never reach it.

    With no budget left the render declines instead of hanging past the global deadline.
    """
    config = {**CONFIG, "render": {**CONFIG["render"], "max_render_ms": 0}}
    result = R.render_with_browser("https://e.test/", config)
    assert not result.available
    assert "budget" in (result.error or "")


def test_quiet_wait_is_skipped_rather_than_given_an_infinite_timeout():
    page = FakePage()
    drive(page, budget_left_ms=lambda: 0)
    assert not [c for c in page.calls if c[0] == "wait_for_load_state"]
    assert ("wait_for_timeout", 0) in page.calls


def test_nav_state_reaches_the_report():
    """A salvaged render is a Tier-A result, and a reader deserves to tell it from a clean one."""
    import run_audit  # noqa: E402  (imported here so the module list above stays about renderers)
    assert "render_nav_state" in Path(run_audit.__file__).read_text(encoding="utf-8")


# =================================================================================================
# 2. The fold-window blindspot
# =================================================================================================
def window_for(html, check_id=HEADING):
    return ENG.fold_window(html, thresholds(check_id))


NAV_LINKS = "".join(f'<li><a href="/s{i}/">Section {i} of the site</a></li>' for i in range(1, 40))
FILLER = "Body copy that a visitor would actually read on arrival. " * 4


def test_deep_chrome_fixture_finds_its_heading():
    """The python.org shape, distilled: the `<h1>` sits 41,752 characters into `<body>`.

    Under the old markup-slice proxy this page reported no heading, no action and no value
    proposition. Everything measurable now resolves.
    """
    html = (FIXTURES / "deep_chrome_page.html").read_text(encoding="utf-8")
    st = orientation(html)
    assert st[HEADING] == "pass"
    assert st[CTA] == "pass"
    assert st[VALUE] == "pass"


def test_old_markup_slice_would_still_fail_the_fixture():
    """Pins WHY the fixture is here. If someone reinstates a markup slice, this stops passing for
    the wrong reason — the fixture would look fine while the proxy had regressed."""
    html = (FIXTURES / "deep_chrome_page.html").read_text(encoding="utf-8")
    body = html[html.lower().index("<body"):]
    assert body.index("<h1>") > 2500 * 10


def test_inline_svg_sprite_does_not_consume_the_fold_budget():
    sprite = "".join(f'<symbol id="i{i}" viewBox="0 0 24 24"><path d="M12 {i}a8 8 0 1 0 .1 0z"/>'
                     f'</symbol>' for i in range(200))
    html = (f'<html lang="en"><body><svg style="display:none">{sprite}</svg>'
            f'<h1>Same-day pallet delivery</h1><p>{FILLER}</p>'
            f'<a href="/quote/">Get a quote</a></body></html>')
    assert orientation(html)[HEADING] == "pass"


def test_critical_css_block_does_not_consume_the_fold_budget():
    css = "".join(f".u-{i}{{margin:{i}px;padding:{i}px;color:#2b5a7c;font-size:{12 + i % 6}px;}}"
                  for i in range(400))
    html = (f'<html lang="en"><body><style>{css}</style>'
            f'<h1>Same-day pallet delivery</h1><p>{FILLER}</p></body></html>')
    assert orientation(html)[HEADING] == "pass"


def test_mega_menu_does_not_push_content_out_of_the_fold():
    """A collapsed mega-menu occupies one bar on screen however many links it holds."""
    html = (f'<html lang="en"><body><nav><ul>{NAV_LINKS}</ul></nav>'
            f'<main><h1>Same-day pallet delivery</h1><p>{FILLER}</p></main></body></html>')
    st = orientation(html)
    assert st[HEADING] == "pass"
    assert st[CTA] == "pass"


def test_consent_dialog_does_not_push_content_out_of_the_fold():
    """A cookie banner floats over the page rather than displacing it, so in DOM order it must not
    consume the budget. Whether it OBSCURES the content is a different check, and one that stays
    `unknown` in Tier B rather than guessing."""
    banner = ('<div role="dialog" aria-modal="true"><h2>We value your privacy</h2><p>'
              + "We and our 812 partners store and access information on your device. " * 12
              + '</p><button>Accept all</button><button>Reject</button></div>')
    html = (f'<html lang="en"><body>{banner}'
            f'<main><h1>Same-day pallet delivery</h1><p>{FILLER}</p></main></body></html>')
    st = orientation(html)
    assert st[HEADING] == "pass"
    assert st["orientation.content_not_obstructed"] == "unknown"


def test_consent_dialog_cannot_supply_the_value_proposition():
    """A privacy notice is not a statement of what the business offers."""
    banner = ('<div role="dialog"><h1>We value your privacy and build a better platform</h1>'
              '<p>' + "Manage your cookie preferences and tracking choices here. " * 6 + '</p></div>')
    html = f'<html lang="en"><body>{banner}<main><p>{FILLER}</p></main></body></html>'
    row = next(r for r in ENG.analyze(artifact_for(html), REGISTRY) if r["check_id"] == VALUE)
    assert row["state"] == "fail"
    assert "privacy" not in (row["evidence"] or "").lower()


def test_hidden_nodes_do_not_consume_the_fold_budget():
    buried = "".join(
        f'<div hidden>{"Deferred panel copy that never renders on arrival. " * 6}</div>'
        f'<div aria-hidden="true">{"Decorative duplicate text. " * 6}</div>'
        f'<div style="display:none">{"Collapsed accordion body copy. " * 6}</div>'
        for _ in range(6))
    html = (f'<html lang="en"><body>{buried}'
            f'<h1>Same-day pallet delivery</h1><p>{FILLER}</p></body></html>')
    assert orientation(html)[HEADING] == "pass"


def test_hidden_heading_cannot_satisfy_the_check():
    """Symmetry: if hidden text does not cost anything, it must not earn anything either."""
    html = (f'<html lang="en"><body><div hidden><h1>Same-day pallet delivery</h1></div>'
            f'<p>{FILLER}</p></body></html>')
    assert orientation(html)[HEADING] == "fail"


def test_wrapping_the_page_in_nav_does_not_buy_free_space():
    """The chrome discount is capped PER CHROME ROOT, so it cannot be farmed.

    Sized deliberately: with `dom_proxy_text_chars` 1200, `chrome_text_weight` 0.1 and
    `chrome_text_cap` 400, the cap covers 4,000 raw characters. 6,000 characters of nav text costs
    400 for the first 4,000 and full price for the rest — 2,400, which exhausts the budget. Remove
    the cap and the same page costs 600 and sails under it. A larger `<nav>` would fail either way
    and would therefore prove nothing about the cap.
    """
    th = thresholds(HEADING)
    allowance = th["chrome_text_cap"] / th["chrome_text_weight"]
    unit = "Assorted navigation link text that goes on and on. "
    filler = unit * int((allowance * 1.5) // len(unit))
    assert allowance < len(filler) < th["dom_proxy_text_chars"] / th["chrome_text_weight"], (
        "fixture no longer discriminates between the capped and uncapped discount")
    html = (f'<html lang="en"><body><nav>{filler}</nav>'
            f'<main><h1>Same-day pallet delivery</h1><p>{FILLER}</p></main></body></html>')
    assert orientation(html)[HEADING] == "fail"


def test_a_nav_within_its_allowance_is_still_discounted():
    """The other side of the cap: an ordinary mega-menu must stay cheap, or the fix that started
    all this would be undone by the guard against farming it."""
    th = thresholds(HEADING)
    allowance = th["chrome_text_cap"] / th["chrome_text_weight"]
    unit = "Section link text. "
    filler = unit * int((allowance * 0.7) // len(unit))
    html = (f'<html lang="en"><body><nav>{filler}</nav>'
            f'<main><h1>Same-day pallet delivery</h1><p>{FILLER}</p></main></body></html>')
    assert orientation(html)[HEADING] == "pass"


def test_navigation_cannot_supply_the_headline():
    html = (f'<html lang="en"><body><nav><h1>Build a better platform with our software</h1>'
            f'<p>{FILLER}</p></nav></body></html>')
    row = next(r for r in ENG.analyze(artifact_for(html), REGISTRY) if r["check_id"] == VALUE)
    assert row["state"] == "fail"


def test_banner_can_supply_the_headline():
    """Heroes legitimately live inside `<header>`; python.org and our own healthy fixture both do
    it. Excluding all chrome would have created a fresh false negative on every such site."""
    html = ('<html lang="en"><body><header><h1>We build software that helps teams ship</h1>'
            f'<p>{FILLER}</p></header></body></html>')
    row = next(r for r in ENG.analyze(artifact_for(html), REGISTRY) if r["check_id"] == VALUE)
    assert row["state"] in ("pass", "partial")


def test_logo_only_heading_counts_through_its_alt_text():
    """`<h1><img alt="Acme"></h1>` holds no text node but is exactly what a visitor sees."""
    html = (f'<html lang="en"><body><h1><img src="/logo.png" alt="Northwind Freight"></h1>'
            f'<p>{FILLER}</p></body></html>')
    assert orientation(html)[HEADING] == "pass"


def test_both_tiers_read_an_image_only_heading_the_same_way():
    """Tier A used `innerText`, which is empty for a logo heading, so the two tiers disagreed about
    the same page. The geometry script now applies the same accessible-name fallback."""
    assert "aria-label" in R.GEOMETRY_JS and "getAttribute('alt')" in R.GEOMETRY_JS


def test_untitled_image_heading_still_fails():
    html = f'<html lang="en"><body><h1><img src="/logo.png"></h1><p>{FILLER}</p></body></html>'
    assert orientation(html)[HEADING] == "fail"


def test_spa_shell_still_reports_nothing_above_the_fold():
    """The window got more generous; it must not have got credulous."""
    html = '<html lang="en"><body><div id="root"></div><script src="/app.js"></script></body></html>'
    st = orientation(html)
    assert st[HEADING] == "fail"
    assert st[CTA] == "fail"


def test_content_genuinely_below_the_fold_still_fails():
    html = (f'<html lang="en"><body><main><p>{"Long introductory prose. " * 300}</p>'
            f'<h1>Finally, a heading</h1></main></body></html>')
    assert orientation(html)[HEADING] == "fail"


def test_window_survives_markup_that_would_break_a_slice():
    """The old proxy cut `<body>` at a character offset, which slices through tags and attribute
    values. Parsing first removes the whole failure class."""
    for html in ('<html lang="en"><body><div class="a',
                 '<html lang="en"><body><h1>Unclosed heading<p>text',
                 '<html lang="en"><body><!-- comment with <h1>fake</h1> --><h1>Real one</h1>',
                 '<html lang="en"><frameset><frame src="/x"></frameset></html>'):
        ENG.analyze(artifact_for(html), REGISTRY)  # must not raise


def test_window_walk_is_bounded():
    """A pathological DOM must cost bounded time, not proportional-to-hostile-input time."""
    html = '<html lang="en"><body>' + "<div>" * 2 + ("<span>x</span>" * 60000) + "</div></div>"
    window = window_for(html)
    assert window.truncated


def test_fold_window_is_reused_across_checks():
    """Three checks ask for the same window on the same page; parsing it three times per page is
    render budget spent for nothing."""
    html = (FIXTURES / "deep_chrome_page.html").read_text(encoding="utf-8")
    assert window_for(html) is window_for(html)


# =================================================================================================
# Cross-cutting: the fixes must not move the sites we already had right
# =================================================================================================
@pytest.mark.parametrize("name", ["healthy_page.html", "non_english_page.html"])
def test_known_good_fixtures_are_unmoved(name):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    lang = "de" if "non_english" in name else "en"
    st = orientation(html, lang=lang, supported=(lang == "en"))
    assert st[HEADING] == "pass"
    assert st[CTA] == "pass"


def test_embedded_thresholds_match_registry_for_the_parity_analyzers():
    """Extends the threshold-drift guard to the comprehension and orientation analyzers.

    Each skill is self-contained by design, so its embedded defaults are a real second copy of the
    registry values and can drift from them silently.
    """
    for module in (ENG, QD):
        for check_id, embedded in module.DEFAULT_THRESHOLDS.items():
            actual = REGISTRY[check_id].get("threshold") or {}
            for key, value in embedded.items():
                if key.startswith("_"):
                    continue
                assert actual.get(key) == value, f"{module.__name__}: {check_id}.{key} drifted"


def test_registry_no_longer_advertises_the_markup_slice():
    for check_id in (HEADING, CTA, VALUE):
        th = thresholds(check_id)
        assert "dom_proxy_chars" not in th
        assert th["dom_proxy_text_chars"] > 0
        assert 0 < th["chrome_text_weight"] <= 1
        assert th["chrome_text_cap"] > 0
