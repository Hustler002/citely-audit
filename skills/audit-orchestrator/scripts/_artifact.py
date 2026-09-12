#!/usr/bin/env python3
"""Crawl-artifact assembly — the single cross-boundary contract.

This is the last stop on the network side of the pipeline. It combines robots evaluation, page
selection, fetching and rendering into one JSON document that validates against
`references/crawl-artifact-schema.json`.

Everything downstream (all four analyzers) is a pure function of this artifact and performs ZERO
network I/O. That is what makes an audit replayable: save the artifact, re-run the analysis, and
the report is byte-identical.

Never raises. Every failure becomes an entry in `errors[]` or a page `status`, because an audit
that crashes tells the user nothing while an audit that degrades still reports what it learned.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

import _page_select as PS
import _render as R
import _safe_fetch as SF

log = logging.getLogger("artifact")

BLOCK_MARKERS = {
    "consent_wall": ["cookie consent", "we use cookies", "accept all cookies", "gdpr",
                     "manage preferences", "cookiebot", "onetrust", "consent-manager"],
    "bot_challenge": ["checking your browser", "cf-browser-verification", "just a moment",
                      "ddos protection", "enable javascript and cookies to continue",
                      "verifying you are human"],
    "captcha": ["recaptcha", "hcaptcha", "captcha-delivery", "g-recaptcha"],
    "login_wall": ["please sign in to continue", "log in to view", "members only",
                   "sign in to read"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_language(html: str) -> tuple:
    """Language from the html lang attribute only.

    Deliberately conservative: we report what the page DECLARES rather than guessing from content.
    A wrong guess would wrongly enable language-dependent checks and produce exactly the false
    positives the i18n gate exists to prevent. Undetected simply means those checks stay `unknown`.
    """
    import re
    if not html:
        return None, None
    m = re.search(r"<html[^>]*\blang\s*=\s*[\"']([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)[\"']", html[:4000], re.I)
    if m:
        return m.group(1).lower(), "html_lang"
    return None, None


def language_supported(tag: str | None, config: dict) -> bool:
    if not tag:
        return False
    supported = [s.lower() for s in config.get("language", {}).get("supported", ["en"])]
    primary = tag.split("-")[0].lower()
    return primary in supported


def detect_blocked_kind(html: str, status: int | None) -> str | None:
    """Identify interstitials that stand in for real content.

    Without this, a consent wall or bot challenge is indistinguishable from a genuinely empty page,
    and the audit would confidently report 'your content is bad' about a page it never actually saw.
    That is the worst false positive available, and it will happen on unseen sites.
    """
    if status in (401, 403):
        return "login_wall" if status == 401 else "bot_challenge"
    if not html:
        return None
    low = html.lower()
    text_len = len(R.visible_text(html))
    for kind, markers in BLOCK_MARKERS.items():
        if any(m in low for m in markers):
            # Markers alone are not enough: real pages mention cookies in a footer banner while
            # still serving content. Only call it a wall when there is little else on the page.
            if text_len < 1500 or kind in ("bot_challenge", "captcha"):
                return kind
    return None


def _page_entry(candidate, fetch_result, render_result, config) -> dict:
    """Build one `pages[]` entry from a fetch + render pair."""
    entry = {"url": candidate.url, "role": candidate.role, "status": "ok",
             "skip_reason": None, "blocked_kind": None}

    if fetch_result is None:
        entry.update(status="skipped", skip_reason="budget")
        return entry

    if fetch_result.error:
        # Distinguish "we could not reach it" from "it is not an HTML page" — the latter is a
        # legitimate not-auditable outcome, not a transport failure.
        kind = "content_type" if fetch_result.error_kind == "content_type" else "fetch_failed"
        entry.update(status="error", skip_reason=kind)
        entry["raw"] = {"status": fetch_result.status, "content_type": fetch_result.content_type,
                        "byte_size": fetch_result.byte_size, "html_sha256": "", "html": ""}
        return entry

    html = fetch_result.body.decode("utf-8", errors="replace") if fetch_result.body else ""
    entry["raw"] = {
        "status": fetch_result.status,
        "content_type": fetch_result.content_type,
        "byte_size": fetch_result.byte_size,
        "html_sha256": hashlib.sha256(fetch_result.body or b"").hexdigest(),
        "html": html,
        "headers": fetch_result.headers,
    }

    blocked = detect_blocked_kind(html, fetch_result.status)
    if blocked:
        entry.update(status="blocked", blocked_kind=blocked, skip_reason="blocked")

    entry["meta_robots"] = _meta_robots(html)
    entry["x_robots_tag"] = (fetch_result.headers or {}).get("X-Robots-Tag")

    if render_result is not None:
        entry["rendered"] = {
            "available": render_result.available,
            "mode": render_result.mode,
            "html": render_result.html,
            "render_ms": render_result.render_ms,
            "console_errors": render_result.console_errors,
            "viewport": render_result.viewport,
            "heuristic_signals": render_result.heuristic_signals,
            "geometry": render_result.geometry,
            "nav_state": render_result.nav_state,
            "wait_strategy": render_result.wait_strategy,
        }
    return entry


def _meta_robots(html: str) -> str | None:
    import re
    if not html:
        return None
    m = re.search(
        r"<meta[^>]*\bname\s*=\s*[\"']robots[\"'][^>]*\bcontent\s*=\s*[\"']([^\"']*)[\"']",
        html[:20000], re.I)
    return m.group(1).strip().lower() if m else None


def build_artifact(url: str, config: dict, *, deadline: float | None = None,
                   force_tier: str | None = None, fetch=None, robots=None) -> dict:
    """Fetch, select, render — then emit the artifact. Never raises.

    `fetch` and `robots` are injectable so the whole pipeline can be exercised offline in tests.
    """
    started = time.monotonic()
    # The 5-minute ceiling is enforced from ONE configured value. Previously a caller that passed
    # no deadline got no budget at all, silently disabling the headline performance constraint.
    if deadline is None:
        deadline = SF.deadline_from_config(config, started)

    # Two fetchers with DIFFERENT content-type rules:
    #   fetch_page — page content, restricted to HTML. A PDF/JSON/image parsed as HTML yields
    #                garbage findings, so it is refused before the body is even downloaded.
    #   fetch_aux  — robots.txt and sitemaps, which legitimately declare text/plain and XML.
    # An injected `fetch` (tests) overrides both, so offline suites stay in full control.
    html_types = config.get("fetch", {}).get("html_content_types")
    injected = fetch

    def fetch_page(u):
        if injected is not None:
            return injected(u)
        return SF.safe_get(u, config, deadline=deadline, require_content_types=html_types)

    def fetch_aux(u):
        if injected is not None:
            return injected(u)
        return SF.safe_get(u, config, deadline=deadline)

    fetch = fetch_aux          # robots / sitemap discovery
    errors = []

    artifact = {
        "requested_url": SF.redact_url(url),
        "final_url": SF.redact_url(url),
        "fetched_at": utc_now(),
        "user_agent": SF.user_agent(config),
        "robots": {"checked": False, "allowed": True, "crawl_delay": None,
                   "status": None, "sitemaps": []},
        "language": {"detected": None, "source": None, "supported": False},
        "pages": [],
        "external_corroboration": None,
        "timing": {"fetch_ms": 0, "render_ms": 0, "total_ms": 0},
        "errors": errors,
    }

    # --- robots: operational gate first ---------------------------------------------------------
    robots_result = robots if robots is not None else SF.check_robots(url, config, deadline=deadline)
    artifact["robots"] = {
        "checked": robots_result.checked,
        "allowed": robots_result.audit_allowed,
        "crawl_delay": robots_result.crawl_delay,
        "status": robots_result.status,
        "sitemaps": list(robots_result.sitemaps or []),
    }
    artifact["ai_crawlers"] = {
        "determinable": robots_result.ai_crawlers_determinable,
        "allowed": dict(robots_result.ai_crawlers),
        "blocked": robots_result.blocked_ai_crawlers,
    }

    if not robots_result.audit_allowed:
        # Do not fetch at all. One root cause, attributed everywhere downstream.
        # Report the REAL cause: an unreachable robots.txt is a fetch failure, not the site
        # refusing us. Labelling both "robots_disallowed" tells the user a site blocks crawlers
        # when we merely failed to reach it.
        if getattr(robots_result, "unreachable", False):
            errors.append({"stage": "fetch", "type": "robots_unreachable",
                           "message": robots_result.error or "robots.txt could not be fetched"})
            artifact["pages"] = [{"url": SF.redact_url(url), "role": "homepage", "status": "error",
                                  "blocked_kind": None, "skip_reason": "fetch_failed"}]
        else:
            errors.append({"stage": "robots", "type": "blocked",
                           "message": "robots.txt disallows the audit user-agent"})
            artifact["pages"] = [{"url": SF.redact_url(url), "role": "homepage", "status": "blocked",
                                  "blocked_kind": "robots_disallowed", "skip_reason": "blocked"}]
        artifact["timing"]["total_ms"] = int((time.monotonic() - started) * 1000)
        artifact["blocked_before_fetch"] = True
        return artifact

    artifact["blocked_before_fetch"] = False

    # --- homepage -------------------------------------------------------------------------------
    fetch_started = time.monotonic()
    home = fetch_page(url)
    fetch_ms = int((time.monotonic() - fetch_started) * 1000)

    if home.error:
        errors.append({"stage": "fetch", "type": home.error_kind or "error", "message": home.error})
        artifact["pages"] = [_page_entry(
            PS.PageCandidate(url=SF.redact_url(url), role="homepage", source="homepage"), home, None, config)]
        artifact["timing"].update(fetch_ms=fetch_ms,
                                  total_ms=int((time.monotonic() - started) * 1000))
        return artifact

    artifact["final_url"] = home.final_url or SF.redact_url(url)
    home_html = home.body.decode("utf-8", errors="replace") if home.body else ""

    lang, source = detect_language(home_html)
    artifact["language"] = {"detected": lang, "source": source,
                            "supported": language_supported(lang, config)}

    # --- page selection (structural, language-neutral) -------------------------------------------
    try:
        candidates = PS.select_pages(url, home_html, config, fetch=fetch,
                                     robots_sitemaps=artifact["robots"]["sitemaps"])
    except Exception as exc:
        errors.append({"stage": "page_select", "type": "error", "message": str(exc)})
        candidates = [PS.PageCandidate(url=url, role="homepage", source="homepage")]

    # --- fetch + render each page ---------------------------------------------------------------
    crawl_delay = artifact["robots"]["crawl_delay"] or 0
    total_render_ms = 0
    pages = []

    for index, candidate in enumerate(candidates):
        over_budget = deadline is not None and time.monotonic() >= deadline
        if over_budget and index > 0:
            pages.append({"url": candidate.url, "role": candidate.role,
                          "status": "skipped", "skip_reason": "budget", "blocked_kind": None})
            continue

        if index == 0:
            page_fetch = home
        else:
            if crawl_delay:
                time.sleep(min(float(crawl_delay), 5.0))
            page_fetch = fetch_page(candidate.url)

        render_result = None
        if page_fetch and not page_fetch.error:
            page_html = page_fetch.body.decode("utf-8", errors="replace") if page_fetch.body else ""
            render_result = R.render_page(candidate.url, page_html, config,
                                          deadline=deadline, force_tier=force_tier)
            total_render_ms += render_result.render_ms or 0
            if render_result.error and not render_result.available:
                errors.append({"stage": "render", "type": "degraded",
                               "message": f"{candidate.url}: {render_result.error}"})

        pages.append(_page_entry(candidate, page_fetch, render_result, config))

    artifact["pages"] = pages
    artifact["timing"] = {
        "fetch_ms": fetch_ms,
        "render_ms": total_render_ms,
        "total_ms": int((time.monotonic() - started) * 1000),
        "deadline_s": None if deadline is None else round(max(0.0, deadline - time.monotonic()), 2),
    }
    return artifact
