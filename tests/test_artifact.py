"""Page selection, rendering, and artifact assembly.

Runs against the localhost fixture server, so the whole acquisition pipeline is exercised for real
(sockets, redirects, robots, sitemaps) with zero external egress.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import _artifact as A  # noqa: E402
import _page_select as PS  # noqa: E402
import _render as R  # noqa: E402
import _safe_fetch as SF  # noqa: E402
import _scoring as S  # noqa: E402
import fixture_server  # noqa: E402

ARTIFACT_SCHEMA = json.loads(
    (REPO_ROOT / "skills" / "audit-orchestrator" / "references" / "crawl-artifact-schema.json")
    .read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    """Config with the documented test-only escape hatch enabled.

    The SSRF guard correctly refuses 127.0.0.1, so reaching our own fixture server requires it.
    """
    cfg = S.load_config()
    cfg = {**cfg, "fetch": {**cfg["fetch"], "allow_private_hosts": True}}
    return cfg


@pytest.fixture(scope="module")
def server():
    with fixture_server.running() as base_url:
        yield base_url


# --- URL helpers ------------------------------------------------------------------------------
@pytest.mark.parametrize("a,b,expected", [
    ("https://example.com/", "https://www.example.com/x", True),
    ("https://example.com/", "https://blog.example.com/", False),
    ("https://example.com/", "https://evil.com/", False),
])
def test_same_site(a, b, expected):
    assert PS.same_site(a, b) is expected


@pytest.mark.parametrize("url,depth", [
    ("https://e.com/", 0), ("https://e.com/a", 1), ("https://e.com/a/b/c", 3),
])
def test_path_depth(url, depth):
    assert PS.path_depth(url) == depth


def test_normalize_strips_fragment():
    assert PS.normalize_url("https://e.com/a#frag") == "https://e.com/a"


# --- Page selection is structural, not lexical --------------------------------------------------
def test_selection_is_language_neutral(config):
    """The core language-neutrality guarantee: a non-English site must select just as many pages as
    an English one.

    The old English keyword approach would have found zero extra pages here.
    """
    english = """<html><body><nav>
        <a href="/about">About</a><a href="/pricing">Pricing</a></nav></body></html>"""
    german = """<html><body><nav>
        <a href="/ueber-uns">Uber uns</a><a href="/preise">Preise</a></nav></body></html>"""
    japanese = """<html><body><nav>
        <a href="/kaisha">会社概要</a><a href="/ryokin">料金</a></nav></body></html>"""

    counts = {
        lang: len(PS.select_pages("https://e.com/", html, config))
        for lang, html in (("en", english), ("de", german), ("ja", japanese))
    }
    assert counts["en"] == counts["de"] == counts["ja"] == 3


def test_homepage_always_first(config):
    pages = PS.select_pages("https://e.com/", "<nav><a href='/a'>a</a></nav>", config)
    assert pages[0].role == "homepage"
    assert pages[0].url == "https://e.com/"


def test_extra_pages_are_role_other(config):
    """Roles are not guessed from URLs — that would reintroduce the lexical assumption."""
    pages = PS.select_pages("https://e.com/", "<nav><a href='/about'>x</a></nav>", config)
    assert all(p.role == "other" for p in pages[1:])


def test_shallower_paths_rank_higher(config):
    html = "<nav><a href='/a/b/c'>deep</a><a href='/x'>shallow</a></nav>"
    pages = PS.select_pages("https://e.com/", html, config)
    assert pages[1].url == "https://e.com/x"


def test_offsite_and_asset_links_excluded(config):
    html = """<nav><a href='https://other.com/x'>off</a><a href='/doc.pdf'>pdf</a>
              <a href='mailto:a@b.c'>mail</a><a href='/ok'>ok</a></nav>"""
    urls = [p.url for p in PS.select_pages("https://e.com/", html, config)]
    assert urls == ["https://e.com/", "https://e.com/ok"]


def test_max_pages_respected(config):
    html = "<nav>" + "".join(f"<a href='/p{i}'>p</a>" for i in range(20)) + "</nav>"
    cfg = {**config, "page_selection": {**config["page_selection"], "max_pages": 3}}
    assert len(PS.select_pages("https://e.com/", html, cfg)) == 3


def test_depth_limit_enforced(config):
    html = "<nav><a href='/a/b/c/d/e'>too deep</a></nav>"
    assert len(PS.select_pages("https://e.com/", html, config)) == 1


# --- Sitemap handling degrades silently ---------------------------------------------------------
def test_sitemap_parsed(config):
    xml = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://e.com/a</loc></url><url><loc>https://e.com/b</loc></url></urlset>"""
    pages, children = PS.parse_sitemap(xml, config)
    assert pages == ["https://e.com/a", "https://e.com/b"]
    assert children == []


def test_sitemap_index_returns_children(config):
    xml = b"""<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://e.com/s1.xml</loc></sitemap></sitemapindex>"""
    pages, children = PS.parse_sitemap(xml, config)
    assert pages == [] and children == ["https://e.com/s1.xml"]


def test_gzipped_sitemap_handled(config):
    import gzip as gz
    xml = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://e.com/a</loc></url></urlset>"""
    pages, _ = PS.parse_sitemap(gz.compress(xml), config)
    assert pages == ["https://e.com/a"]


@pytest.mark.parametrize("raw", [b"", b"not xml at all", b"<urlset><loc>unclosed",
                                 b"\x1f\x8b corrupt gzip"])
def test_malformed_sitemap_degrades_silently(raw, config):
    """A bad sitemap must never fail the audit — it just yields nothing."""
    pages, children = PS.parse_sitemap(raw, config)
    assert pages == [] and children == []


def test_oversized_sitemap_truncated(config):
    cfg = {**config, "page_selection": {**config["page_selection"], "max_sitemap_bytes": 200}}
    big = b"<?xml version='1.0'?><urlset>" + b"<url><loc>https://e.com/x</loc></url>" * 500 + b"</urlset>"
    pages, _ = PS.parse_sitemap(big, cfg)
    assert isinstance(pages, list)  # truncated parse, no crash


# --- Tier B: browserless SPA detection ----------------------------------------------------------
def test_spa_signals_detect_empty_mount(config):
    html = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    sig = R.detect_spa_signals(html, config)
    assert sig["empty_mount_node"] == "#root"
    assert sig["client_rendered_likely"] is True


def test_spa_signals_detect_framework_marker(config):
    html = '<html><body><div>hi</div><script>window.__NEXT_DATA__={}</script></body></html>'
    sig = R.detect_spa_signals(html, config)
    assert "__NEXT_DATA__" in sig["framework_markers"]
    assert sig["client_rendered_likely"] is True


def test_spa_signals_quiet_on_server_rendered_page(config):
    html = "<html><body><main><h1>Real</h1>" + "<p>Substantial prose. </p>" * 60 + "</main></body></html>"
    sig = R.detect_spa_signals(html, config)
    assert sig["client_rendered_likely"] is False
    assert sig["framework_markers"] == []


def test_visible_text_strips_script_and_style():
    html = "<html><style>p{color:red}</style><script>var x=1</script><p>Hello</p></html>"
    assert R.visible_text(html) == "Hello"


def test_noscript_signal_detected(config):
    sig = R.detect_spa_signals("<html><body><noscript>Enable JS</noscript></body></html>", config)
    assert sig["has_noscript_warning"] is True


# --- Tier A / Tier B selection ------------------------------------------------------------------
def test_forced_tier_b_never_uses_browser(config, server):
    result = R.render_page(f"{server}/broken", "<div id='root'></div>", config, force_tier=R.TIER_B)
    assert result.mode == R.TIER_B
    assert result.available is False
    assert result.html is None
    assert result.heuristic_signals["client_rendered_likely"] is True


@pytest.mark.slow
def test_tier_a_renders_javascript_content(config, server):
    """The demo's core mechanic: content invisible in raw HTML appears after rendering."""
    available, _ = R.probe_playwright()
    if not available:
        pytest.skip("chromium not provisioned")

    raw = "<html><body><div id='root'></div></body></html>"
    result = R.render_page(f"{server}/healthy", raw, config)
    assert result.mode == R.TIER_A
    assert result.available is True
    assert result.html and len(R.visible_text(result.html)) > 100
    assert result.viewport == {"width": 1280, "height": 800}


@pytest.mark.slow
def test_render_diff_exposes_client_only_content(config, server):
    """THE core mechanic: facts absent from raw HTML must appear after rendering.

    Without this the whole render tier is unverified — a static shell whose bundles 404 would
    pass a naive 'did it render' test while proving nothing about the diff.
    """
    available, _ = R.probe_playwright()
    if not available:
        pytest.skip("chromium not provisioned")

    raw = SF.safe_get(f"{server}/spa-hydrating", config).body.decode()
    result = R.render_page(f"{server}/spa-hydrating", raw, config)

    raw_len = len(R.visible_text(raw))
    rendered_len = len(R.visible_text(result.html or ""))
    assert result.available is True
    assert raw_len < 100, "fixture should start as a near-empty shell"
    assert rendered_len > raw_len * 4, f"expected a large render gain, got {raw_len} -> {rendered_len}"
    assert "4,000 product teams" in R.visible_text(result.html)  # a fact only JS produces


@pytest.mark.slow
def test_server_rendered_page_shows_no_render_gain(config, server):
    """Control: a genuinely server-rendered page must NOT look client-rendered."""
    available, _ = R.probe_playwright()
    if not available:
        pytest.skip("chromium not provisioned")

    raw = SF.safe_get(f"{server}/healthy", config).body.decode()
    result = R.render_page(f"{server}/healthy", raw, config)
    raw_len = len(R.visible_text(raw))
    rendered_len = len(R.visible_text(result.html or ""))
    assert rendered_len <= raw_len * 1.2
    assert result.heuristic_signals["client_rendered_likely"] is False


def test_hydrating_fixture_flagged_by_tier_b_too(config, server):
    """Tier B must reach the same conclusion without a browser."""
    raw = SF.safe_get(f"{server}/spa-hydrating", config).body.decode()
    signals = R.detect_spa_signals(raw, config)
    assert signals["client_rendered_likely"] is True
    assert signals["empty_mount_node"] == "#root"
    assert "__NEXT_DATA__" in signals["framework_markers"]


def test_render_viewport_matches_check_threshold(config):
    """Guards a silent inconsistency: rendering 1280x800 while checks measure a different fold."""
    checks = json.loads((REPO_ROOT / "config" / "checks.json").read_text(encoding="utf-8"))
    fold = {c["threshold"]["first_viewport_px"] for c in checks["checks"]
            if c.get("threshold") and "first_viewport_px" in c["threshold"]}
    assert fold == {config["render"]["viewport_height"]}


# --- Blocked-page detection ---------------------------------------------------------------------
def test_consent_wall_detected():
    html = fixture_server.CONSENT_WALL
    assert A.detect_blocked_kind(html, 200) == "consent_wall"


def test_bot_challenge_detected():
    assert A.detect_blocked_kind(fixture_server.BOT_CHALLENGE, 200) == "bot_challenge"


def test_cookie_mention_on_real_page_is_not_a_wall():
    """Guards the worst false positive: a footer cookie notice must not blank a real page."""
    html = ("<html><body><main><h1>Real product</h1>"
            + "<p>Substantial article content here. </p>" * 80
            + "<footer>We use cookies</footer></main></body></html>")
    assert A.detect_blocked_kind(html, 200) is None


def test_403_treated_as_bot_challenge():
    assert A.detect_blocked_kind("", 403) == "bot_challenge"


# --- Language detection --------------------------------------------------------------------------
LANGUAGE_CASES = [
    ('<html lang="en">', "en"),
    ('<html lang="en-GB">', "en-gb"),
    ('<html lang="de">', "de"),
    ("<html>", None),
    # Unquoted values are valid HTML and common on minified pages. Requiring quotes discarded a
    # real declaration on smashingmagazine.com, which ships `<html lang=en>`.
    ("<html lang=en>", "en"),
    ("<html lang=en-GB>", "en-gb"),
    ("<html lang=en><head><title>x</title>", "en"),
    ('<html class="no-js" lang=de>', "de"),
    ("<html lang = fr dir=ltr>", "fr"),
    # Accepting unquoted values must not start accepting things that are not language tags.
    ('<html lang="">', None),
    ("<html lang=>", None),
    ("<html lang=e>", None),
    ("<html lang=english>", None),
    ("<html lang=en_US>", None),
]


@pytest.mark.parametrize("html,expected", LANGUAGE_CASES)
def test_language_detection(html, expected):
    assert A.detect_language(html)[0] == expected


@pytest.mark.parametrize("html,expected", LANGUAGE_CASES)
def test_every_copy_of_language_detection_agrees(tmp_path, html, expected):
    """Each skill carries its own copy, by design, so each must read a declaration the same way.

    Run through each skill's real offline entry point rather than comparing regex text, so a copy
    that is changed in how it is CALLED is caught as well as one changed in its pattern.
    """
    for sub in ("engagement-orientation-audit", "quotability-density-audit", "remediation-advisor"):
        path = str(REPO_ROOT / "skills" / sub / "scripts")
        if path not in sys.path:
            sys.path.insert(0, path)
    import advise as AD
    import engagement_orientation as EO
    import quotability_density as QD

    page = tmp_path / "page.html"
    page.write_text(html + "<body><p>Body.</p></body></html>", encoding="utf-8")
    detected = {name: module.artifact_from_html_file(str(page))["language"]["detected"]
                for name, module in (("engagement", EO), ("quotability", QD), ("advisor", AD))}
    assert detected == {"engagement": expected, "quotability": expected, "advisor": expected}, detected


def test_language_support_gate(config):
    assert A.language_supported("en", config) is True
    assert A.language_supported("en-GB", config) is True
    assert A.language_supported("de", config) is False
    assert A.language_supported(None, config) is False


# --- End-to-end artifact assembly -----------------------------------------------------------------
def test_artifact_validates_against_schema(config, server):
    artifact = A.build_artifact(f"{server}/healthy", config, force_tier=R.TIER_B)
    Draft202012Validator(ARTIFACT_SCHEMA).validate(artifact)


def test_artifact_records_homepage_and_language(config, server):
    artifact = A.build_artifact(f"{server}/healthy", config, force_tier=R.TIER_B)
    assert artifact["pages"][0]["role"] == "homepage"
    assert artifact["pages"][0]["status"] == "ok"
    assert artifact["language"]["detected"] == "en"
    assert artifact["language"]["supported"] is True


def test_artifact_multi_page_from_nav(config, server):
    artifact = A.build_artifact(f"{server}/", config, force_tier=R.TIER_B)
    assert len(artifact["pages"]) > 1
    assert artifact["pages"][0]["role"] == "homepage"


def test_artifact_detects_blocked_page(config, server):
    artifact = A.build_artifact(f"{server}/consent-wall", config, force_tier=R.TIER_B)
    assert artifact["pages"][0]["blocked_kind"] == "consent_wall"
    assert artifact["pages"][0]["status"] == "blocked"


def test_artifact_captures_meta_robots(config, server):
    artifact = A.build_artifact(f"{server}/noindex", config, force_tier=R.TIER_B)
    assert artifact["pages"][0]["meta_robots"] == "noindex"


def test_ai_crawler_block_visible_but_we_stay_allowed(config, server):
    """The signal the audit exists for: crawlable by us, invisible to ChatGPT.

    Uses a live robots.txt served over a real socket, not a mock, so the whole
    fetch -> parse -> evaluate path is exercised.
    """
    import urllib.robotparser as rp
    result = SF.safe_get(f"{server}/robots-block-ai", config)
    assert result.ok is True

    parser = rp.RobotFileParser()
    parser.parse(result.body.decode().splitlines())
    assert parser.can_fetch("GPTBot", f"{server}/") is False
    assert parser.can_fetch("ClaudeBot", f"{server}/") is False
    assert parser.can_fetch("CitelyAuditBot", f"{server}/") is True


def test_robots_blocking_us_halts_before_fetch(config, server):
    """Operational gate: when OUR UA is refused we must not fetch the page at all."""
    robots = SF.check_robots(f"{server}/", config)
    assert robots.audit_allowed is True  # baseline: this server allows us

    blocked = SF.RobotsResult(checked=True, status=200, audit_allowed=False)
    artifact = A.build_artifact(f"{server}/healthy", config, force_tier=R.TIER_B, robots=blocked)
    assert artifact["blocked_before_fetch"] is True
    assert artifact["pages"][0]["blocked_kind"] == "robots_disallowed"
    assert "raw" not in artifact["pages"][0]  # proof nothing was downloaded


def test_artifact_never_raises_on_dead_host(config):
    artifact = A.build_artifact("http://127.0.0.1:1/", config, force_tier=R.TIER_B)
    assert artifact["errors"]
    assert artifact["pages"][0]["status"] in ("error", "blocked")


def test_redirect_chain_followed_to_final_page(config, server):
    result = SF.safe_get(f"{server}/redirect-chain", config)
    assert result.ok is True
    assert len(result.redirect_chain) == 2


def test_redirect_loop_refused_against_live_server(config, server):
    result = SF.safe_get(f"{server}/redirect-loop", config)
    assert result.error_kind == "unsafe_url"
    assert "loop" in result.error


def test_compression_bomb_refused_against_live_server(config, server):
    """A real streamed bomb, not a mock — proves the cap fires on the wire."""
    cfg = {**config, "fetch": {**config["fetch"], "max_decompressed_bytes": 1_000_000}}
    result = SF.safe_get(f"{server}/bomb", cfg)
    assert result.error_kind == "too_large"


class SteppedClock:
    """A clock the test advances on purpose, instead of hoping the real one cooperates.

    Only `_artifact` sees this — it is installed as that module's `time` attribute, so
    `_safe_fetch` keeps the real clock and the fixture-server fetches get their full real budget.
    That separation is the point: the deadline must be live during acquisition and expired by the
    time the page loop runs, and nothing about real elapsed time can be relied on to arrange that.
    """

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return time.monotonic() + self.offset

    def sleep(self, seconds):
        time.sleep(seconds)


def test_deadline_marks_later_pages_skipped(config, server, monkeypatch):
    """Pages after the homepage are skipped once the global deadline has passed.

    This test used to pass `deadline=time.monotonic() + 0.001` and accept either a skipped page OR
    a single-page artifact. It failed roughly one run in six, and the cause was not CPU load:
    **`time.monotonic()` on Windows is `GetTickCount64()` with a resolution of 15.625 ms**, so a
    1 ms deadline is smaller than the clock can represent. Whether it had "expired" by the time the
    page loop ran depended entirely on whether a tick boundary happened to fall during the crawl —
    a coin toss, not a timing margin. The `or len(pages) == 1` escape hatch then hid the other half
    of the problem, because a run where acquisition itself died on the deadline also counted as a
    pass while proving nothing about skipping.

    The clock is now driven explicitly: acquisition runs with 30 real seconds of budget, and the
    deadline is pushed into the past the instant page selection returns. The loop check is
    therefore expired on every iteration, on every machine, regardless of speed or load.

    `timing.total_ms` in the resulting artifact is synthetic — it includes the jump — which is
    irrelevant here and asserted nowhere.
    """
    clock = SteppedClock()
    monkeypatch.setattr(A, "time", clock)

    real_select = A.PS.select_pages

    def select_then_expire_the_budget(*args, **kwargs):
        candidates = real_select(*args, **kwargs)
        clock.offset = 31.0          # strictly greater than the 30 s budget below
        return candidates

    monkeypatch.setattr(A.PS, "select_pages", select_then_expire_the_budget)

    artifact = A.build_artifact(f"{server}/", config, force_tier=R.TIER_B,
                                deadline=time.monotonic() + 30.0)

    pages = artifact["pages"]
    # No escape hatch: if the fixture server ever stops offering a second page, this test has
    # nothing to measure and must say so rather than passing silently.
    assert len(pages) >= 2, f"expected more than one candidate page, got {pages}"
    assert pages[0]["status"] == "ok", "acquisition must succeed; only the LATER pages are skipped"

    skipped = [p for p in pages[1:] if p["status"] == "skipped"]
    assert skipped, f"no page was skipped despite an expired budget: {pages}"
    assert all(p["skip_reason"] == "budget" for p in skipped)


def test_pages_are_not_skipped_while_the_budget_is_intact(config, server):
    """The control for the test above.

    Without it, a skip caused by something other than the deadline — a broken fixture page, a
    refused fetch — would still satisfy the assertions and the budget logic would go unmeasured.
    """
    artifact = A.build_artifact(f"{server}/", config, force_tier=R.TIER_B,
                                deadline=time.monotonic() + 300.0)
    assert len(artifact["pages"]) >= 2
    assert not [p for p in artifact["pages"] if p["status"] == "skipped"]


# --- Acquisition cleanup regressions -------------------------------------------------------------
# Each test below pins a fix from the 8-issue cleanup. They exist because the original content-type
# "fix" was inert and 211 green tests failed to notice — behaviour must be asserted, not assumed.

def test_non_html_page_is_refused(config, server):
    """Page content is restricted to text/html. Parsing JSON or PDF as HTML yields garbage findings."""
    artifact = A.build_artifact(f"{server}/not-html", config, force_tier=R.TIER_B)
    page = artifact["pages"][0]
    assert page["status"] == "error"
    assert page["skip_reason"] == "content_type"


def test_html_page_still_accepted(config, server):
    artifact = A.build_artifact(f"{server}/healthy", config, force_tier=R.TIER_B)
    assert artifact["pages"][0]["status"] == "ok"


def test_content_type_refused_before_body_download(config, server):
    result = SF.safe_get(f"{server}/not-html", config,
                         require_content_types=["text/html"])
    assert result.error_kind == "content_type"
    assert result.body == b""  # nothing was downloaded


def test_robots_and_sitemaps_are_not_html_restricted(config, server):
    """text/plain robots and XML sitemaps must still be fetchable."""
    assert SF.safe_get(f"{server}/robots.txt", config).ok is True
    assert SF.safe_get(f"{server}/sitemap.xml", config).ok is True


@pytest.mark.parametrize("declared,allowed,expected", [
    ("text/html", ["text/html"], True),
    ("text/html; charset=utf-8", ["text/html"], True),
    ("TEXT/HTML", ["text/html"], True),
    ("application/json", ["text/html"], False),
    (None, ["text/html"], False),
    ("anything", None, True),          # no allowlist => no restriction
])
def test_content_type_matching(declared, allowed, expected):
    assert SF.content_type_matches(declared, allowed) is expected


def test_deadline_defaults_from_config(config):
    """The 5-minute ceiling must come from config, not only from an explicit caller argument."""
    import time
    start = time.monotonic()
    deadline = SF.deadline_from_config(config, start)
    assert deadline == start + config["budgets"]["global_deadline_s"]


def test_build_artifact_applies_config_deadline(config, server):
    artifact = A.build_artifact(f"{server}/healthy", config, force_tier=R.TIER_B)
    assert artifact["timing"]["deadline_s"] is not None  # a budget was in force


def test_unencoded_body_capped_without_content_length(config, server):
    """Closes the gap where a chunked, unencoded body between the two caps slipped through."""
    class Resp:
        headers = {}  # no Content-Length, no Content-Encoding
        def iter_content(self, chunk_size=65536):
            for _ in range(50):
                yield b"A" * 8192
        def close(self):
            pass
    with pytest.raises(SF.ResponseTooLargeError, match="body exceeded cap"):
        SF.read_capped(Resp(), max_bytes=100_000, max_decompressed=10_000_000)


def test_artifact_declares_all_emitted_fields(config, server):
    """Guards schema drift: the analyzers depend on these keys being part of the contract."""
    artifact = A.build_artifact(f"{server}/healthy", config, force_tier=R.TIER_B)
    declared = set(ARTIFACT_SCHEMA["properties"])
    assert set(artifact) - declared == set()


def test_role_enum_matches_what_we_emit():
    """Roles are homepage/other only — semantic roles would reintroduce the lexical assumption."""
    roles = ARTIFACT_SCHEMA["properties"]["pages"]["items"]["properties"]["role"]["enum"]
    assert set(roles) == {"homepage", "other"}


def test_no_duplicate_timeout_config(config):
    """Timeouts live in fetch/render only. Duplicates in `budgets` were inert and drifted."""
    assert "per_request_timeout_s" not in config["budgets"]
    assert "render_timeout_s" not in config["budgets"]
    assert "read_timeout_s" in config["fetch"]
    assert "max_render_ms" in config["render"]


def test_no_dead_config_keys(config):
    assert "audit_user_agent_gate" not in config["robots"]
    assert "strategy" not in config["page_selection"]
    assert "default_when_undetected" not in config["language"]


# =================================================================================================
# Content encodings: decode what the stack supports, refuse the rest, never guess
#
# We advertise `Accept-Encoding: gzip, deflate`. Some servers answer in Brotli regardless: a CDN
# returned `br` to this auditor even when asked for `identity`. With no Brotli decoder, urllib3
# passed the compressed bytes through WITHOUT raising, so they arrived labelled `text/html`. Brotli
# is now a pinned dependency and urllib3 decodes it. What the stack still cannot undo is refused
# before the body is read, and a body that claims an encoding it does not contain is refused as
# malformed. Either refusal leaves the page unread, so its checks resolve to `unknown`.
# =================================================================================================
@pytest.mark.parametrize("route,title", [
    ("/gzipped-page", "Gzipped"),
    ("/deflate-page", "Deflated"),
    ("/brotli-page", "Brotli"),
    ("/layered-gzip-br", "Layered"),   # two encodings applied in order, undone in reverse
    ("/identity-page", "Identity"),    # explicitly "no encoding at all"
])
def test_each_supported_encoding_is_decoded_before_analysis(config, server, route, title):
    result = SF.safe_get(f"{server}{route}", config)
    assert result.error_kind is None, result.error
    body = result.body or b""
    assert body.startswith(b"<!DOCTYPE html>"), f"analyzers would be handed {body[:12]!r}"
    assert f"<title>{title}</title>".encode() in body


def test_brotli_is_decodable_with_the_pinned_dependency():
    """The regression for the live failure: `br` was refused because nothing here could undo it."""
    assert "br" in SF.decodable_encodings()
    assert SF.undecodable_encodings("br") == []


def test_the_pinned_brotli_bounds_what_one_call_can_inflate():
    """Why the pin is exact.

    urllib3 bounds Brotli output only through `output_buffer_limit`, which arrived in Brotli 1.2.0.
    With an older release it decodes without a limit and merely warns. The streaming cap would still
    stop the running total, but not how far a single chunk expands in memory before it is counted.

    The limit is a bound, not an exact size: Brotli rounds it up to its own internal block. So the
    assertion is that one limited call stays far below the whole payload, while an unlimited call
    produces all of it at once.
    """
    import brotli
    payload = brotli.compress(b"A" * 1_000_000)

    bounded = brotli.Decompressor()
    first = bounded.process(payload, output_buffer_limit=4096)
    assert 0 < len(first) < 100_000, "a limited call must not inflate the whole payload at once"
    assert not bounded.is_finished(), "the remainder must still be pending, not already produced"

    unbounded = brotli.Decompressor().process(payload)
    assert len(unbounded) == 1_000_000, "the control: with no limit it all arrives in one call"


def test_a_server_that_sends_brotli_unasked_is_now_read(config, server):
    """The live failure end to end: an unrequested `br` page becomes a readable page."""
    artifact = A.build_artifact(f"{server}/brotli-page", config, force_tier="heuristic")
    page = artifact["pages"][0]
    assert page["status"] == "ok", page.get("skip_reason")
    assert "<title>Brotli</title>" in page["raw"]["html"]


def test_an_unsupported_encoding_is_refused_before_the_body_is_read(config, server):
    result = SF.safe_get(f"{server}/compress-unrequested", config)
    assert result.error_kind == "content_encoding"
    assert "compress" in result.error
    assert not result.body, "undecodable bytes must never be stored as page HTML"


@pytest.mark.parametrize("route,encoding", [
    ("/brotli-malformed", "br"),
    ("/gzip-malformed", "gzip"),
])
def test_a_malformed_body_is_refused_with_a_clear_diagnostic(config, server, route, encoding):
    """A body that claims an encoding it does not contain.

    This used to surface as `error_kind="network"` carrying urllib3's message. Safe, since no bytes
    reached the analyzers, but it blamed the connection for a corrupt payload.
    """
    result = SF.safe_get(f"{server}{route}", config)
    assert result.error_kind == "content_encoding", result.error
    assert f"content-encoding {encoding}" in result.error
    assert "not valid" in result.error
    assert not result.body


def test_a_brotli_bomb_is_stopped_by_the_existing_cap(config, server):
    """Adding a decoder must not open a way around the decompression cap."""
    cfg = {**config, "fetch": {**config["fetch"], "max_decompressed_bytes": 1_000_000}}
    result = SF.safe_get(f"{server}/brotli-bomb", cfg)
    assert result.error_kind == "too_large"
    assert not result.body


@pytest.mark.parametrize("header", [None, "", "identity", "gzip", "deflate", "x-gzip", "br",
                                    "GZIP", "BR", " gzip ", "identity, gzip", "gzip, br"])
def test_supported_content_encodings_are_never_refused(header):
    assert SF.undecodable_encodings(header) == []


@pytest.mark.parametrize("header,stuck", [
    ("compress", ["compress"]),
    ("gzip, compress", ["compress"]),
    ("br, compress", ["compress"]),
    ("x-unknown", ["x-unknown"]),
])
def test_encodings_the_stack_cannot_undo_are_named(header, stuck):
    assert SF.undecodable_encodings(header) == stuck


def test_what_is_decodable_is_read_from_the_http_stack_not_hardcoded():
    """`br` becomes decodable the moment a Brotli package is installed.

    Hardcoding either answer would be wrong on half the machines this runs on, so the set comes
    from urllib3 itself. If that is ever replaced by a literal, this test says so.
    """
    from urllib3.response import HTTPResponse
    decodable = SF.decodable_encodings()
    for name in HTTPResponse.CONTENT_DECODERS:
        assert str(name).lower() in decodable
    assert "identity" in decodable and "" in decodable
    if "br" in {str(n).lower() for n in HTTPResponse.CONTENT_DECODERS}:
        assert SF.undecodable_encodings("br") == []
    else:
        assert SF.undecodable_encodings("br") == ["br"]


@pytest.mark.parametrize("route", ["/compress-unrequested", "/brotli-malformed"])
def test_a_page_that_cannot_be_decoded_degrades_to_unknown_not_to_failure(config, server, route):
    """The scoring consequence: an unreadable page is not a badly-built page.

    It must land as `fetch_failed` with the checks unmeasured, never as a page that was read and
    found wanting.
    """
    artifact = A.build_artifact(f"{server}{route}", config, force_tier="heuristic")
    page = artifact["pages"][0]
    assert page["status"] == "error"
    assert page["skip_reason"] == "fetch_failed"
    assert page["raw"]["html"] == ""
