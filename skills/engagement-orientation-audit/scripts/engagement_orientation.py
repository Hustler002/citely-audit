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

# Specificity, not vocabulary. A value proposition is recognised by naming something CONCRETE —
# a measured figure or a proper noun — because no closed word list can hold the open web's product
# categories. The lookbehinds stop a capitalised first word counting as a name.
_ANCHOR_NUMBER_RE = re.compile(r"(?:[$£€¥]\s?\d|\d[\d,.]*\s?%|\b\d[\d,.]*\s?[a-zA-Z]{1,12}\b)")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*|[.!?]")


def has_proper_noun(text: str) -> bool:
    """A capitalised word used INSIDE a sentence, which is where real names show up.

    Done token by token rather than with one regex, because the rule needs the PREVIOUS word and a
    variable-length lookbehind is not expressible. Two exclusions, both found by probing rather
    than by reasoning:

      * the first word of a sentence is capitalised by grammar, not by being a name;
      * a capitalised word following another capitalised word is a Title Case run. Without this,
        the heading "Example Domain" reads as a proper noun and example.com — a page that offers
        nothing whatsoever — scores as though it named something specific.
    """
    previous, sentence_start = "", True
    for token in _TOKEN_RE.findall(text or ""):
        if token in ".!?":
            sentence_start = True
            previous = ""
            continue
        if (not sentence_start and len(token) >= 3
                and token[0].isupper() and token[1:].islower()
                and previous and previous[0].islower()):
            return True
        sentence_start = False
        previous = token
    return False
_LANG_ATTR_RE = re.compile(
    r"<html[^>]*\blang\s*=\s*[\"']([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)[\"']", re.IGNORECASE)

PROXY_DEFAULTS = {"dom_proxy_text_chars": 1200, "chrome_text_weight": 0.1, "chrome_text_cap": 400}

DEFAULT_THRESHOLDS = {
    "orientation.heading_first_viewport": {"first_viewport_px": 800, **PROXY_DEFAULTS},
    "orientation.primary_cta": {"first_viewport_px": 800, **PROXY_DEFAULTS},
    "orientation.content_not_obstructed": {"max_overlay_coverage": 0.4},
    "orientation.viewport_meta": {},
    "orientation.value_proposition": {"first_viewport_px": 800, "min_signal_score": 1,
                                      "max_vague_markers": 2, **PROXY_DEFAULTS},
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


try:
    from bs4 import Comment, NavigableString, Tag
except ImportError:
    # bs4 absent. `soup_of` then returns None and every DOM check degrades to `unknown`, so these
    # names are never reached; an empty tuple keeps `isinstance` valid rather than raising NameError.
    Comment = NavigableString = Tag = ()


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


def safe_geometry(page) -> dict:
    """Geometry with every field type-checked.

    Tier-A geometry is produced by JavaScript running in a hostile page. A site can return
    unexpected shapes (nulls, strings where numbers belong), so each field is validated here rather
    than at a dozen call sites — wrong types previously turned real checks into `unknown`.
    """
    if not isinstance(page, dict):
        return {}
    rendered = page.get("rendered")
    if not isinstance(rendered, dict):
        return {}
    geo = rendered.get("geometry")
    if not isinstance(geo, dict) or not geo:
        return {}

    def num(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def rows(key):
        items = geo.get(key)
        if not isinstance(items, list):
            return []
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            out.append({"tag": item.get("tag") if isinstance(item.get("tag"), str) else "",
                        "top": num(item.get("top")),
                        "text": item.get("text") if isinstance(item.get("text"), str) else "",
                        "coverage": num(item.get("coverage"))})
        return out

    text = geo.get("above_fold_text")
    return {"viewport_height": num(geo.get("viewport_height")),
            "body_font_px": num(geo.get("body_font_px")),
            "above_fold_text": text if isinstance(text, str) else "",
            "headings": rows("headings"), "interactive": rows("interactive"),
            "overlays": rows("overlays")}



# --- Tier-B fold window -------------------------------------------------------------------------
# Subtrees that occupy no line box, so they must not consume the fold budget. Inline <svg> icon
# sprites and a critical-CSS <style> block routinely run to tens of kilobytes while rendering as
# nothing or as a 24px glyph.
NON_RENDERING_TAGS = ("script", "style", "svg", "noscript", "template", "head", "link", "meta",
                      "title", "canvas", "map", "iframe", "object", "embed")

# Chrome floats or collapses instead of pushing content down the page, so it is charged at a
# discount. `nav` and floating dialogs additionally cannot supply the page's headline: a consent
# banner is not a value proposition, and a menu label is not a hero.
NAV_TAGS, NAV_ROLES = ("nav",), ("navigation", "menu", "menubar", "search", "tablist")
BANNER_TAGS, BANNER_ROLES = ("header",), ("banner",)
FLOATING_TAGS, FLOATING_ROLES = ("dialog",), ("dialog", "alertdialog")
HEADLINE_KINDS = (None, "banner")

_HIDDEN_STYLE_RE = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
MAX_WALK_NODES = 40000


def _role_of(tag) -> str:
    role = tag.get("role")
    if isinstance(role, list):
        role = " ".join(role)
    return role.strip().lower() if isinstance(role, str) else ""


def chrome_kind(tag) -> str | None:
    """`"nav"`, `"banner"`, `"floating"` or None — landmarks and ARIA roles only.

    Deliberately not class or id based. A framework or CMS allowlist only ever fits the sites it
    was written against, and `class="header"` means whatever the author wanted it to mean.
    """
    role = _role_of(tag)
    if tag.name in NAV_TAGS or role in NAV_ROLES:
        return "nav"
    if tag.name in BANNER_TAGS or role in BANNER_ROLES:
        return "banner"
    if tag.name in FLOATING_TAGS or role in FLOATING_ROLES:
        return "floating"
    return None


def is_hidden(tag) -> bool:
    """Nodes present in the DOM but absent from the screen, so absent from the fold budget too."""
    try:
        if tag.has_attr("hidden"):
            return True
        if str(tag.get("aria-hidden", "")).strip().lower() == "true":
            return True
        style = tag.get("style")
        return isinstance(style, str) and bool(_HIDDEN_STYLE_RE.search(style))
    except Exception:
        return False


def element_text(el) -> str:
    """Visible text, falling back to the accessible name of an image-only element.

    A logo headline — `<h1><img alt="Acme"></h1>` — holds no text node yet is precisely what a
    visitor sees on arrival. Tier A applies the same fallback, so the tiers cannot disagree.
    """
    try:
        text = el.get_text(" ", strip=True)
    except Exception:
        return ""
    if text:
        return text
    label = el.get("aria-label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    for img in el.find_all("img", limit=3):
        alt = img.get("alt")
        if isinstance(alt, str) and alt.strip():
            return alt.strip()
    return ""


class FoldWindow:
    """The Tier-B stand-in for 'what renders in the first screen'.

    Replaces a raw markup slice, which measured the wrong quantity. On python.org the first 16,151
    characters of `<body>` are 11,414 characters of tags and 1,345 of text, so a 2,500-character
    markup budget never left the masthead: the first heading sat 6.5x beyond the cutoff and the
    check reported "No heading near the top of the document" about a page whose hero is a heading.

    Three corrections:
      * non-rendering subtrees and hidden nodes are removed before anything is counted, so an
        inline `<svg>` sprite or a critical-CSS block cannot consume the budget;
      * the budget is spent in VISIBLE TEXT rather than in markup;
      * chrome is charged at a discount, because a collapsed mega-menu occupies one bar on screen
        however many links it holds. The discount is capped PER CHROME ROOT, so wrapping a page in
        `<nav>` exhausts the budget at full price instead of buying unlimited free space.

    The markup is parsed before it is measured, never sliced. Slicing at a character offset cuts
    through tags and attribute values, handing the parser wreckage it then has to guess at.
    """

    def __init__(self, html: str, thresholds: dict):
        self.budget = float(thresholds.get("dom_proxy_text_chars",
                                           PROXY_DEFAULTS["dom_proxy_text_chars"]))
        weight = float(thresholds.get("chrome_text_weight", PROXY_DEFAULTS["chrome_text_weight"]))
        cap = float(thresholds.get("chrome_text_cap", PROXY_DEFAULTS["chrome_text_cap"]))
        self.weight = min(max(weight, 0.0), 1.0)
        self.cap = max(cap, 0.0)
        self.body = None
        self._offsets: dict = {}
        self._kinds: dict = {}
        self.total = 0.0
        self.truncated = False
        self._build(html)

    # -- construction ---------------------------------------------------------------------------
    def _build(self, html: str) -> None:
        soup = soup_of(html)
        if soup is None:
            return
        body = soup.body or soup
        try:
            for el in body.find_all(NON_RENDERING_TAGS):
                el.decompose()
            for comment in body.find_all(string=lambda s: isinstance(s, Comment)):
                comment.extract()
            for el in body.find_all(is_hidden):
                el.decompose()
        except Exception as exc:
            log.warning("fold window pruning failed: %s", exc)
        self.body = body
        self._walk(body)

    def _chrome_owners(self, body) -> dict:
        """Map every node to the chrome root that owns it, so the discount is charged once."""
        owners: dict = {}
        try:
            roots = [t for t in body.find_all(True) if chrome_kind(t) is not None]
        except Exception:
            return owners
        for root in roots:
            kind = chrome_kind(root)
            if id(root) in owners:          # nested chrome belongs to the outermost root
                continue
            owners[id(root)] = (id(root), kind)
            try:
                for node in root.find_all(True):
                    owners.setdefault(id(node), (id(root), kind))
            except Exception:
                continue
        return owners

    def _walk(self, body) -> None:
        owners = self._chrome_owners(body)
        # A chrome root's discount covers this many raw characters; past it, text costs full price.
        allowance = (self.cap / self.weight) if self.weight > 0 else 0.0
        seen_by_root: dict = {}
        spent = 0.0
        visited = 0

        for node in body.descendants:
            visited += 1
            if visited > MAX_WALK_NODES:
                self.truncated = True
                break
            if isinstance(node, Tag):
                self._offsets[id(node)] = spent
                self._kinds[id(node)] = owners.get(id(node), (None, None))[1]
                continue
            if not isinstance(node, NavigableString):
                continue
            parent = node.parent
            if parent is None:
                continue
            length = len(str(node).strip())
            if not length:
                continue
            root_id, _kind = owners.get(id(parent), (None, None))
            if root_id is None:
                spent += length
                continue
            used = seen_by_root.get(root_id, 0.0)
            discounted = max(0.0, min(float(length), allowance - used))
            spent += discounted * self.weight + (length - discounted)
            seen_by_root[root_id] = used + length

        self.total = spent

    # -- queries --------------------------------------------------------------------------------
    def usable(self) -> bool:
        return self.body is not None

    def offset(self, el) -> float:
        return self._offsets.get(id(el), float("inf"))

    def kind(self, el) -> str | None:
        return self._kinds.get(id(el))

    def above_fold(self, names, *, headline_only: bool = False) -> list:
        """Elements of `names` that fall inside the window, in document order."""
        if self.body is None:
            return []
        try:
            candidates = self.body.find_all(names)
        except Exception:
            return []
        out = []
        for el in candidates:
            if self.offset(el) >= self.budget:
                continue
            if headline_only and self.kind(el) not in HEADLINE_KINDS:
                continue
            if not element_text(el):
                continue
            out.append(el)
        return out


_FOLD_CACHE: dict = {}
_FOLD_CACHE_MAX = 8


def fold_window(html: str, thresholds: dict) -> FoldWindow:
    """Build, or reuse, the fold window for `html`.

    Three checks ask for the same window on the same page, and re-parsing a 50 KB document three
    times per page across a five-page scan is render budget spent for nothing.
    """
    key = (hash(html or ""), len(html or ""),
           thresholds.get("dom_proxy_text_chars"),
           thresholds.get("chrome_text_weight"),
           thresholds.get("chrome_text_cap"))
    cached = _FOLD_CACHE.get(key)
    if cached is not None:
        return cached
    window = FoldWindow(html or "", thresholds)
    if len(_FOLD_CACHE) >= _FOLD_CACHE_MAX:
        _FOLD_CACHE.clear()
    _FOLD_CACHE[key] = window
    return window


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

    window = fold_window(html, thresholds)
    if not window.usable():
        return result("orientation.heading_first_viewport", "unknown", page_url=url,
                      reason="page produced no parseable document body")
    headings = window.above_fold(["h1", "h2", "h3"])
    if headings:
        return result("orientation.heading_first_viewport", "pass", measurement=len(headings),
                      page_url=url, reason=PROXY_NOTE,
                      evidence=f"Heading near the top of the document: {element_text(headings[0])}")
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

    window = fold_window(html, thresholds)
    if not window.usable():
        return result("orientation.primary_cta", "unknown", page_url=url,
                      reason="page produced no parseable document body")
    # Navigation links count here, unlike for the headline: a menu genuinely is a next step for an
    # arriving visitor, even though it is not a statement of what the page offers.
    elements = window.above_fold(["a", "button"])
    if elements:
        return result("orientation.primary_cta", "pass", measurement=len(elements), page_url=url,
                      reason=PROXY_NOTE,
                      evidence=f"Actionable element near the top: {element_text(elements[0])}")
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
    window = fold_window(html, thresholds)
    if not window.usable():
        return "", "", note
    # `headline_only` excludes navigation and floating dialogs. A menu label is not a hero, a
    # consent banner is not a value proposition, and excluding them is what stops the discount
    # given to chrome from being farmed: wrapping the page in <nav> now yields no headline at all.
    headings = window.above_fold(["h1", "h2"], headline_only=True)
    if not headings:
        return "", "", note
    heading = headings[0]
    heading_text = element_text(heading)
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

    # SPECIFICITY is the primary signal. Requiring a word from an 18-noun list failed "Emergency
    # lock repair across Leeds" at high severity while passing example.com, which offers nothing at
    # all — the list simply had no entry for locksmithing, and could never hold every trade on the
    # web. Vocabulary is kept, but as extra positive evidence only: a word missing from the list
    # can never cause a failure.
    anchored = bool(_ANCHOR_NUMBER_RE.search(text)) or has_proper_noun(text)
    found_verbs = [v for v in verbs if re.search(r"\b" + re.escape(v) + r"\w*\b", lowered)]
    found_nouns = [n for n in nouns if re.search(r"\b" + re.escape(n) + r"s?\b", lowered)]
    # BOTH kinds of vocabulary, not either. A single incidental hit is not evidence of anything:
    # example.com passed on the link label "Learn more" matching the action verb "learn", which
    # says nothing about what the page offers. Requiring a verb AND a noun restores the two-signal
    # idea the original scoring had, while the anchor path covers every trade and product category
    # the lists could never enumerate.
    vocabulary = bool(found_verbs and found_nouns)

    # The mirror image, and the reason an open-ended list is safe HERE: vague markers only ever
    # subtract. A phrase missing from this list can never cause a failure, whereas a product
    # category missing from the offering nouns used to cause one on every unseen trade.
    markers = [m.lower() for m in thresholds.get("vague_markers", [])]
    vague_hits = [m for m in markers if m in lowered]
    filler = len(vague_hits) >= int(thresholds.get("max_vague_markers", 2))

    specific = anchored or vocabulary
    measurement = (f"anchor={'y' if anchored else 'n'} vocabulary={'y' if vocabulary else 'n'} "
                   f"filler={len(vague_hits)} (cap {thresholds.get('max_vague_markers', 2)})")

    # Filler CAPS the outcome rather than subtracting from it. Subtracting let one proper noun
    # rescue a hero carrying seven filler phrases, because anchor + vocabulary outran the penalty:
    # a page can name itself and still say nothing about what it offers.
    if filler:
        if not specific:
            return result("orientation.value_proposition", "fail", measurement=measurement,
                          page_url=url, reason=note,
                          evidence=f"Headline area is generic filler with nothing specific in it: "
                                   f"{heading_text}")
        return result("orientation.value_proposition", "partial", measurement=measurement,
                      page_url=url, reason=note,
                      evidence=f"Headline area says something specific but it is buried in "
                               f"generic filler: {heading_text}")

    if specific:
        return result("orientation.value_proposition", "pass", measurement=measurement,
                      page_url=url, reason=note,
                      evidence=f"Headline area names something specific: {heading_text}")
    # Readable, no filler, but nothing concrete. Partial rather than fail, because this check is
    # heuristic and language-gated, and a borderline call should take the softer verdict.
    return result("orientation.value_proposition", "partial", measurement=measurement,
                  page_url=url, reason=note,
                  evidence=f"Headline area is readable but names nothing specific: {heading_text}")


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
    geometry = safe_geometry(home)
    html = rendered_html(home)
    soup = soup_of(html)

    language = artifact.get("language")
    language = language if isinstance(language, dict) else {}
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
            out.append(result(check_id, "unknown", page_url=url,
                              reason=f"analyzer error: {type(exc).__name__}"))

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
        out.append(result("orientation.legibility", "unknown", page_url=url,
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
