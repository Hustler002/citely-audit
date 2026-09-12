#!/usr/bin/env python3
"""Tiered rendering — the raw-vs-rendered comparison that exposes client-side-only content.

Tier A: a real Chromium render via Playwright. Produces the post-JavaScript DOM, so we can measure
        exactly what a JS-executing crawler sees versus what a plain fetch returns.
Tier B: a browserless heuristic used when no browser is available. Infers client-side rendering
        from the raw HTML alone — empty mount node, framework markers, script-to-text ratio.

Tier B exists so the audit always completes and always says something useful, including in
egress-restricted or browser-less environments. Findings derived from it are labelled `heuristic`
and `diagnostics.render_mode` discloses which tier ran, so a reader is never misled about how a
conclusion was reached.

Chromium is provisioned as a SETUP step (`playwright install chromium`), never downloaded inside a
timed audit run — a 150 MB download would blow the 5-minute budget on its own.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import _safe_fetch

log = logging.getLogger("render")

TIER_A = "playwright"
TIER_B = "heuristic"

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WS_RE = re.compile(r"\s+")


@dataclass
class RenderResult:
    available: bool = False
    mode: str = TIER_B
    html: str | None = None
    render_ms: int | None = None
    console_errors: list = field(default_factory=list)
    viewport: dict = field(default_factory=dict)
    heuristic_signals: dict = field(default_factory=dict)
    geometry: dict = field(default_factory=dict)
    error: str | None = None
    # How the render actually went, disclosed rather than hidden behind `available`.
    #   ok          — the navigation milestone fired
    #   busy        — milestone fired, but the page never went quiet (beacons, sockets, polling)
    #   salvaged    — the milestone timed out and the DOM was harvested anyway
    nav_state: str | None = None
    wait_strategy: str | None = None


# --- Navigation policy ---------------------------------------------------------------------------
# `networkidle` waits for 500 ms with at most two connections in flight. Analytics beacons, chat
# widgets, long-polling and video players hold a modern page above that line indefinitely, so the
# condition never fires and the entire render budget is spent waiting for it. python.org burned the
# full 20 s this way and dropped to the browserless heuristic — while the page had been sitting
# complete for 1.2 s. Measured on python.org: `load` 1.2 s, `domcontentloaded` 3.9 s,
# `networkidle` 20.0 s and a timeout.
#
# So the milestone we WAIT on is one that actually fires, quiescence is a bounded bonus, and — the
# part that generalises past this one cause — a navigation timeout SALVAGES the DOM instead of
# discarding it. A timeout means "the milestone did not fire", not "there is no page": the same
# python.org run that raised held a complete 68 KB document.
NAV_MILESTONES = ("commit", "domcontentloaded", "load", "networkidle")
DEFAULT_WAIT_UNTIL = "load"

# Below this, `page.content()` is an empty shell (`about:blank`) rather than a page worth keeping.
SALVAGE_FLOOR_CHARS = 200


# Layout facts HTML alone cannot answer: what is genuinely above the fold, how large body text
# actually renders, and whether an overlay covers first-paint content. Collected only in Tier A —
# without it the engagement analyzer falls back to a DOM-order proxy and labels itself heuristic.
GEOMETRY_JS = """
() => {
  const out = {viewport_height: window.innerHeight, viewport_width: window.innerWidth,
               body_font_px: null, headings: [], interactive: [], overlays: [],
               above_fold_text_chars: 0, above_fold_text: ''};
  const px = v => { const n = parseFloat(v); return isFinite(n) ? n : null; };
  const topOf = el => {
    try { const r = el.getBoundingClientRect(); return Math.round(r.top + window.scrollY); }
    catch (e) { return null; }
  };
  try { out.body_font_px = px(getComputedStyle(document.body).fontSize); } catch (e) {}

  const take = (selector, bucket, limit) => {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(selector)).slice(0, limit); } catch (e) { return; }
    for (const el of nodes) {
      let text = '';
      try { text = (el.innerText || el.textContent || '').trim().slice(0, 160); } catch (e) {}
      // A logo headline — <h1><img alt="Acme"></h1> — has no text node, but it is exactly what a
      // visitor sees on arrival. Fall back to the accessible name, which is also what the Tier-B
      // proxy reads, so the two tiers cannot disagree about the same page.
      if (!text) {
        try {
          const label = el.getAttribute('aria-label');
          const img = el.querySelector('img[alt], [aria-label]');
          text = ((label || (img && (img.getAttribute('alt') || img.getAttribute('aria-label'))) || '')
                  + '').trim().slice(0, 160);
        } catch (e) {}
      }
      let fs = null;
      try { fs = px(getComputedStyle(el).fontSize); } catch (e) {}
      out[bucket].push({tag: el.tagName.toLowerCase(), top: topOf(el), text: text, font_px: fs});
    }
  };
  take('h1,h2,h3', 'headings', 40);
  take('a[href],button,input[type=submit]', 'interactive', 120);

  // Overlays: fixed/sticky elements blanketing the first screen (consent walls, modals).
  try {
    for (const el of Array.from(document.body.querySelectorAll('*')).slice(0, 3000)) {
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
      if (cs.display === 'none' || cs.visibility === 'hidden' || px(cs.opacity) === 0) continue;
      const r = el.getBoundingClientRect();
      const cover = (r.width * r.height) / (window.innerWidth * window.innerHeight);
      if (cover > 0.4) {
        out.overlays.push({tag: el.tagName.toLowerCase(),
                           coverage: Math.round(cover * 100) / 100, top: Math.round(r.top)});
        if (out.overlays.length >= 5) break;
      }
    }
  } catch (e) {}

  // Text that actually renders within the first viewport.
  //
  // A TreeWalker over SHOW_TEXT returns the CONTENTS of <script> and <style> too, and those
  // elements report top = 0, so inline JavaScript was being counted as the first thing a visitor
  // reads. On dev.to that was 3,301 of 6,214 reported characters, and the value-proposition check
  // was handed `if (navigator.userAgent === 'ForemWebView/1')` in place of the page's own hero
  // copy. Elements with no layout box report top = 0 for the same reason, so a hidden dropdown
  // also read as above-the-fold content.
  try {
    const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|HEAD|TITLE|META|LINK)$/;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let total = 0, seen = 0;
    const parts = [];
    while (walker.nextNode() && seen < 4000) {
      seen++;
      const node = walker.currentNode;
      const value = (node.nodeValue || '').trim();
      if (!value) continue;
      const parent = node.parentElement;
      if (!parent || SKIP.test(parent.tagName)) continue;
      let rect = null;
      try { rect = parent.getBoundingClientRect(); } catch (e) { continue; }
      if (!rect || (rect.width === 0 && rect.height === 0)) continue;   // not laid out
      const t = Math.round(rect.top + window.scrollY);
      if (t < window.innerHeight) {
        total += value.length;
        if (parts.length < 200) parts.push(value);
      }
    }
    out.above_fold_text_chars = total;
    // Capped sample so the value-proposition check can read what a visitor actually sees first.
    out.above_fold_text = parts.join(' ').slice(0, 2000);
  } catch (e) {}

  return out;
}
"""


# --- Text extraction --------------------------------------------------------------------------
def visible_text(html: str) -> str:
    """Approximate the text a reader (or a text-only crawler) actually sees.

    Regex-based on purpose: this runs on hostile input, and a full parse here would duplicate work
    the analyzers do later. Patterns are static and anchored, never built from page content, so
    they cannot be turned into a ReDoS vector.
    """
    if not html:
        return ""
    stripped = _COMMENT_RE.sub(" ", html)
    stripped = _SCRIPT_RE.sub(" ", stripped)
    stripped = _STYLE_RE.sub(" ", stripped)
    stripped = _TAG_RE.sub(" ", stripped)
    return _WS_RE.sub(" ", stripped).strip()


def script_bytes(html: str) -> int:
    return sum(len(m.group(0)) for m in _SCRIPT_RE.finditer(html or ""))


# --- Tier B: browserless SPA heuristic ---------------------------------------------------------
def detect_spa_signals(raw_html: str, config: dict) -> dict:
    """Positive evidence that the page builds itself in the browser.

    Every signal is evidence FOR client-side rendering. Absence of signals is not evidence against
    anything — it simply means we learned nothing, which the caller reports as lower confidence
    rather than as a pass.
    """
    render_cfg = config.get("render", {})
    mounts = render_cfg.get("spa_mount_selectors", [])
    markers = render_cfg.get("spa_framework_markers", [])
    ratio_threshold = float(render_cfg.get("spa_script_to_text_ratio", 3.0))

    html = raw_html or ""
    text = visible_text(html)
    text_len = len(text)
    script_len = script_bytes(html)

    found_markers = [m for m in markers if m in html]

    # An empty mount node is the strongest single signal: the container exists but holds nothing.
    empty_mount = None
    for selector in mounts:
        ident = selector.strip("#[]").split("=")[0]
        if not ident:
            continue
        pattern = re.compile(
            r"<(\w+)[^>]*\b(?:id|data-\w+)\s*=\s*[\"']?" + re.escape(ident) + r"[\"']?[^>]*>(\s*)</\1>",
            re.IGNORECASE,
        )
        if pattern.search(html):
            empty_mount = selector
            break
        if re.search(r"\bid\s*=\s*[\"']" + re.escape(ident) + r"[\"']", html, re.IGNORECASE):
            empty_mount = empty_mount or None

    ratio = (script_len / text_len) if text_len > 0 else (float("inf") if script_len else 0.0)

    signals = {
        "visible_text_chars": text_len,
        "script_bytes": script_len,
        "script_to_text_ratio": round(ratio, 2) if ratio != float("inf") else None,
        "script_to_text_exceeds_threshold": ratio > ratio_threshold,
        "framework_markers": found_markers,
        "empty_mount_node": empty_mount,
        "has_noscript_warning": "<noscript" in html.lower(),
    }
    signals["client_rendered_likely"] = bool(
        empty_mount or found_markers or (signals["script_to_text_exceeds_threshold"] and text_len < 500)
    )
    return signals


# --- Tier A: real browser ----------------------------------------------------------------------
def probe_playwright() -> tuple:
    """Return (available, reason). Never raises — absence is an expected, supported condition."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return False, f"playwright not installed: {exc}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True, ""
    except Exception as exc:
        return False, f"chromium unavailable: {exc}"


def is_nav_timeout(exc: Exception) -> bool:
    """True when navigation ran out of time, as opposed to genuinely failing.

    The distinction decides whether the DOM is worth salvaging. A timeout leaves a loaded page
    behind; `net::ERR_NAME_NOT_RESOLVED` or a refused connection does not.
    """
    if type(exc).__name__ == "TimeoutError":
        return True
    message = str(exc).lower()
    return "timeout" in message and "exceeded" in message


def is_blank_document(html: str | None) -> bool:
    """True for the empty shell Chromium reports when navigation never produced a document."""
    if not html or len(html) < SALVAGE_FLOOR_CHARS:
        return True
    return not visible_text(html) and "<body" not in html.lower()


def resolve_wait_until(render_cfg: dict) -> str:
    """Pick the navigation milestone, refusing the one that does not fire.

    `networkidle` is still accepted from config for an operator who explicitly wants it, but it is
    no longer the default and the downgrade is logged rather than silent.
    """
    configured = render_cfg.get("wait_until", DEFAULT_WAIT_UNTIL)
    if configured not in NAV_MILESTONES:
        log.warning("unknown wait_until %r; using %r", configured, DEFAULT_WAIT_UNTIL)
        return DEFAULT_WAIT_UNTIL
    return configured


HARVEST_ATTEMPTS = 3
HARVEST_BACKOFF_MS = 250


def harvest_dom(page, budget_left_ms, attempts: int = HARVEST_ATTEMPTS) -> str:
    """Read the DOM, retrying while the page is still swapping documents.

    `page.content()` raises if it is called during a navigation. That is a transient state, not a
    failure of the page, so retrying inside the remaining budget is the difference between a
    salvaged render and a needless drop to Tier B.
    """
    last = None
    for attempt in range(max(1, attempts)):
        try:
            return page.content()
        except Exception as exc:
            last = exc
            if attempt == attempts - 1 or budget_left_ms() <= 0:
                break
            page.wait_for_timeout(min(HARVEST_BACKOFF_MS, budget_left_ms()))
    raise last if last is not None else RuntimeError("could not read the rendered DOM")


def drive_page(page, url: str, *, wait_until: str, nav_timeout_ms: int, quiet_ms: int,
               settle_ms: int, budget_left_ms) -> tuple:
    """Navigate `page` and harvest its DOM. Returns `(html, nav_state)`.

    Split out from the browser plumbing so the policy that matters — which milestone we wait on,
    what we do when it does not fire — can be tested against a stub instead of only against a live
    site, where the interesting cases are whatever the network happens to be doing that day.
    """
    nav_state = "ok"
    try:
        page.goto(url, wait_until=wait_until, timeout=nav_timeout_ms)
    except Exception as exc:
        if not is_nav_timeout(exc):
            raise
        # Not fatal: the milestone did not fire, but the document usually has.
        nav_state = "salvaged"
        log.info("%s: %r did not fire within %dms; salvaging the DOM as rendered",
                 url, wait_until, nav_timeout_ms)
        # A timeout can land mid-navigation, where `content()` raises "the page is navigating and
        # changing the content" and the salvage is worth nothing. Reaching `domcontentloaded` first
        # gives the document a harvestable state to be read from. Found by forcing a short
        # navigation timeout against a live site; a stub cannot reproduce it.
        settle_budget = min(nav_timeout_ms, budget_left_ms())
        if settle_budget > 0:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=settle_budget)
            except Exception:
                pass

    # Quiescence is a bonus, never a gate. A page that never goes quiet is the normal case on the
    # modern web, not a failure, so this budget is small and its expiry is recorded, not raised.
    quiet_budget = min(quiet_ms, budget_left_ms())
    if quiet_budget > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=quiet_budget)
        except Exception:
            if nav_state == "ok":
                nav_state = "busy"

    page.wait_for_timeout(max(0, min(settle_ms, budget_left_ms())))
    html = harvest_dom(page, budget_left_ms)
    if is_blank_document(html):
        raise RuntimeError(f"navigation produced no document (nav_state={nav_state})")
    return html, nav_state


def render_with_browser(url: str, config: dict, *, deadline: float | None = None) -> RenderResult:
    """Tier A render. Any failure degrades to a recorded error, never an exception."""
    render_cfg = config.get("render", {})
    width = int(render_cfg.get("viewport_width", 1280))
    height = int(render_cfg.get("viewport_height", 800))
    wait_until = resolve_wait_until(render_cfg)
    settle_ms = int(render_cfg.get("settle_ms", 1200))
    quiet_ms = int(render_cfg.get("network_quiet_ms", 3000))
    nav_timeout_ms = int(render_cfg.get("nav_timeout_ms", 15000))
    max_render_ms = int(render_cfg.get("max_render_ms", 20000))
    capture_console = bool(render_cfg.get("capture_console_errors", True))
    max_console = int(render_cfg.get("max_console_errors", 25))

    if deadline is not None:
        remaining_ms = int(max(0, (deadline - time.monotonic()) * 1000))
        if remaining_ms <= 0:
            return RenderResult(available=False, mode=TIER_B, error="deadline exceeded before render")
        max_render_ms = min(max_render_ms, remaining_ms)

    # Navigation may not outlive the whole render budget, and Playwright reads `timeout=0` as
    # "wait forever" — the one value that must never reach it.
    nav_timeout_ms = min(nav_timeout_ms, max_render_ms)
    if nav_timeout_ms <= 0:
        return RenderResult(available=False, mode=TIER_B, wait_strategy=wait_until,
                            error="no render budget left for navigation")

    result = RenderResult(mode=TIER_A, viewport={"width": width, "height": height},
                          wait_strategy=wait_until)
    started = time.monotonic()

    def budget_left_ms() -> int:
        return int(max(0, max_render_ms - (time.monotonic() - started) * 1000))

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-dev-shm-usage"])
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    user_agent=_safe_fetch.user_agent(config),
                )
                page = context.new_page()

                errors = []
                if capture_console:
                    def on_console(msg):
                        if msg.type == "error" and len(errors) < max_console:
                            errors.append(str(msg.text)[:500])
                    page.on("console", on_console)
                    page.on("pageerror", lambda e: errors.append(str(e)[:500])
                            if len(errors) < max_console else None)

                html, nav_state = drive_page(
                    page, url, wait_until=wait_until, nav_timeout_ms=nav_timeout_ms,
                    quiet_ms=quiet_ms, settle_ms=settle_ms, budget_left_ms=budget_left_ms)
                result.html = html
                result.nav_state = nav_state
                result.console_errors = errors

                # Geometry is best-effort: a page that blocks evaluation must not fail the render.
                try:
                    result.geometry = page.evaluate(GEOMETRY_JS) or {}
                except Exception as exc:
                    log.warning("geometry capture failed for %s: %s", url, exc)
                    result.geometry = {}

                result.available = True
            finally:
                # Hard close so a hung page cannot leak a browser process past the audit.
                browser.close()
    except Exception as exc:
        result.available = False
        result.mode = TIER_B
        result.error = f"render failed: {exc}"
        log.warning("Tier-A render failed for %s: %s", url, exc)
    finally:
        result.render_ms = int((time.monotonic() - started) * 1000)

    return result


# --- Entry point -------------------------------------------------------------------------------
def render_page(url: str, raw_html: str, config: dict, *,
                deadline: float | None = None, force_tier: str | None = None) -> RenderResult:
    """Render `url`, falling back to the browserless heuristic when Tier A is unavailable.

    `force_tier` exists for tests, so the Tier-B path can be exercised deterministically on a
    machine that does have a browser installed.
    """
    signals = detect_spa_signals(raw_html, config)

    if force_tier == TIER_B:
        return RenderResult(available=False, mode=TIER_B, heuristic_signals=signals,
                            error="tier B forced")

    available, reason = (True, "") if force_tier == TIER_A else probe_playwright()
    if not available:
        log.info("falling back to Tier B: %s", reason)
        return RenderResult(available=False, mode=TIER_B, heuristic_signals=signals, error=reason)

    result = render_with_browser(url, config, deadline=deadline)
    result.heuristic_signals = signals
    if not result.available:
        result.mode = TIER_B  # render attempted but failed; be honest about what we actually have
    return result
