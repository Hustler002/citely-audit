#!/usr/bin/env python3
"""quotability-density-audit (mechanics 2 + 3) — is this content citable, and does it survive summarizing?

Pure consumer of the crawl artifact. ZERO network access, no exceptions.

Usage:
  python quotability_density.py --artifact crawl_artifact.json [--config config/checks.json]
  python quotability_density.py --html-file page.html          [--config config/checks.json]

Output: a JSON array of CHECK STATES on stdout (see references/check-result-schema.json).

i18n is built in, not retrofitted: two of the six checks are language-dependent and use English
vocabulary. They run ONLY when the page declares a supported language, and otherwise resolve to
`unknown` — never `fail`. Judging a German page with English heuristics would produce a wall of
false positives, which the rubric penalises exactly as hard as misses.

The four structural checks (title, meta description, heading hierarchy, scannable blocks) are
language-independent and always run.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("quotability-density-audit")


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

MAX_EVIDENCE = 300
_WS_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_DIGIT_RE = re.compile(r"\d")
_PROPER_NOUN_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]{2,}")
_LANG_ATTR_RE = re.compile(
    r"<html[^>]*\blang\s*=\s*[\"']([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)[\"']", re.IGNORECASE)

DEFAULT_THRESHOLDS = {
    "content.title_descriptive": {"min_chars": 15, "max_chars": 70},
    "content.meta_description": {"min_chars": 50, "max_chars": 160},
    "content.heading_hierarchy": {"max_level_skip": 1},
    "content.scannable_blocks": {"min_blocks": 1, "min_items_per_block": 2,
                                 "short_page_chars": 1000},
    "quotability.self_contained_facts": {"min_quotable_sentences": 3, "min_sentence_chars": 40,
                                         "max_sentence_chars": 300, "min_words": 6},
    "density.factual_ratio": {"min_ratio": 0.25, "long_page_word_count": 2000},
}

CHECKS = ["content.title_descriptive", "content.meta_description", "content.heading_hierarchy",
          "content.scannable_blocks", "quotability.self_contained_facts", "density.factual_ratio"]


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

# --- Resilience helpers -------------------------------------------------------------------------
# Artifacts may be replayed from disk, hand-written or truncated. Every accessor degrades to an
# empty value rather than raising: an analyzer that throws takes the whole audit down with it.

def safe_pages(artifact) -> list:
    if not isinstance(artifact, dict):
        return []
    pages = artifact.get("pages")
    if not isinstance(pages, list):
        return []
    return [p for p in pages if isinstance(p, dict)]


def page_html(page) -> str:
    if not isinstance(page, dict):
        return ""
    raw = page.get("raw")
    if not isinstance(raw, dict):
        return ""
    html = raw.get("html")
    return html if isinstance(html, str) else ""


def rendered_html(page) -> str:
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

    Without this, an internal exception produced a result keyed by the FUNCTION name, which the
    scoring engine rejects as registry drift — crashing the orchestrator and losing the real check.
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



def visible_text_from(soup) -> str:
    if soup is None:
        return ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return _WS_RE.sub(" ", soup.get_text(" ")).strip()


def split_sentences(text: str) -> list:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def is_quotable(sentence: str, thresholds: dict) -> bool:
    """A sentence a retrieval system could lift verbatim and still have it make sense.

    Requires: a workable length, enough words, a concrete anchor (a number or a proper noun), and
    no dangling opener — a sentence starting with "It" or "However" depends on the previous one and
    is exactly what gets dropped when an answer engine quotes a fragment.
    """
    min_chars = int(thresholds.get("min_sentence_chars", 40))
    max_chars = int(thresholds.get("max_sentence_chars", 300))
    min_words = int(thresholds.get("min_words", 6))
    openers = {o.lower() for o in thresholds.get("dangling_openers", [])}

    if not (min_chars <= len(sentence) <= max_chars):
        return False
    words = sentence.split()
    if len(words) < min_words:
        return False
    if words[0].strip(",.:;").lower() in openers:
        return False
    return bool(_DIGIT_RE.search(sentence) or _PROPER_NOUN_RE.search(sentence))


# --- structural checks (always run, any language) ------------------------------------------------
def check_title(soup, url, thresholds) -> dict:
    title = (soup.title.get_text().strip() if soup and soup.title else "") if soup else ""
    if not title:
        return result("content.title_descriptive", "fail", measurement=0, page_url=url,
                      selector="title", evidence="Page has no <title> element")
    lo, hi = int(thresholds.get("min_chars", 15)), int(thresholds.get("max_chars", 70))
    if lo <= len(title) <= hi:
        return result("content.title_descriptive", "pass", measurement=len(title), page_url=url,
                      evidence=f"Title ({len(title)} chars): {title}")
    why = "shorter than" if len(title) < lo else "longer than"
    return result("content.title_descriptive", "partial", measurement=len(title), page_url=url,
                  selector="title",
                  evidence=f"Title is {len(title)} characters, {why} the {lo}-{hi} range: {title}")


def check_meta_description(soup, url, thresholds) -> dict:
    tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) if soup else None
    content = (tag.get("content") or "").strip() if tag else ""
    if not content:
        return result("content.meta_description", "fail", measurement=0, page_url=url,
                      selector='meta[name="description"]',
                      evidence="No meta description, so answer engines have no ready-made summary to reuse")
    lo, hi = int(thresholds.get("min_chars", 50)), int(thresholds.get("max_chars", 160))
    if lo <= len(content) <= hi:
        return result("content.meta_description", "pass", measurement=len(content), page_url=url,
                      evidence=f"Meta description ({len(content)} chars): {content}")
    return result("content.meta_description", "partial", measurement=len(content), page_url=url,
                  selector='meta[name="description"]',
                  evidence=f"Meta description is {len(content)} characters, outside the {lo}-{hi} range")


def check_heading_hierarchy(soup, url, thresholds) -> dict:
    headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]) if soup else []
    if not headings:
        return result("content.heading_hierarchy", "fail", measurement=0, page_url=url,
                      evidence="No headings at all, so there is no document outline for a summarizer to follow")

    levels = [int(h.name[1]) for h in headings]
    max_skip = int(thresholds.get("max_level_skip", 1))
    skips = [(levels[i - 1], levels[i]) for i in range(1, len(levels))
             if levels[i] - levels[i - 1] > max_skip]
    h1_count = levels.count(1)
    measurement = f"{len(headings)} headings, h1_count={h1_count}, skips={len(skips)}"

    if not skips and h1_count == 1:
        return result("content.heading_hierarchy", "pass", measurement=measurement, page_url=url,
                      evidence=f"Clean outline: {measurement}")
    problems = []
    if h1_count != 1:
        problems.append(f"{h1_count} h1 elements (expected exactly 1)")
    if skips:
        problems.append("level jumps " + ", ".join(f"h{a}->h{b}" for a, b in skips[:3]))
    return result("content.heading_hierarchy", "partial", measurement=measurement, page_url=url,
                  evidence="Outline issues: " + "; ".join(problems))


def check_scannable_blocks(soup, url, text, thresholds) -> dict:
    if soup is None:
        return result("content.scannable_blocks", "unknown", page_url=url,
                      reason="HTML parser unavailable")
    min_items = int(thresholds.get("min_items_per_block", 2))
    blocks = 0
    for lst in soup.find_all(["ul", "ol"]):
        if len(lst.find_all("li", recursive=False)) >= min_items:
            blocks += 1
    for table in soup.find_all("table"):
        if len(table.find_all("tr")) >= min_items:
            blocks += 1
    for dl in soup.find_all("dl"):
        if len(dl.find_all("dt")) >= min_items:
            blocks += 1

    minimum = int(thresholds.get("min_blocks", 1))
    if blocks >= minimum:
        return result("content.scannable_blocks", "pass", measurement=blocks, page_url=url,
                      evidence=f"{blocks} structured block(s) (lists/tables) carrying content")

    # Conservative: only fail when the page is long enough that structure would clearly help.
    short = int(thresholds.get("short_page_chars", 1000))
    if len(text) < short:
        return result("content.scannable_blocks", "partial", measurement=blocks, page_url=url,
                      evidence=f"No lists or tables, though the page is short ({len(text)} chars) "
                               f"so there may be little to structure")
    return result("content.scannable_blocks", "fail", measurement=blocks, page_url=url,
                  evidence=f"No lists or tables across {len(text)} characters of prose; facts buried "
                           f"in paragraphs are extracted far less reliably than structured blocks")


# --- language-dependent checks -------------------------------------------------------------------
def check_quotability(text, url, thresholds) -> dict:
    sentences = split_sentences(text)
    if not sentences:
        return result("quotability.self_contained_facts", "fail", measurement=0, page_url=url,
                      evidence="No sentences found to quote")
    quotable = [s for s in sentences if is_quotable(s, thresholds)]
    minimum = int(thresholds.get("min_quotable_sentences", 3))
    measurement = f"{len(quotable)}/{len(sentences)} sentences quotable (threshold {minimum})"

    if len(quotable) >= minimum:
        return result("quotability.self_contained_facts", "pass", measurement=measurement,
                      page_url=url,
                      evidence=f"Self-contained statements found, e.g.: {quotable[0]}")
    if quotable:
        return result("quotability.self_contained_facts", "partial", measurement=measurement,
                      page_url=url,
                      evidence=f"Only {len(quotable)} self-contained statement(s), e.g.: {quotable[0]}")
    return result("quotability.self_contained_facts", "fail", measurement=measurement, page_url=url,
                  evidence="No self-contained factual statements: nothing here can be lifted and "
                           "cited without surrounding context")


def check_density(text, url, thresholds) -> dict:
    sentences = split_sentences(text)
    if not sentences:
        return result("density.factual_ratio", "fail", measurement=0, page_url=url,
                      evidence="No prose to assess")

    quote_thresholds = {**DEFAULT_THRESHOLDS["quotability.self_contained_facts"], **thresholds}
    factual = [s for s in sentences if is_quotable(s, quote_thresholds)]
    ratio = len(factual) / len(sentences)
    minimum = float(thresholds.get("min_ratio", 0.25))
    words = len(text.split())
    long_page = words > int(thresholds.get("long_page_word_count", 2000))

    filler = [t for t in thresholds.get("filler_tokens", []) if t.lower() in text.lower()]
    measurement = f"ratio {ratio:.2f} (threshold {minimum}), {words} words"

    if ratio >= minimum:
        return result("density.factual_ratio", "pass", measurement=measurement, page_url=url,
                      evidence=f"{len(factual)} of {len(sentences)} sentences carry concrete facts")
    detail = (f"Only {len(factual)} of {len(sentences)} sentences carry concrete facts "
              f"({measurement})")
    if filler:
        detail += f"; marketing filler present: {', '.join(filler[:4])}"
    if long_page:
        return result("density.factual_ratio", "fail", measurement=measurement, page_url=url,
                      evidence=detail + ". On a long page, sparse facts are the first thing a "
                                        "summarizer discards")
    return result("density.factual_ratio", "partial", measurement=measurement, page_url=url,
                  evidence=detail)


# --- input handling --------------------------------------------------------------------------
def artifact_from_html_file(path: str) -> dict:
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    lang = None
    match = _LANG_ATTR_RE.search(html[:4000])
    if match:
        lang = match.group(1).lower()
    return {"requested_url": f"file://{path}",
            "language": {"detected": lang, "source": "html_lang" if lang else None,
                         "supported": bool(lang) and lang.split("-")[0] in supported_languages()},
            "pages": [{"url": f"file://{path}", "role": "homepage", "status": "ok",
                       "raw": {"status": 200, "html": html, "byte_size": len(html),
                               "content_type": "text/html", "html_sha256": ""}}]}


def analyze(artifact: dict, registry: dict) -> list:
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
    html = rendered_html(home)

    soup = soup_of(html)
    text = visible_text_from(soup_of(html))  # fresh parse: visible_text_from mutates the tree

    language = artifact.get("language")
    language = language if isinstance(language, dict) else {}
    lang_ok = bool(language.get("supported"))
    lang_tag = language.get("detected")

    out = []
    for fn, args in (
        (check_title, (soup, url, thresholds_for("content.title_descriptive", registry))),
        (check_meta_description, (soup, url, thresholds_for("content.meta_description", registry))),
        (check_heading_hierarchy, (soup, url, thresholds_for("content.heading_hierarchy", registry))),
        (check_scannable_blocks, (soup, url, text, thresholds_for("content.scannable_blocks", registry))),
    ):
        try:
            out.append(fn(*args))
        except Exception as exc:
            # Index-based recovery was fragile; finalize() now guarantees the contract regardless.
            log.warning("%s failed: %s", fn.__name__, exc)
            out.append(result(CHECKS[len(out)] if len(out) < len(CHECKS) else CHECKS[-1],
                              "unknown", page_url=url,
                              reason=f"analyzer error: {type(exc).__name__}"))

    # Language gate: English vocabulary must never be applied to a page we cannot confirm is English.
    for check_id, fn in (("quotability.self_contained_facts", check_quotability),
                         ("density.factual_ratio", check_density)):
        if not lang_ok:
            out.append(result(check_id, "unknown", page_url=url,
                              reason="language_unsupported_or_undetected"
                                     + (f" (detected: {lang_tag})" if lang_tag else "")))
            continue
        try:
            out.append(fn(text, url, thresholds_for(check_id, registry)))
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
