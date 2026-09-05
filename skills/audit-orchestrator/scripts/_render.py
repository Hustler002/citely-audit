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
    error: str | None = None


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


def render_with_browser(url: str, config: dict, *, deadline: float | None = None) -> RenderResult:
    """Tier A render. Any failure degrades to a recorded error, never an exception."""
    render_cfg = config.get("render", {})
    width = int(render_cfg.get("viewport_width", 1280))
    height = int(render_cfg.get("viewport_height", 800))
    wait_until = render_cfg.get("wait_until", "networkidle")
    settle_ms = int(render_cfg.get("settle_ms", 1200))
    max_render_ms = int(render_cfg.get("max_render_ms", 20000))
    capture_console = bool(render_cfg.get("capture_console_errors", True))
    max_console = int(render_cfg.get("max_console_errors", 25))

    if deadline is not None:
        remaining_ms = int(max(0, (deadline - time.monotonic()) * 1000))
        if remaining_ms <= 0:
            return RenderResult(available=False, mode=TIER_B, error="deadline exceeded before render")
        max_render_ms = min(max_render_ms, remaining_ms)

    result = RenderResult(mode=TIER_A, viewport={"width": width, "height": height})
    started = time.monotonic()

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-dev-shm-usage"])
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    user_agent=config.get("fetch", {}).get("user_agent"),
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

                page.goto(url, wait_until=wait_until, timeout=max_render_ms)
                page.wait_for_timeout(min(settle_ms, max_render_ms))
                result.html = page.content()
                result.console_errors = errors
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
