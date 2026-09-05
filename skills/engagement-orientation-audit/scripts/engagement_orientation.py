#!/usr/bin/env python3
"""engagement-orientation-audit (mechanic 5) — can a human who arrived from an AI answer orient themselves?

Pure consumer of the crawl artifact. ZERO network access, no exceptions.

Usage:
  python engagement_orientation.py --artifact crawl_artifact.json [--config config/checks.json]
  python engagement_orientation.py --html-file page.html          [--config config/checks.json]

Output: a JSON array of CHECK STATES on stdout (see references/check-result-schema.json).

Two measurement tiers, mirroring the render strategy:
  * Tier A — real geometry captured during the Chromium render (pixel offsets, computed font size,
    overlay coverage). Verdicts reflect what a visitor actually sees.
  * Tier B — no browser ran, so we fall back to a DOM-ORDER PROXY: an element appearing within the
    first N characters of <body> is treated as above the fold. Cruder, so those verdicts are
    reported with `reason` noting the proxy, and legibility (which needs a computed font size)
    resolves to `unknown` rather than guessing from inline styles.

Detection is structural, not lexical: a CTA is an anchor/button in the first viewport, NOT a word
like "Sign up". Only the value-proposition check is language-dependent, and it is gated on a
supported declared language — otherwise `unknown`, never `fail`.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("engagement-orientation-audit")

MAX_EVIDENCE = 300
_WS_RE = re.compile(r"\s+")
_LANG_ATTR_RE = re.compile(
    r"<html[^>]*\blang\s*=\s*[\"']([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)[\"']", re.IGNORECASE)

DEFAULT_THRESHOLDS = {
    "orientation.heading_first_viewport": {"first_viewport_px": 800, "dom_proxy_chars": 2500},
    "orientation.primary_cta": {"first_viewport_px": 800, "dom_proxy_chars": 2500},
    "orientation.content_not_obstructed": {"max_overlay_coverage": 0.4},
    "orientation.viewport_meta": {},
    "orientation.value_proposition": {"first_viewport_px": 800, "min_signal_score": 1},
    "orientation.legibility": {"min_body_font_px": 14},
}

CHECKS = ["orientation.heading_first_viewport", "orientation.primary_cta",
          "orientation.content_not_obstructed", "orientation.viewport_meta",
          "orientation.value_proposition", "orientation.legibility"]

PROXY_NOTE = "measured by DOM-order proxy (no browser render available)"


# --- helpers ----------------------------------------------------------------------------------
def sanitize(text, limit: int = MAX_EVIDENCE) -> str:
    if not text:
        return ""
    clean = "".join(ch for ch in str(text) if ch == "\n" or ch >= " ")
    clean = _WS_RE.sub(" ", clean).strip()
    return clean[:limit] + ("…" if len(clean) > limit else "")


def result(check_id, state, *, measurement=None, evidence=None, selector=None,
           page_url=None, reason=None) -> dict:
    return {"check_id": check_id, "state": state, "measurement": measurement,
            "evidence": sanitize(evidence) if evidence else None,
            "selector": selector, "page_url": page_url, "reason": reason}


def thresholds_for(check_id: str, registry: dict) -> dict:
    entry = registry.get(check_id) or {}
    return entry.get("threshold") or DEFAULT_THRESHOLDS.get(check_id, {})


def load_registry(path: str | None) -> dict:
    if not path:
        path = Path(__file__).resolve().parents[3] / "config" / "checks.json"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {c["id"]: c for c in data.get("checks", [])}
    except Exception as exc:
        log.warning("could not load check registry (%s); using embedded defaults", exc)
        return {}


def supported_languages(config_path: str | None = None) -> list:
    path = config_path or (Path(__file__).resolve().parents[3] / "config" / "scoring-config.json")
    try:
        return [s.lower() for s in
                json.loads(Path(path).read_text(encoding="utf-8")).get("language", {})
                .get("supported", ["en"])]
    except Exception:
        return ["en"]


def soup_of(html: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html or "", parser)
        except Exception:
            continue
    return None


def body_prefix(html: str, limit: int) -> str:
    """The first `limit` characters of <body> — the DOM-order stand-in for 'above the fold'."""
    if not html:
        return ""
    lowered = html.lower()
    start = lowered.find("<body")
    if start >= 0:
        start = lowered.find(">", start) + 1
    else:
        start = 0
    return html[start:start + limit]


# --- checks -----------------------------------------------------------------------------------
def check_heading_first_viewport(geometry, html, url, thresholds) -> dict:
    fold = int(thresholds.get("first_viewport_px", 800))
    if geometry:
        fold = int(geometry.get("viewport_height") or fold)
        above = [h for h in geometry.get("headings", [])
                 if h.get("top") is not None and h["top"] < fold and (h.get("text") or "").strip()]
        if above:
            return result("orientation.heading_first_viewport", "pass",
                          measurement=f"{len(above)} heading(s) above {fold}px", page_url=url,
                          evidence=f"Heading visible on arrival: {above[0].get('text')}")
        return result("orientation.heading_first_viewport", "fail", measurement=0, page_url=url,
                      evidence=f"No heading renders within the first {fold}px, so a visitor cannot "
                               f"confirm they landed in the right place")

    prefix = body_prefix(html, int(thresholds.get("dom_proxy_chars", 2500)))
    soup = soup_of(prefix)
    headings = [h for h in (soup.find_all(["h1", "h2", "h3"]) if soup else [])
                if h.get_text(strip=True)]
    if headings:
        return result("orientation.heading_first_viewport", "pass", measurement=len(headings),
                      page_url=url, reason=PROXY_NOTE,
                      evidence=f"Heading near the top of the document: {headings[0].get_text(strip=True)}")
    return result("orientation.heading_first_viewport", "fail", measurement=0, page_url=url,
                  reason=PROXY_NOTE,
                  evidence="No heading near the top of the document")


def check_primary_cta(geometry, html, url, thresholds) -> dict:
    """Structural on purpose: an anchor or button in the first viewport, never a matched word."""
    fold = int(thresholds.get("first_viewport_px", 800))
    if geometry:
        fold = int(geometry.get("viewport_height") or fold)
        above = [i for i in geometry.get("interactive", [])
                 if i.get("top") is not None and i["top"] < fold]
        actionable = [i for i in above if (i.get("text") or "").strip()]
        if actionable:
            return result("orientation.primary_cta", "pass", measurement=len(actionable),
                          page_url=url,
                          evidence=f"{len(actionable)} actionable element(s) above the fold, "
                                   f"e.g.: {actionable[0].get('text')}")
        return result("orientation.primary_cta", "fail", measurement=0, page_url=url,
                      evidence=f"No link or button renders within the first {fold}px, so there is "
                               f"no obvious next step for an arriving visitor")

    prefix = body_prefix(html, int(thresholds.get("dom_proxy_chars", 2500)))
    soup = soup_of(prefix)
    elements = [] if soup is None else [
        e for e in soup.find_all(["a", "button"]) if e.get_text(strip=True)]
    if elements:
        return result("orientation.primary_cta", "pass", measurement=len(elements), page_url=url,
                      reason=PROXY_NOTE,
                      evidence=f"Actionable element near the top: {elements[0].get_text(strip=True)}")
    return result("orientation.primary_cta", "fail", measurement=0, page_url=url,
                  reason=PROXY_NOTE, evidence="No link or button near the top of the document")


def check_not_obstructed(geometry, page, url, thresholds) -> dict:
    blocked_kind = page.get("blocked_kind")
    if blocked_kind:
        return result("orientation.content_not_obstructed", "fail", measurement=blocked_kind,
                      page_url=url,
                      evidence=f"First-paint content is replaced by an interstitial ({blocked_kind})")
    if not geometry:
        return result("orientation.content_not_obstructed", "unknown", page_url=url,
                      reason="overlay coverage needs a browser render")
    cap = float(thresholds.get("max_overlay_coverage", 0.4))
    overlays = [o for o in geometry.get("overlays", []) if (o.get("coverage") or 0) > cap]
    if overlays:
        worst = max(overlays, key=lambda o: o.get("coverage") or 0)
        return result("orientation.content_not_obstructed", "fail",
                      measurement=worst.get("coverage"), page_url=url,
                      selector=worst.get("tag"),
                      evidence=f"A fixed <{worst.get('tag')}> covers {int((worst.get('coverage') or 0) * 100)}% "
                               f"of the first screen, hiding the answer the visitor arrived for")
    return result("orientation.content_not_obstructed", "pass", measurement=0, page_url=url,
                  evidence="No overlay blankets the first screen")


def check_viewport_meta(soup, url) -> dict:
    tag = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}) if soup else None
    if tag and (tag.get("content") or "").strip():
        return result("orientation.viewport_meta", "pass", measurement=tag.get("content"),
                      page_url=url, evidence=f"Viewport declared: {tag.get('content')}")
    return result("orientation.viewport_meta", "fail", measurement="absent", page_url=url,
                  selector='meta[name="viewport"]',
                  evidence="No meta viewport, so the page renders unusably small on mobile — where "
                           "most AI-referred traffic arrives")


def prominent_text(geometry, html, thresholds) -> tuple:
    """The headline area: the topmost heading plus the first paragraph after it.

    Scoring only this — rather than every word above the fold — is what stops the check being
    gamed by appending text. A page with no heading has no prominent claim by definition.
    """
    note = None
    heading_text = ""
    if geometry and geometry.get("headings"):
        fold = int(geometry.get("viewport_height") or thresholds.get("first_viewport_px", 800))
        above = [h for h in geometry["headings"]
                 if h.get("top") is not None and h["top"] < fold and (h.get("text") or "").strip()]
        if above:
            heading_text = above[0]["text"]
        follow = (geometry.get("above_fold_text") or "")[:400]
        return (heading_text + " " + follow).strip(), heading_text, note

    note = PROXY_NOTE
    prefix = body_prefix(html, int(thresholds.get("dom_proxy_chars", 2500)))
    soup = soup_of(prefix)
    if soup is None:
        return "", "", note
    heading = next((h for h in soup.find_all(["h1", "h2"]) if h.get_text(strip=True)), None)
    if heading is None:
        return "", "", note
    heading_text = heading.get_text(" ", strip=True)
    follow = ""
    for sibling in heading.find_all_next(["p", "h2", "li"], limit=3):
        follow += " " + sibling.get_text(" ", strip=True)
        if len(follow) > 400:
            break
    return (heading_text + " " + follow).strip(), heading_text, note


def check_value_proposition(geometry, html, url, thresholds) -> dict:
    """Language-dependent. Only reached when the page declares a supported language."""
    verbs = [v.lower() for v in thresholds.get("action_verbs", [])]
    nouns = [n.lower() for n in thresholds.get("offering_nouns", [])]
    text, heading_text, note = prominent_text(geometry, html, thresholds)

    if not heading_text.strip():
        return result("orientation.value_proposition", "fail", measurement=0, page_url=url,
                      reason=note,
                      evidence="No headline above the fold, so there is no prominent statement of "
                               "what you offer for an arriving visitor to read")

    min_text = int(thresholds.get("min_text_chars", 80))
    if len(text.strip()) < min_text:
        return result("orientation.value_proposition", "fail", measurement=len(text.strip()),
                      page_url=url, reason=note,
                      evidence=f"Only {len(text.strip())} characters in the headline area — too "
                               f"little for a visitor to learn what you offer")

    lowered = text.lower()
    found_verbs = [v for v in verbs if re.search(r"\b" + re.escape(v) + r"\w*\b", lowered)]
    found_nouns = [n for n in nouns if re.search(r"\b" + re.escape(n) + r"s?\b", lowered)]
    score = (1 if found_verbs else 0) + (1 if found_nouns else 0)
    minimum = int(thresholds.get("min_signal_score", 1))
    measurement = f"signal score {score} (threshold {minimum})"

    if score >= minimum + 1:
        return result("orientation.value_proposition", "pass", measurement=measurement,
                      page_url=url, reason=note,
                      evidence=f"Headline area states an action and an offering: {heading_text}")
    if score >= minimum:
        return result("orientation.value_proposition", "partial", measurement=measurement,
                      page_url=url, reason=note,
                      evidence=f"Headline area hints at the offering but not clearly: {heading_text}")
    return result("orientation.value_proposition", "fail", measurement=measurement, page_url=url,
                  reason=note,
                  evidence=f"Headline area does not say what you do or offer: {heading_text}")


def check_legibility(geometry, url, thresholds) -> dict:
    minimum = float(thresholds.get("min_body_font_px", 14))
    if not geometry or geometry.get("body_font_px") is None:
        return result("orientation.legibility", "unknown", page_url=url,
                      reason="needs a computed font size from a browser render; inferring from "
                             "inline styles would be unreliable")
    size = float(geometry["body_font_px"])
    if size >= minimum:
        return result("orientation.legibility", "pass", measurement=size, page_url=url,
                      evidence=f"Body text renders at {size}px")
    return result("orientation.legibility", "fail", measurement=size, page_url=url, selector="body",
                  evidence=f"Body text renders at {size}px, below the {minimum}px comfort threshold "
                           f"for mobile reading")


# --- input handling --------------------------------------------------------------------------
def artifact_from_html_file(path: str) -> dict:
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    match = _LANG_ATTR_RE.search(html[:4000])
    lang = match.group(1).lower() if match else None
    return {"requested_url": f"file://{path}",
            "language": {"detected": lang, "source": "html_lang" if lang else None,
                         "supported": bool(lang) and lang.split("-")[0] in supported_languages()},
            "pages": [{"url": f"file://{path}", "role": "homepage", "status": "ok",
                       "raw": {"status": 200, "html": html, "byte_size": len(html),
                               "content_type": "text/html", "html_sha256": ""}}]}


def analyze(artifact: dict, registry: dict) -> list:
    if artifact.get("blocked_before_fetch"):
        return [result(c, "unknown", reason="blocked_before_fetch") for c in CHECKS]

    pages = artifact.get("pages") or []
    usable = [p for p in pages if p.get("status") == "ok" and (p.get("raw") or {}).get("html")]
    if not usable:
        reason = "no page could be read"
        if pages and pages[0].get("blocked_kind"):
            reason = f"page blocked: {pages[0]['blocked_kind']}"
        return [result(c, "unknown", reason=reason,
                       page_url=pages[0].get("url") if pages else None) for c in CHECKS]

    home = usable[0]
    url = home.get("url")
    rendered = home.get("rendered") or {}
    geometry = rendered.get("geometry") or {}
    html = (rendered.get("html") if rendered.get("available") else None) or \
           (home.get("raw") or {}).get("html") or ""
    soup = soup_of(html)

    language = artifact.get("language") or {}
    lang_ok = bool(language.get("supported"))
    lang_tag = language.get("detected")

    out = []
    for check_id, fn, args in (
        ("orientation.heading_first_viewport", check_heading_first_viewport,
         (geometry, html, url, thresholds_for("orientation.heading_first_viewport", registry))),
        ("orientation.primary_cta", check_primary_cta,
         (geometry, html, url, thresholds_for("orientation.primary_cta", registry))),
        ("orientation.content_not_obstructed", check_not_obstructed,
         (geometry, home, url, thresholds_for("orientation.content_not_obstructed", registry))),
        ("orientation.viewport_meta", check_viewport_meta, (soup, url)),
    ):
        try:
            out.append(fn(*args))
        except Exception as exc:
            log.warning("%s failed: %s", check_id, exc)
            out.append(result(check_id, "unknown", reason=f"analyzer error: {exc}"))

    if lang_ok:
        try:
            out.append(check_value_proposition(
                geometry, html, url, thresholds_for("orientation.value_proposition", registry)))
        except Exception as exc:
            log.warning("value_proposition failed: %s", exc)
            out.append(result("orientation.value_proposition", "unknown",
                              reason=f"analyzer error: {exc}"))
    else:
        out.append(result("orientation.value_proposition", "unknown", page_url=url,
                          reason="language_unsupported_or_undetected"
                                 + (f" (detected: {lang_tag})" if lang_tag else "")))

    try:
        out.append(check_legibility(geometry, url, thresholds_for("orientation.legibility", registry)))
    except Exception as exc:
        out.append(result("orientation.legibility", "unknown", reason=f"analyzer error: {exc}"))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--artifact")
    src.add_argument("--html-file")
    parser.add_argument("--config", help="path to config/checks.json")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    registry = load_registry(args.config)
    try:
        artifact = (json.loads(Path(args.artifact).read_text(encoding="utf-8"))
                    if args.artifact else artifact_from_html_file(args.html_file))
        results = analyze(artifact, registry)
    except Exception as exc:
        log.error("analyzer failed: %s", exc)
        results = []
    json.dump(results, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
