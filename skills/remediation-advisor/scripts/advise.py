#!/usr/bin/env python3
"""remediation-advisor — prescription, not detection.

Every other skill in this marketplace measures the page. This one measures nothing: it consumes
verdicts that already exist and answers the question they leave open — what exactly do I change,
and how will I know it worked?

Usage:
  python advise.py --findings findings.json --check-states states.json \
                   --artifact crawl_artifact.json [--config config/checks.json]
  python advise.py --html-file page.html        # proactive detectors only, offline, no findings

Output: a single JSON object on stdout (see references/advice-schema.json). Logs to stderr.

ZERO network access, no exceptions, exactly like the analysis skills. No LLM is involved: snippets
are templates filled from values OBSERVED on the page, and anything unobserved stays a literal
{{PLACEHOLDER}} rather than becoming a plausible-looking invention.

Three rules hold this together, and each exists because its absence produced a real defect:
  1. Never invent a business fact  — the report is read as authoritative; a fabricated address in a
     copy-paste snippet would be published verbatim.
  2. Never report one root cause twice — a proactive item is suppressed when its related check
     already produced a finding. This is the 2026-09-10 double-jeopardy defect in a new place.
  3. Proactive items are not findings — they live in recommendations[], outside summary counts and
     outside scoring, so they can neither inflate nor deflate the score.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("remediation-advisor")

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Send every log record to stderr, and mean it.

    `logging.basicConfig` is a SILENT NO-OP when the root logger already has a handler, so a
    dependency that configured logging at import time keeps its handler AND its stream. This skill
    prints its advice to stdout, so a stray record there would corrupt the contract the
    orchestrator parses. `force=True` is the load-bearing argument.
    """
    logging.basicConfig(stream=sys.stderr, level=level, force=True, format=LOG_FORMAT)


HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = HERE.parent / "references" / "remediation-templates"
CORRECTIVE_PATH = TEMPLATES_DIR / "corrective.json"
PROACTIVE_PATH = TEMPLATES_DIR / "proactive.json"

MAX_EVIDENCE = 300
MAX_SNIPPET = 4000

# Detector order is fixed, so R-001… is deterministic and repeat runs are byte-identical.
DETECTOR_ORDER = ("R:faq_schema", "R:facts_in_prose", "R:undated_claims", "R:image_facts_with_alt")

# Thresholds are deliberately conservative (PLAN.md §8): a proactive suggestion that fires on every
# page is noise, and silence costs nothing because these are not findings.
DEFAULT_THRESHOLDS = {
    "R:faq_schema": {"min_pairs": 2, "min_answer_chars": 40},
    "R:facts_in_prose": {"min_stranded": 4},
    "R:undated_claims": {"min_claims": 3},
    "R:image_facts_with_alt": {"min_images": 2, "min_fact_image_px": 64},
}

_WS_RE = re.compile(r"\s+")
_NON_RENDERING = ("script", "style", "noscript", "template", "svg")

# A number is not a claim. The first version of this matched any multi-digit run, which read
# python.org-style outline numbering — 1.1, 1.2, 1.3 — as 107 factual claims on the deep-chrome
# fixture. Same defect family as `facts_not_image_only` counting every fingerprinted filename as a
# chart: a permissive numeric pattern matches almost the whole web.
#
# _QUANT_RE holds only the three markers that mean "quantity" without reference to any language:
# a currency amount, a percentage, and a thousands-separated figure. Used where the detector must
# stay language-neutral.
_QUANT_RE = re.compile(r"(?:[$£€¥]\s?\d[\d,.]*|\d[\d,.]*\s?%|\b\d{1,3}(?:,\d{3})+\b)")

# _CLAIM_RE adds a number bound to an English unit or quantity noun. That vocabulary is why the
# detector using it declares requires_language, and it is what separates "serves 4,000 teams" from
# a version string.
_UNIT_WORDS = (r"kg|km|mb|gb|tb|hz|ms|mm|cm|ft|lb|oz|ml|hrs?|hours?|mins?|minutes?|seconds?|days?|"
               r"weeks?|months?|years?|users?|customers?|clients?|teams?|members?|countries|"
               r"locations?|stores?|seats?|projects?|employees?|people|times")
_CLAIM_RE = re.compile(
    _QUANT_RE.pattern + r"|(?:\b\d[\d,.]*\s?(?:" + _UNIT_WORDS + r")\b)", re.IGNORECASE)

_DIGITS_RE = re.compile(r"\d[\d,.]*")
# "Label: value" and "12 kg" / "30 minutes" shapes — a specification stated inside a sentence.
# The `(?!//)` matters: without it "https://…" reads as a label called "https", so every page
# quoting a URL in prose would look like it was full of stranded specifications.
_SPEC_RE = re.compile(
    r"(?:\b[\w][\w \-/]{2,30}:\s*(?!//)\S)"
    r"|(?:\d[\d,.]*\s?(?:%|[$£€¥]|kg|km|mb|gb|tb|hz|ms|mm|cm|m|ft|lb|oz|l|ml|hrs?|hours?|mins?|"
    r"minutes?|days?|weeks?|months?|years?|users?|customers?|clients?|countries|locations?|seats?))",
    re.IGNORECASE)


# --- helpers ------------------------------------------------------------------------------------
def sanitize(text, limit: int = MAX_EVIDENCE) -> str:
    """Page-derived text is UNTRUSTED. Strip control characters, collapse whitespace, truncate."""
    if not text:
        return ""
    clean = "".join(ch for ch in str(text) if ch == "\n" or ch >= " ")
    clean = _WS_RE.sub(" ", clean).strip()
    return clean[:limit] + ("…" if len(clean) > limit else "")


def load_json_file(path, what: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("could not read %s (%s)", what, type(exc).__name__)
        return {}


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


# --- Resilience helpers ---------------------------------------------------------------------
# Artifacts may be replayed from disk, hand-written or truncated. Every accessor degrades to an
# empty value rather than raising: this skill must never take the orchestrator down with it.

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

    Same rule as the analyzers: measure the homepage or nothing. Prescribing against whichever page
    happened to load would hand the reader a fix addressed to a page they did not ask about.
    """
    for page in pages:
        if page.get("role") == "homepage":
            return page
    return pages[0] if pages else None


def visible_text_of(soup) -> str:
    """Text a visitor can actually read.

    `get_text()` on an untrimmed tree returns the contents of `<script>` and `<style>`, which is how
    inline JavaScript came to be scored as page copy elsewhere in this project. Non-rendering
    subtrees are removed first, on a throwaway parse so the caller's tree is left intact.
    """
    if soup is None:
        return ""
    for tag in soup(list(_NON_RENDERING)):
        tag.decompose()
    return _WS_RE.sub(" ", soup.get_text(" ")).strip()


def jsonld_blocks(soup) -> list:
    """Every parseable JSON-LD payload on the page, flattened out of any @graph.

    Malformed JSON-LD is common and must degrade to "no blocks", never raise.
    """
    out = []
    if soup is None:
        return out
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        stack = [data]
        seen = 0
        while stack and seen < 500:          # bounded: a hostile page may nest arbitrarily
            item = stack.pop()
            seen += 1
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                out.append(item)
                graph = item.get("@graph")
                if isinstance(graph, (list, dict)):
                    stack.append(graph)
    return out


def _types_of(node) -> set:
    raw = node.get("@type") if isinstance(node, dict) else None
    if isinstance(raw, str):
        return {raw.split("/")[-1].lower()}
    if isinstance(raw, list):
        return {str(t).split("/")[-1].lower() for t in raw if isinstance(t, str)}
    return set()


def meta_content(soup, *, name: str | None = None, prop: str | None = None) -> str:
    if soup is None:
        return ""
    attrs = {"name": name} if name else {"property": prop}
    try:
        tag = soup.find("meta", attrs=attrs)
    except Exception:
        return ""
    if not tag:
        return ""
    value = tag.get("content")
    return value.strip() if isinstance(value, str) else ""


# --- Observed values ------------------------------------------------------------------------
# The hard rule from PLAN.md §7: a snippet value is either observed here or stays a literal
# placeholder. Nothing in this function guesses, derives or completes a business fact.

def observed_values(artifact, soup) -> dict:
    values: dict[str, str] = {}
    pages = safe_pages(artifact)
    home = homepage_of(pages)

    url = ""
    if isinstance(artifact, dict) and isinstance(artifact.get("final_url"), str):
        url = artifact["final_url"]
    if not url and home and isinstance(home.get("url"), str):
        url = home["url"]
    if url:
        values["SITE_URL"] = url
        try:
            host = urlsplit(url).netloc
        except Exception:
            host = ""
        if host:
            values["SITE_HOST"] = host

    if soup is None:
        return values

    title = ""
    try:
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
    except Exception:
        title = ""
    if title:
        values["PAGE_TITLE"] = sanitize(title, 200)

    description = meta_content(soup, name="description")
    if description:
        values["META_DESCRIPTION"] = sanitize(description, 300)

    # Brand name, strongest carrier first: a typed entity states it, Open Graph asserts it, and a
    # title's first segment is merely where it usually sits.
    brand = ""
    for node in jsonld_blocks(soup):
        if _types_of(node) & {"organization", "localbusiness", "person"} or node.get("sameAs"):
            name = node.get("name")
            if isinstance(name, str) and name.strip():
                brand = name.strip()
                break
    if not brand:
        brand = meta_content(soup, prop="og:site_name")
    if not brand and title:
        brand = re.split(r"\s[|–—\-:]\s", title)[0].strip()
    if brand:
        values["BRAND_NAME"] = sanitize(brand, 120)

    logo = ""
    for node in jsonld_blocks(soup):
        candidate = node.get("logo")
        if isinstance(candidate, dict):
            candidate = candidate.get("url")
        if isinstance(candidate, str) and candidate.strip():
            logo = candidate.strip()
            break
    if not logo:
        logo = meta_content(soup, prop="og:image")
    if logo:
        values["LOGO_URL"] = sanitize(logo, 400)

    links = observed_profile_links(soup)
    if links:
        # Two forms, because a JSON array element needs a trailing comma when something follows it
        # and must not have one when it is last. Emitting only the un-terminated form produced a
        # snippet that LOOKED complete and did not parse — the worst possible failure for something
        # whose entire purpose is to be pasted in unmodified.
        values["SAMEAS_LINKS"] = ",\n".join(f'    "{u}"' for u in links)
        values["SAMEAS_LINKS_LEADING"] = "".join(f'    "{u}",\n' for u in links).rstrip("\n")

    return values


def observed_profile_links(soup) -> list:
    """Profile URLs the page already declares as its own.

    Only sameAs and rel="me" — both are the site SAYING these are its profiles. An arbitrary
    outbound link is not, and putting one in a sameAs snippet would be inventing a claim.
    """
    found: list[str] = []
    for node in jsonld_blocks(soup):
        same = node.get("sameAs")
        if isinstance(same, str):
            same = [same]
        if isinstance(same, list):
            for item in same:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    found.append(item.strip())
    try:
        for tag in soup.find_all("a", attrs={"rel": True}):
            rel = tag.get("rel")
            rel = rel if isinstance(rel, list) else [str(rel)]
            if any(str(r).lower() == "me" for r in rel):
                href = tag.get("href")
                if isinstance(href, str) and href.startswith(("http://", "https://")):
                    found.append(href.strip())
    except Exception:
        pass

    unique, seen = [], set()
    for url in found:
        clean = sanitize(url, 300)
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(clean)
    return unique[:10]


_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def fill(template: str | None, values: dict) -> tuple:
    """Substitute observed values; leave everything else as a literal placeholder.

    Returns (filled_text, placeholders_remaining). The remaining list is not a failure — it is the
    deliverable: it tells the reader precisely which facts they must supply, instead of handing
    them a snippet that looks complete and quietly contains something the page never said.
    """
    if not isinstance(template, str) or not template:
        return None, []
    remaining: list[str] = []

    def replace(match):
        key = match.group(1)
        value = values.get(key)
        if isinstance(value, str) and value:
            return value
        remaining.append(match.group(0))
        return match.group(0)

    filled = _PLACEHOLDER_RE.sub(replace, template)
    if len(filled) > MAX_SNIPPET:
        filled = filled[:MAX_SNIPPET] + "\n<!-- snippet truncated -->"
    ordered, seen = [], set()
    for item in remaining:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return filled, ordered


# --- Corrective ---------------------------------------------------------------------------------
def build_corrective(findings, templates: dict, values: dict) -> list:
    """One prescription per finding, keyed by the id the orchestrator already assigned.

    Keyed by finding id rather than check id because the same check can fail on more than one page,
    and a reader following F-004 should not be silently handed F-002's address.
    """
    by_check = templates.get("checks") or {}
    out = []
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        finding_id = finding.get("id")
        check_id = finding.get("check_id")
        if not isinstance(finding_id, str) or not finding_id:
            continue
        entry = by_check.get(check_id)
        if not isinstance(entry, dict):
            continue
        snippet, remaining = fill(entry.get("snippet"), values)
        validation, validation_remaining = fill(entry.get("validation"), values)
        # `target` answers "where do I make this change". It is always supplied, because about half
        # of all findings are an ABSENCE with no element to point at: a missing meta tag has no
        # selector, and "in <head>" is the only address that exists. The analyzer's `selector` still
        # names the exact element when there is one — the two are complementary.
        row = {"finding_id": finding_id, "check_id": check_id, "target": entry.get("target"),
               "snippet": snippet, "validation": validation}
        merged = remaining + [p for p in validation_remaining if p not in remaining]
        if merged:
            row["placeholders_remaining"] = merged
        out.append(row)
    return out


# --- Proactive detectors ------------------------------------------------------------------------
# Each returns None (did not fire) or a dict of {measurement, evidence}. Detectors observe; the
# wording they carry comes from references/remediation-templates/proactive.json, so the prose and
# the logic can be reviewed independently.

def detect_faq_schema(soup, text: str, thresholds: dict):
    """Question-and-answer pairs in plain text, with no FAQPage markup declaring them.

    Question detection is deliberately punctuation-based rather than word-based: "?" and its
    full-width form "？" carry across languages, whereas a list of interrogative words would only
    fit English and would have to be gated off on every other site.
    """
    if soup is None:
        return None
    for node in jsonld_blocks(soup):
        if "faqpage" in _types_of(node):
            return None

    min_pairs = int(thresholds.get("min_pairs", 2))
    min_answer = int(thresholds.get("min_answer_chars", 40))
    pairs, first = 0, ""
    try:
        candidates = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "summary", "dt"])
    except Exception:
        return None

    for tag in candidates[:400]:
        question = _WS_RE.sub(" ", tag.get_text(" ")).strip()
        if not question.endswith(("?", "？")) or len(question) < 8:
            continue
        answer = _answer_after(tag)
        if len(answer) >= min_answer:
            pairs += 1
            if not first:
                first = question
    if pairs < min_pairs:
        return None
    return {"measurement": pairs,
            "evidence": f"{pairs} question-and-answer pairs in plain text, e.g. {first!r}"}


def _answer_after(tag) -> str:
    """Text that reads as the answer to this question element.

    A `<dt>` is answered by its `<dd>`, a `<summary>` by the rest of its `<details>`, and a heading
    by whatever follows it until the next heading. Anything else would count the whole page.
    """
    try:
        name = (tag.name or "").lower()
        if name == "dt":
            sibling = tag.find_next_sibling()
            if sibling is not None and (sibling.name or "").lower() == "dd":
                return _WS_RE.sub(" ", sibling.get_text(" ")).strip()
            return ""
        if name == "summary":
            parent = tag.parent
            if parent is None:
                return ""
            whole = _WS_RE.sub(" ", parent.get_text(" ")).strip()
            head = _WS_RE.sub(" ", tag.get_text(" ")).strip()
            return whole[len(head):].strip()
        collected = []
        for sibling in tag.find_next_siblings():
            if (sibling.name or "").lower() in ("h1", "h2", "h3", "h4", "h5", "h6"):
                break
            collected.append(_WS_RE.sub(" ", sibling.get_text(" ")).strip())
            if sum(len(c) for c in collected) > 600:
                break
        return " ".join(c for c in collected if c).strip()
    except Exception:
        return ""


def detect_facts_in_prose(soup, text: str, thresholds: dict):
    """Specification-shaped facts stranded in paragraphs rather than in a list or table.

    Distinct from `content.scannable_blocks`, which asks whether the page has ANY structured block.
    This asks whether the facts most worth citing are the ones still in prose, and it only fires
    when prose holds more of them than the structured blocks do.
    """
    if soup is None:
        return None
    min_stranded = int(thresholds.get("min_stranded", 4))
    try:
        structured_chars = 0
        structured_hits = 0
        for tag in soup.find_all(["ul", "ol", "dl", "table"])[:300]:
            block = _WS_RE.sub(" ", tag.get_text(" ")).strip()
            structured_chars += len(block)
            structured_hits += len(_SPEC_RE.findall(block))

        prose_hits, sample = 0, ""
        for tag in soup.find_all("p")[:400]:
            if tag.find_parent(["ul", "ol", "dl", "table"]) is not None:
                continue
            paragraph = _WS_RE.sub(" ", tag.get_text(" ")).strip()
            hits = _SPEC_RE.findall(paragraph)
            if hits:
                prose_hits += len(hits)
                if not sample:
                    sample = paragraph
    except Exception:
        return None

    if prose_hits < min_stranded or prose_hits <= structured_hits:
        return None
    return {"measurement": prose_hits,
            "evidence": f"{prose_hits} specification-shaped facts sit in paragraphs against "
                        f"{structured_hits} in lists or tables, e.g. {sample!r}"}


def detect_undated_claims(soup, text: str, thresholds: dict):
    """Numeric claims with no publication date and nothing cited.

    A copyright year is deliberately NOT accepted as a date signal. "© 2026" says when the footer
    was generated, not when the figure above it was true, and accepting it would silence the
    detector on essentially every site while answering none of the question it asks.
    """
    if soup is None:
        return None
    min_claims = int(thresholds.get("min_claims", 3))
    claims = _CLAIM_RE.findall(text or "")
    if len(claims) < min_claims:
        return None

    try:
        if soup.find("time", attrs={"datetime": True}):
            return None
        if soup.find("cite"):
            return None
        if meta_content(soup, prop="article:published_time"):
            return None
    except Exception:
        return None

    for node in jsonld_blocks(soup):
        if node.get("datePublished") or node.get("dateModified"):
            return None

    return {"measurement": len(claims),
            "evidence": f"{len(claims)} numeric claims on the page, with no <time> element, "
                        f"no datePublished and no citation"}


def detect_image_facts_with_alt(soup, text: str, thresholds: dict):
    """Numbers that live in image alt text and nowhere in the page's own words.

    The alt text is correct and the accessibility audit passes; the fact is still missing from the
    document. Distinct from `extraction.facts_not_image_only`, which is about images carrying facts
    with NO text equivalent at all.
    """
    if soup is None:
        return None
    min_images = int(thresholds.get("min_images", 2))
    min_px = int(thresholds.get("min_fact_image_px", 64))
    body_numbers = {_normalise_number(n) for n in _DIGITS_RE.findall(text or "")}

    hits, sample = 0, ""
    try:
        images = soup.find_all("img")[:400]
    except Exception:
        return None

    for img in images:
        alt = img.get("alt")
        if not isinstance(alt, str) or not alt.strip():
            continue
        if _declared_smaller_than(img, min_px):
            continue
        alt_claims = _QUANT_RE.findall(alt)
        if not alt_claims:
            continue
        missing = [c for c in alt_claims
                   if _normalise_number(c) and _normalise_number(c) not in body_numbers]
        if missing:
            hits += 1
            if not sample:
                sample = _WS_RE.sub(" ", alt).strip()
    if hits < min_images:
        return None
    return {"measurement": hits,
            "evidence": f"{hits} images whose alt text carries figures absent from the page text, "
                        f"e.g. {sample!r}"}


def _normalise_number(token: str) -> str:
    """Compare 1,200 and 1200 as the same figure; separators are presentation, not value."""
    digits = re.sub(r"[^\d]", "", str(token or ""))
    return digits.lstrip("0") or digits


def _declared_smaller_than(img, min_px: int) -> bool:
    """An image the page itself declares too small to hold a readable fact.

    Only the page's own declaration is trusted — nothing is fetched or measured here.
    """
    for attr in ("width", "height"):
        raw = img.get(attr)
        if raw is None:
            continue
        try:
            if int(str(raw).strip().rstrip("px")) < min_px:
                return True
        except (TypeError, ValueError):
            continue
    return False


DETECTORS = {
    "R:faq_schema": detect_faq_schema,
    "R:facts_in_prose": detect_facts_in_prose,
    "R:undated_claims": detect_undated_claims,
    "R:image_facts_with_alt": detect_image_facts_with_alt,
}


# --- Assembly -----------------------------------------------------------------------------------
FINDING_STATES = {"fail", "partial"}


def build_recommendations(artifact, html: str, text: str, templates: dict, values: dict,
                          finding_check_ids: set, page_url, diagnostics: dict) -> list:
    """Run every detector, honouring suppression and the language gate, then assign R-001…"""
    detector_meta = templates.get("detectors") or {}
    language = artifact.get("language") if isinstance(artifact, dict) else None
    language = language if isinstance(language, dict) else {}
    language_ok = bool(language.get("supported"))

    out = []
    for detector_id in DETECTOR_ORDER:
        meta = detector_meta.get(detector_id)
        if not isinstance(meta, dict):
            continue

        # The language gate comes first and is absolute: running a word-level heuristic against a
        # page whose language we cannot confirm is the thing i18n gating exists to prevent.
        if meta.get("requires_language") and not language_ok:
            diagnostics.setdefault("language_gated", []).append(detector_id)
            continue

        # Suppression is checked AFTER the detector runs, not before. Checking first was cheaper
        # but recorded "already reported as a finding on X" for detectors that had nothing to say,
        # so the diagnostics asserted a redundancy that was never established — on four of five
        # real sites sampled. Now "suppressed" means exactly one thing: this had something to
        # report and it was withheld because the reader has already been told the root cause.
        try:
            # A fresh parse per detector. `visible_text_of` destroys non-rendering subtrees in
            # place, and one detector reads `<script type="application/ld+json">`, which that pass
            # removes. Re-parsing costs milliseconds and removes a whole class of order-dependent
            # bug, where a detector's verdict would depend on which detector ran before it.
            hit = DETECTORS[detector_id](soup_of(html), text,
                                         DEFAULT_THRESHOLDS.get(detector_id, {}))
        except Exception as exc:
            diagnostics.setdefault("errors", []).append(
                f"{detector_id}: {type(exc).__name__}")
            continue
        if not hit:
            continue

        blocked_by = [c for c in (meta.get("suppressed_by") or []) if c in finding_check_ids]
        if blocked_by:
            diagnostics.setdefault("suppressed", []).append(
                f"{detector_id} had something to report, withheld because {blocked_by[0]} is "
                f"already a finding")
            continue

        snippet, remaining = fill(meta.get("snippet"), values)
        validation, validation_remaining = fill(meta.get("validation"), values)
        row = {
            "id": "R-000",                      # replaced below, once the set is known
            "type": "proactive",
            "detector": detector_id,
            "category": meta.get("category") or "ai_comprehension",
            "title": meta.get("title") or detector_id,
            "summary": meta.get("summary") or "",
            "rationale": meta.get("rationale") or "",
            "evidence": sanitize(hit.get("evidence")),
            "measurement": hit.get("measurement"),
            "snippet": snippet,
            "validation": validation,
            "page_url": page_url,
        }
        merged = remaining + [p for p in validation_remaining if p not in remaining]
        if merged:
            row["placeholders_remaining"] = merged
        out.append(row)

    for index, row in enumerate(out, start=1):
        row["id"] = f"R-{index:03d}"
    return out


def advise(findings, check_states, artifact, corrective_templates, proactive_templates) -> dict:
    diagnostics: dict = {}
    pages = safe_pages(artifact)
    home = homepage_of(pages)

    # Nothing readable means nothing to prescribe against. Emitting suggestions from an empty
    # document would be advice about a page nobody has seen.
    if home is None or not page_html(home):
        diagnostics["errors"] = ["no readable homepage; no advice generated"]
        return {"corrective": [], "recommendations": [], "diagnostics": diagnostics}

    html = rendered_html(home)
    soup = soup_of(html)
    text = visible_text_of(soup_of(html))       # throwaway parse: this call mutates its tree
    values = observed_values(artifact, soup)

    corrective = build_corrective(findings, corrective_templates, values)

    finding_check_ids = {f.get("check_id") for f in (findings or [])
                         if isinstance(f, dict) and f.get("check_id")}
    # In offline mode there are no findings, so suppression falls back to the check states when
    # they were supplied. Without this, a page with no structured data at all would be told to add
    # FAQPage markup — the double-reporting rule, applied to the standalone entry point too.
    for check_id, state in (check_states or {}).items():
        if isinstance(state, str) and state in FINDING_STATES:
            finding_check_ids.add(check_id)

    recommendations = build_recommendations(
        artifact, html, text, proactive_templates, values,
        finding_check_ids, home.get("url"), diagnostics)

    result = {"corrective": corrective, "recommendations": recommendations}
    if diagnostics:
        result["diagnostics"] = diagnostics
    return result


def artifact_from_html_file(path: str) -> dict:
    """Minimal artifact for offline mode, so the skill is independently runnable.

    Language is read from the document's own `lang` attribute, never guessed, matching the
    analyzers: an undeclared language leaves the language-gated detectors silent.
    """
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    match = re.search(r"<html[^>]*\blang\s*=\s*[\"']([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)[\"']",
                      html, re.IGNORECASE)
    lang = match.group(1).lower() if match else None
    return {
        "requested_url": f"file://{path}", "final_url": f"file://{path}",
        "language": {"detected": lang, "source": "html_lang" if lang else None,
                     "supported": bool(lang and lang.split("-")[0] == "en")},
        "pages": [{"url": f"file://{path}", "role": "homepage", "status": "ok",
                   "raw": {"status": 200, "content_type": "text/html", "html": html}}],
    }


EMPTY_ADVICE = {"corrective": [], "recommendations": []}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Citely remediation advisor.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--artifact", help="crawl artifact JSON produced by the orchestrator")
    source.add_argument("--html-file", help="local HTML file; proactive detectors only, offline")
    parser.add_argument("--findings", help="JSON array of findings with ids already assigned")
    parser.add_argument("--check-states", help="JSON object mapping check_id to resolved state")
    parser.add_argument("--config", help="path to config/checks.json (unused today; accepted so "
                                         "the invocation matches the analysis skills)")
    args = parser.parse_args(argv)

    configure_logging()

    corrective_templates = load_json_file(CORRECTIVE_PATH, "corrective templates")
    proactive_templates = load_json_file(PROACTIVE_PATH, "proactive templates")

    try:
        artifact = (json.loads(Path(args.artifact).read_text(encoding="utf-8"))
                    if args.artifact else artifact_from_html_file(args.html_file))
        findings = json.loads(Path(args.findings).read_text(encoding="utf-8")) if args.findings else []
        states = json.loads(Path(args.check_states).read_text(encoding="utf-8")) if args.check_states else {}
        advice = advise(findings if isinstance(findings, list) else [],
                        states if isinstance(states, dict) else {},
                        artifact, corrective_templates, proactive_templates)
    except Exception as exc:
        # The advisor is an enrichment stage. A failure here must cost the reader their snippets,
        # never their report — the orchestrator keeps the registry's prose remediation either way.
        log.error("advisor failed: %s", type(exc).__name__)
        advice = dict(EMPTY_ADVICE, diagnostics={"errors": [type(exc).__name__]})

    json.dump(advice, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
