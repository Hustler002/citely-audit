#!/usr/bin/env python3
"""crawl-render-extraction-audit (mechanic 1) — can a machine reach, render and parse this page?

Pure consumer of the crawl artifact. ZERO network access, no exceptions.

Usage:
  python crawl_render_extract.py --artifact crawl_artifact.json [--config scoring-config.json]
  python crawl_render_extract.py --html-file page.html          [--config scoring-config.json]

Output: a JSON array of CHECK STATES on stdout (see references/check-result-schema.json).
Not findings — report wording lives once in config/checks.json and is applied by the orchestrator,
so it can never drift between analyzers.

Evaluation scope: the homepage (pages[0]) is authoritative for these checks, because it is the page
AI assistants are most likely to fetch and cite. Other audited pages inform evidence but do not
override the homepage verdict.

Skills are self-contained folders (agentskills.io), so this file deliberately re-implements small
helpers rather than importing across skill boundaries. Drift is caught by tests, not prevented by
imports.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("crawl-render-extraction-audit")


LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Send every log record to stderr, and mean it.

    `logging.basicConfig` is a SILENT NO-OP when the root logger already has a handler, so a
    dependency that configured logging at import time keeps its handler AND its stream. Verified:
    with a library calling `basicConfig(stream=sys.stdout)` first, our records land on stdout and
    our format is ignored. This analyzer prints its check states to stdout, so that would corrupt
    the contract the orchestrator parses. `force=True` is the load-bearing argument.
    """
    logging.basicConfig(stream=sys.stderr, level=level, force=True, format=LOG_FORMAT)

# Static, anchored patterns only — never built from page content, so they cannot become a ReDoS
# vector on hostile input.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DIGIT_RE = re.compile(r"\d")

MAX_EVIDENCE = 300

CHECKS = ["access.http_ok", "access.ai_crawlers_allowed", "access.indexable",
          "render.content_without_js", "extraction.semantic_html",
          "extraction.facts_not_image_only"]

DEFAULT_THRESHOLDS = {
    "render.content_without_js": {"min_visible_chars_raw": 500, "empty_floor_chars": 50},
    "extraction.facts_not_image_only": {"max_image_only_fact_ratio": 0.5,
                                        "min_images_to_evaluate": 3},
}


# --- helpers ----------------------------------------------------------------------------------
def visible_text(html: str) -> str:
    if not html:
        return ""
    s = _COMMENT_RE.sub(" ", html)
    s = _SCRIPT_RE.sub(" ", s)
    s = _STYLE_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def sanitize(text: str, limit: int = MAX_EVIDENCE) -> str:
    """Collapse whitespace, strip control characters, truncate.

    Evidence is page-derived and therefore untrusted: it is data for a human or an agent to read,
    never instructions. Keeping it short and inert is part of the injection defence.
    """
    if not text:
        return ""
    clean = "".join(ch for ch in text if ch == "\n" or ch >= " ")
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


# SINGLE SOURCE OF TRUTH: config/scoring-config.json -> render.spa_framework_markers /
# spa_mount_selectors. These constants are only a FALLBACK for running this skill standalone outside
# the repo, and tests/test_analyzers.py asserts they match the config exactly — so adding a framework
# to config can never silently fail to reach this analyzer.
_FALLBACK_SPA_MOUNTS = ["#root", "#app", "#__next", "#__nuxt", "[data-reactroot]", "[ng-app]"]
_FALLBACK_SPA_MARKERS = ["__NEXT_DATA__", "window.__NUXT__", "ng-version", "data-server-rendered",
                         "__remixContext", "__sveltekit", "window.__INITIAL_STATE__", "data-vue-meta"]


def load_spa_config(config_path: str | None = None) -> tuple:
    """Read SPA markers from scoring-config.json, falling back to the embedded copy."""
    path = config_path or (Path(__file__).resolve().parents[3] / "config" / "scoring-config.json")
    try:
        render = json.loads(Path(path).read_text(encoding="utf-8")).get("render", {})
        mounts = render.get("spa_mount_selectors") or _FALLBACK_SPA_MOUNTS
        markers = render.get("spa_framework_markers") or _FALLBACK_SPA_MARKERS
        return list(mounts), list(markers)
    except Exception:
        return list(_FALLBACK_SPA_MOUNTS), list(_FALLBACK_SPA_MARKERS)


def local_spa_signals(html: str, mounts=None, markers=None) -> dict:
    """Detect client-rendered-shell evidence from raw HTML, independent of the render tier."""
    out = {"empty_mount_node": None, "framework_markers": []}
    if not html:
        return out
    if mounts is None or markers is None:
        mounts, markers = load_spa_config()
    out["framework_markers"] = [m for m in markers if m in html]
    for selector in mounts:
        ident = selector.strip("#[]").split("=")[0]
        if not ident:
            continue
        pattern = re.compile(
            r"<(\w+)[^>]*\bid\s*=\s*[\"']" + re.escape(ident) + r"[\"'][^>]*>(\s*)</\1>",
            re.IGNORECASE)
        if pattern.search(html):
            out["empty_mount_node"] = selector if selector.startswith("#") else f"#{ident}"
            break
    return out


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

# --- Resilience helpers -------------------------------------------------------------------------
# The artifact is produced by our own orchestrator, but it may also be hand-written, replayed from
# disk, or truncated. Every accessor below degrades to an empty value rather than raising, because
# an analyzer that throws takes the entire audit down with it.

def safe_pages(artifact) -> list:
    """Page list from an artifact of any shape. A dict, string or None yields []."""
    if not isinstance(artifact, dict):
        return []
    pages = artifact.get("pages")
    if not isinstance(pages, list):
        return []
    return [p for p in pages if isinstance(p, dict)]


def page_html(page) -> str:
    """Raw HTML as a string. Non-string values (ints, None, lists) yield ''."""
    if not isinstance(page, dict):
        return ""
    raw = page.get("raw")
    if not isinstance(raw, dict):
        return ""
    html = raw.get("html")
    return html if isinstance(html, str) else ""


def rendered_html(page) -> str:
    """Prefer the rendered DOM when a real render produced one; else the raw HTML."""
    if not isinstance(page, dict):
        return ""
    rendered = page.get("rendered")
    if isinstance(rendered, dict) and rendered.get("available"):
        html = rendered.get("html")
        if isinstance(html, str) and html:
            return html
    return page_html(page)


def homepage_of(pages) -> dict | None:
    """The page the audit was actually asked about.

    Checks documented as homepage-authoritative must measure THIS page or nothing. Falling back to
    whichever page happened to load would report another page's verdicts under the homepage's name
    and hide the reason the homepage could not be read.
    """
    for page in pages:
        if page.get("role") == "homepage":
            return page
    return pages[0] if pages else None


def homepage_block_reason(home) -> str:
    if home is None:
        return "no page could be read"
    if home.get("blocked_kind"):
        return f"homepage blocked: {home['blocked_kind']}"
    if home.get("status") == "error":
        return f"homepage could not be fetched ({home.get('skip_reason') or 'error'})"
    if home.get("status") == "skipped":
        return f"homepage skipped ({home.get('skip_reason') or 'skipped'})"
    return "homepage returned no readable HTML"


def finalize(results, page_url=None) -> list:
    """Guarantee the output contract: exactly CHECKS, every id valid, no duplicates.

    This is what makes an internal error survivable. Previously an exception produced a result
    keyed by the FUNCTION name, which the scoring engine rejects as registry drift — crashing the
    orchestrator and losing the real check. Now anything unrecognised is dropped and any missing
    check is reported honestly as `unknown`.
    """
    by_id, extras = {}, []
    for row in results or []:
        if not isinstance(row, dict):
            continue
        cid = row.get("check_id")
        if cid in CHECKS and cid not in by_id:
            by_id[cid] = row
        elif cid not in CHECKS:
            extras.append(cid)
    for cid in CHECKS:
        if cid not in by_id:
            reason = "analyzer produced no result for this check"
            if extras:
                reason += f" (internal error in {extras[0]})"
            by_id[cid] = result(cid, "unknown", page_url=page_url, reason=reason)
    return [by_id[cid] for cid in CHECKS]



# --- checks -----------------------------------------------------------------------------------
def check_http_ok(page, url) -> dict:
    status = (page.get("raw") or {}).get("status")
    if status is None:
        return result("access.http_ok", "unknown", page_url=url, reason="no response recorded")
    ok = 200 <= int(status) < 300
    return result("access.http_ok", "pass" if ok else "fail",
                  measurement=status, page_url=url,
                  evidence=f"HTTP {status} returned for the audited URL")


def check_ai_crawlers(artifact, url) -> dict:
    info = artifact.get("ai_crawlers") or {}
    if not info.get("determinable"):
        return result("access.ai_crawlers_allowed", "unknown", page_url=url,
                      reason="robots.txt could not be read, so AI-crawler access is undetermined")
    blocked = list(info.get("blocked") or [])
    if not blocked:
        allowed = sorted((info.get("allowed") or {}).keys())
        return result("access.ai_crawlers_allowed", "pass", measurement=0, page_url=url,
                      evidence=f"robots.txt permits all {len(allowed)} checked AI crawlers")
    return result("access.ai_crawlers_allowed", "fail", measurement=len(blocked), page_url=url,
                  selector="/robots.txt",
                  evidence=f"robots.txt disallows: {', '.join(blocked)}")


def check_indexable(page, url) -> dict:
    meta = (page.get("meta_robots") or "").lower()
    header = (page.get("x_robots_tag") or "").lower()
    combined = f"{meta} {header}".strip()
    if "noindex" in combined:
        source = "meta name=robots" if "noindex" in meta else "X-Robots-Tag header"
        return result("access.indexable", "fail", measurement=combined, page_url=url,
                      selector=source, evidence=f"{source} declares: {combined}")
    return result("access.indexable", "pass", measurement=combined or "none", page_url=url,
                  evidence="No noindex directive found in meta robots or X-Robots-Tag")


def check_content_without_js(page, url, thresholds) -> dict:
    """Measured from RAW HTML alone, so the verdict is `verified` in BOTH render tiers.

    The render diff is supporting evidence (how much JavaScript adds), never the basis of the
    judgement — otherwise this check would silently become heuristic whenever no browser is present.
    """
    raw_html = page_html(page)
    minimum = int(thresholds.get("min_visible_chars_raw", 500))
    raw_len = len(visible_text(raw_html))

    rendered = page.get("rendered") or {}
    rendered_len = len(visible_text(rendered.get("html") or "")) if rendered.get("available") else None

    # Prefer signals computed during rendering, but fall back to detecting them ourselves so the
    # analyzer works standalone (--html-file) and cannot be blinded by a missing rendered block.
    signals = dict(rendered.get("heuristic_signals") or {})
    if not signals.get("empty_mount_node") and not signals.get("framework_markers"):
        signals.update(local_spa_signals(raw_html))

    if raw_len >= minimum:
        return result("render.content_without_js", "pass", measurement=raw_len, page_url=url,
                      evidence=f"{raw_len} characters of visible text present before any JavaScript runs")

    # Below the comfortable bar. Only call this a RENDERING defect when JavaScript is demonstrably
    # the cause — otherwise a small, honestly server-rendered page would be falsely flagged. Sparse
    # content is a different mechanic (information density), judged by its own check.
    floor = int(thresholds.get("empty_floor_chars", 100))
    js_evidence = []
    if signals.get("empty_mount_node"):
        js_evidence.append(f"empty mount node {signals['empty_mount_node']}")
    if signals.get("framework_markers"):
        js_evidence.append(f"framework markers {', '.join(signals['framework_markers'])}")
    if rendered_len is not None and rendered_len > max(raw_len * 2, raw_len + 200):
        js_evidence.append(f"rendering added {rendered_len - raw_len} characters "
                           f"({raw_len} -> {rendered_len})")

    if js_evidence:
        return result("render.content_without_js", "fail", measurement=raw_len, page_url=url,
                      selector=signals.get("empty_mount_node"),
                      evidence=f"Only {raw_len} characters of visible text before JavaScript runs; "
                               f"{'; '.join(js_evidence)}")

    if raw_len < floor:
        return result("render.content_without_js", "fail", measurement=raw_len, page_url=url,
                      evidence=f"Page carries almost no readable text ({raw_len} characters), "
                               f"so there is nothing for an AI assistant to extract or cite")

    return result("render.content_without_js", "pass", measurement=raw_len, page_url=url,
                  evidence=f"{raw_len} characters of visible text available without JavaScript "
                           f"(below the {minimum}-character comfort bar, but no evidence that "
                           f"JavaScript is required — sparse content is judged separately)")


def check_semantic_html(page, url) -> dict:
    html = page_html(page)
    soup = soup_of(html)
    if soup is None:
        return result("extraction.semantic_html", "unknown", page_url=url,
                      reason="HTML parser unavailable")

    landmarks = [t for t in ("main", "article", "header", "nav") if soup.find(t)]
    h1s = soup.find_all("h1")
    has_primary = bool(soup.find("main") or soup.find("article"))
    single_h1 = len(h1s) == 1

    measurement = f"landmarks={','.join(landmarks) or 'none'}; h1_count={len(h1s)}"
    if has_primary and single_h1:
        return result("extraction.semantic_html", "pass", measurement=measurement, page_url=url,
                      evidence=f"Primary content landmark present with exactly one h1 ({measurement})")
    if landmarks or h1s:
        return result("extraction.semantic_html", "partial", measurement=measurement, page_url=url,
                      evidence=f"Partial semantic structure: {measurement}")
    return result("extraction.semantic_html", "fail", measurement=measurement, page_url=url,
                  evidence="No semantic landmarks and no h1 — extractors cannot tell content from chrome")


def check_facts_not_image_only(page, url, thresholds) -> dict:
    """Flag images that appear to carry factual content with no text equivalent.

    Deliberately conservative (a statistical check per PLAN §6.3): an image is only suspicious when
    its filename carries digits AND it has no usable alt text. Decorative imagery and properly
    described images are ignored, because a false 'your facts are trapped in pictures' finding on an
    unseen site is worse than a miss.
    """
    html = page_html(page)
    soup = soup_of(html)
    if soup is None:
        return result("extraction.facts_not_image_only", "unknown", page_url=url,
                      reason="HTML parser unavailable")

    images = soup.find_all("img")
    minimum = int(thresholds.get("min_images_to_evaluate", 3))
    if len(images) < minimum:
        return result("extraction.facts_not_image_only", "not_applicable",
                      measurement=len(images), page_url=url,
                      reason=f"only {len(images)} images; below the {minimum} needed to judge")

    suspicious = []
    for img in images:
        alt = (img.get("alt") or "").strip()
        src = (img.get("src") or "")
        filename = urlparse(src).path.rsplit("/", 1)[-1]
        looks_factual = bool(_DIGIT_RE.search(filename))
        has_text_equivalent = len(alt) >= 10
        if looks_factual and not has_text_equivalent:
            suspicious.append(filename or src)

    ratio = len(suspicious) / len(images)
    cap = float(thresholds.get("max_image_only_fact_ratio", 0.5))
    measurement = f"{len(suspicious)}/{len(images)} images (ratio {ratio:.2f}, threshold {cap})"

    if not suspicious:
        return result("extraction.facts_not_image_only", "pass", measurement=measurement,
                      page_url=url, evidence=f"No fact-bearing images lack a text equivalent ({measurement})")
    if ratio > cap:
        return result("extraction.facts_not_image_only", "fail", measurement=measurement,
                      page_url=url, selector="img[alt='']",
                      evidence=f"Images that appear to carry facts but have no alt text: "
                               f"{', '.join(suspicious[:5])} ({measurement})")
    return result("extraction.facts_not_image_only", "partial", measurement=measurement,
                  page_url=url,
                  evidence=f"Some fact-bearing images lack a text equivalent: "
                           f"{', '.join(suspicious[:5])} ({measurement})")


# --- input handling ---------------------------------------------------------------------------
def artifact_from_html_file(path: str) -> dict:
    """Wrap a local HTML file in a minimal artifact so the analyzer stays independently runnable."""
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    return {
        "requested_url": f"file://{path}",
        "ai_crawlers": {"determinable": False, "allowed": {}, "blocked": []},
        "pages": [{
            "url": f"file://{path}", "role": "homepage", "status": "ok",
            "raw": {"status": 200, "content_type": "text/html", "byte_size": len(html),
                    "html_sha256": "", "html": html},
            "meta_robots": None, "x_robots_tag": None,
        }],
    }


def analyze(artifact: dict, registry: dict) -> list:
    """All six checks for this mechanic. Every failure degrades to `unknown`, never a crash."""
    if isinstance(artifact, dict) and artifact.get("blocked_before_fetch"):
        return finalize([result(c, "unknown", reason="blocked_before_fetch") for c in CHECKS])

    pages = safe_pages(artifact)
    home = homepage_of(pages)
    # These checks are homepage-authoritative, so an unreadable homepage yields `unknown` with the
    # real reason — never another page's verdicts wearing the homepage's name.
    if home is None or home.get("status") != "ok" or not page_html(home):
        reason = homepage_block_reason(home)
        url = home.get("url") if home else None
        return finalize([result(c, "unknown", reason=reason, page_url=url) for c in CHECKS], url)

    url = home.get("url")
    out = []
    # The check id travels WITH the call, so an internal error still reports against the real
    # check rather than against a function name the registry has never heard of.
    for check_id, fn, args in (
        ("access.http_ok", check_http_ok, (home, url)),
        ("access.ai_crawlers_allowed", check_ai_crawlers, (artifact, url)),
        ("access.indexable", check_indexable, (home, url)),
        ("render.content_without_js", check_content_without_js,
         (home, url, thresholds_for("render.content_without_js", registry))),
        ("extraction.semantic_html", check_semantic_html, (home, url)),
        ("extraction.facts_not_image_only", check_facts_not_image_only,
         (home, url, thresholds_for("extraction.facts_not_image_only", registry))),
    ):
        try:
            out.append(fn(*args))
        except Exception as exc:
            log.warning("%s failed: %s", check_id, exc)
            out.append(result(check_id, "unknown", page_url=url,
                              reason=f"analyzer error: {type(exc).__name__}"))
    return finalize(out, url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--artifact")
    src.add_argument("--html-file")
    parser.add_argument("--config", help="path to config/checks.json")
    args = parser.parse_args(argv)

    configure_logging()
    registry = load_registry(args.config)

    try:
        if args.artifact:
            artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        else:
            artifact = artifact_from_html_file(args.html_file)
        results = analyze(artifact, registry)
    except Exception as exc:
        log.error("analyzer failed: %s", exc)
        results = []

    json.dump(results, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
