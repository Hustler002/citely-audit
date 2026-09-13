#!/usr/bin/env python3
"""entity-corroboration-audit (mechanic 4) — can an AI tell WHO this page is about, unambiguously?

Pure consumer of the crawl artifact. ZERO network access, no exceptions. Authority is judged from
the links the page itself publishes; there is no external lookup anywhere in this project. The
artifact declares an `external_corroboration` slot for one, performed in the orchestrator's fetch
stage if it is ever built, but none is implemented and no check here reads it.

Usage:
  python entity_corroboration.py --artifact crawl_artifact.json [--config config/checks.json]
  python entity_corroboration.py --html-file page.html          [--config config/checks.json]

Output: a JSON array of CHECK STATES on stdout (see references/check-result-schema.json).

Evaluation scope: entity markup legitimately lives on any page — an Organization block is often on
/about rather than the homepage. So presence checks accept evidence from ANY successfully fetched
page, and record which page supplied it. This differs from mechanic 1, where the homepage is
authoritative.

Structured data is treated as CLAIMS, never instructions: parsed with size and depth caps, and any
text echoed into evidence is sanitized and truncated.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("entity-corroboration-audit")


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

CHECKS = ["entity.structured_data_present", "entity.organization_declared",
          "entity.sameas_present", "entity.sameas_authority",
          "entity.opengraph_identity", "entity.name_consistency"]

MAX_EVIDENCE = 300
MAX_JSONLD_BYTES = 512 * 1024      # a single block larger than this is not legitimate markup
MAX_JSONLD_DEPTH = 20              # bounds traversal on adversarial nesting

_WS_RE = re.compile(r"\s+")

# Identity types we name explicitly. This is schema.org VOCABULARY, not a site allowlist, and it is
# deliberately not exhaustive: schema.org has around 200 LocalBusiness subtypes, so any fixed list
# is wrong for somebody. `IDENTITY_PROPERTIES` below is what actually generalises — a node carrying
# a name plus real-world contact facts is an identity claim whatever it calls itself.
DEFAULT_IDENTITY_TYPES = [
    "Organization", "Person", "LocalBusiness", "Corporation", "NGO", "NewsMediaOrganization",
    "EducationalOrganization", "GovernmentOrganization", "SportsOrganization", "Airline",
    "Consortium", "Cooperative", "MedicalOrganization", "PerformingGroup", "Project", "Brand",
    "Store", "Restaurant", "Dentist", "Physician", "LegalService", "HomeAndConstructionBusiness",
    "FoodEstablishment", "HealthAndBeautyBusiness", "AutomotiveBusiness", "FinancialService",
    "ProfessionalService", "EntertainmentBusiness", "LodgingBusiness", "TravelAgency",
]

# A node with a name and at least two of these is making a real-world identity claim. This is the
# rule that carries the check on the open web: `Dentist`, `Bakery`, `Plumber` and every subtype
# invented after this file was written are caught without being listed.
IDENTITY_PROPERTIES = ("address", "telephone", "email", "sameAs", "logo", "founder", "vatID",
                       "taxID", "openingHours", "openingHoursSpecification", "geo", "contactPoint",
                       "hasMap", "aggregateRating", "duns", "leiCode", "naics", "isicV4")

# Types that name something without claiming to BE the site's owner. A `WebSite` node called
# "Acme | Widgets for everyone" is a page title, not an entity, and treating it as one produced
# name conflicts against the real Organization node sitting beside it.
NON_IDENTITY_TYPES = ("WebSite", "WebPage", "Article", "NewsArticle", "BlogPosting", "ImageObject",
                      "BreadcrumbList", "SiteNavigationElement", "SearchAction", "WPHeader",
                      "WPFooter", "CollectionPage", "ItemList", "Offer", "Product", "Service")

# Third-party roles. The agency that built the site publishes a Person node with `worksFor`; the
# byline of an article publishes another. Neither is the site's identity, and reading them as
# rival brand names is how a correctly marked-up bakery got told its names conflicted.
#
# `publisher` and `copyrightHolder` are deliberately NOT here. In the graph every major CMS emits,
# an Article's publisher IS the site's own Organization, so excluding it deleted exactly the node
# the whole category is looking for: a bakery's real Organization node vanished and the site was
# graded as having no declared identity.
THIRD_PARTY_KEYS = ("author", "creator", "contributor", "editor", "sponsor", "translator")

# Name separators, so "Bakeshop | NE Portland Retail and Wholesale Bakery" and "Bakeshop" are
# recognised as the same brand rather than as a contradiction.
_NAME_SPLIT_RE = re.compile(r"\s*[|·–—•»«:/\-‐]\s*|\s+[-–]\s+")

# Keep letters and digits in ANY script, not just ASCII. `[^a-z0-9]+` erased every non-Latin
# character, so a name written in its own script normalized to the EMPTY STRING and could never
# match anything — including a byte-identical copy of itself. Measured: `デジタル庁` in <title> and
# the same five characters in og:site_name were reported as corroborating neither each other nor
# the domain. The same held for Greek, Cyrillic, Korean, Arabic and Devanagari, so every site
# naming itself outside the Latin alphabet failed a check it satisfied perfectly.
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)

DEFAULT_THRESHOLDS = {
    "entity.organization_declared": {
        "accepted_types": DEFAULT_IDENTITY_TYPES,
        "identity_properties": list(IDENTITY_PROPERTIES),
        "min_identity_properties": 2},
    "entity.sameas_present": {"min_links": 1},
    "entity.sameas_authority": {
        # Third-party records an AI can corroborate against. Directory and registry listings count
        # in full: a bakery will never have a Wikidata entry, and its Google Business or Yelp
        # record is exactly the anchor that disambiguates it from every other bakery of that name.
        "authority_domains": [
            "wikidata.org", "wikipedia.org", "dbpedia.org", "viaf.org", "isni.org", "ror.org",
            "orcid.org", "crunchbase.com", "opencorporates.com", "companieshouse.gov.uk",
            "sec.gov", "bloomberg.com", "linkedin.com", "g.page", "business.site",
            "maps.google.com", "yelp.com", "tripadvisor.com", "trustpilot.com", "bbb.org",
            "glassdoor.com", "indeed.com", "yellowpages.com", "thomasnet.com", "angi.com",
            "checkatrade.com", "trustedtraders.which.co.uk", "healthgrades.com", "zocdoc.com",
            "avvo.com", "justia.com", "github.com", "gitlab.com",
        ],
        # Self-published profiles. Real corroboration a crawler can follow, but the brand controls
        # both ends, so it cannot settle "which Acme is this?" on its own.
        "social_profile_domains": [
            "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
            "tiktok.com", "pinterest.com", "threads.net", "threads.com", "snapchat.com",
            "reddit.com", "vimeo.com", "flickr.com", "tumblr.com", "medium.com", "substack.com",
            "bsky.app", "mastodon.social", "weibo.com", "vk.com", "xing.com", "line.me",
            "t.me", "telegram.me", "whatsapp.com", "wa.me",
        ],
        # Share widgets, which every CMS emits and which say nothing about identity. Counting
        # `facebook.com/sharer/sharer.php?u=...` as a profile would pass essentially every site.
        "share_path_markers": ["/sharer", "/share", "/intent/", "/shareartic", "/submit",
                               "/dialog/", "/send", "/post?", "/tweet", "/pin/create"],
    },
    "entity.opengraph_identity": {"required_properties": ["og:title", "og:type", "og:url"]},
    "entity.name_consistency": {"min_sources": 2},
}


# --- helpers ----------------------------------------------------------------------------------
def sanitize(text, limit: int = MAX_EVIDENCE) -> str:
    if not text:
        return ""
    text = str(text)
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



def _flatten(node, depth: int = 0):
    """Yield every dict in a JSON-LD tree, honouring @graph and arrays, depth-capped."""
    if depth > MAX_JSONLD_DEPTH:
        return
    if isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "mainEntity", "about", "publisher"):
            if key in node:
                yield from _flatten(node[key], depth + 1)
    elif isinstance(node, list):
        for item in node[:200]:
            yield from _flatten(item, depth + 1)


def extract_jsonld(html: str) -> list:
    """Parse every application/ld+json block. Malformed blocks are skipped, never fatal."""
    soup = soup_of(html)
    if soup is None:
        return []
    blocks = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip() or len(raw) > MAX_JSONLD_BYTES:
            continue
        try:
            blocks.append(json.loads(raw))
        except Exception:
            continue  # malformed JSON-LD is common; it simply yields no entity
    return blocks


def extract_opengraph(html: str) -> dict:
    soup = soup_of(html)
    if soup is None:
        return {}
    out = {}
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop.startswith("og:") and tag.get("content"):
            out[prop] = tag["content"].strip()
    return out


def _types_of(node: dict) -> list:
    raw = node.get("@type") or node.get("type") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).split("/")[-1] for t in raw if t]


def _third_party_ids(blocks: list) -> set:
    """@ids referenced through an authorship or publisher relationship.

    A site built by an agency publishes the agency as a `Person` with `worksFor`; an article
    publishes its byline. Both are entities, neither is the site. Left in, they were read as rival
    brand names and a correctly marked-up bakery was told its own names conflicted.
    """
    refs = set()
    for block in blocks:
        for node in _flatten(block):
            if not isinstance(node, dict):
                continue
            for key in THIRD_PARTY_KEYS:
                value = node.get(key)
                for item in (value if isinstance(value, list) else [value]):
                    if isinstance(item, dict) and item.get("@id"):
                        refs.add(str(item["@id"]))
                    elif isinstance(item, str) and item.startswith(("http", "#", "/")):
                        refs.add(item)
    return refs


def _identity_strength(node: dict, types: set, properties: tuple, minimum: int) -> str | None:
    """`"typed"`, `"described"` or None — how strongly this node claims to be somebody.

    Two independent routes on purpose. The named-type route is fast and explicit; the property
    route is what generalises, because schema.org has roughly 200 LocalBusiness subtypes and no
    fixed list survives contact with the open web. A real dental practice publishing a `Dentist`
    node with an address, a phone number and sameAs links scored 39.3 out of 100 under the old
    five-name allowlist, worse than a site with no markup at all.
    """
    node_types = {t.lower() for t in _types_of(node)}
    if not node_types:
        return None
    if node_types & {t.lower() for t in NON_IDENTITY_TYPES} and not (node_types & types):
        return None
    if not str(node.get("name") or node.get("legalName") or "").strip():
        return None
    if node_types & types:
        return "typed"
    present = sum(1 for p in properties if node.get(p))
    return "described" if present >= minimum else None


def find_identity_entities(blocks: list, thresholds: dict) -> list:
    """The site's own identity nodes, strongest first, third parties removed."""
    types = {t.lower() for t in thresholds.get("accepted_types", DEFAULT_IDENTITY_TYPES)}
    properties = tuple(thresholds.get("identity_properties", IDENTITY_PROPERTIES))
    minimum = int(thresholds.get("min_identity_properties", 2))
    excluded = _third_party_ids(blocks)

    found = []
    for block in blocks:
        for node in _flatten(block):
            if not isinstance(node, dict):
                continue
            if node.get("@id") and str(node["@id"]) in excluded:
                continue
            strength = _identity_strength(node, types, properties, minimum)
            if strength:
                found.append((strength, node))

    # An Organization outranks a Person when both are present: the Person is almost always a
    # founder, an author or the developer who built the site.
    org_like = [n for s, n in found if "person" not in {t.lower() for t in _types_of(n)}]
    if org_like and len(org_like) < len(found):
        found = [(s, n) for s, n in found if n in org_like]
    order = {"typed": 0, "described": 1}
    return [n for _s, n in sorted(found, key=lambda pair: order.get(pair[0], 2))]


def find_entities(blocks: list, accepted: list) -> list:
    """Backwards-compatible wrapper: the same question asked with a bare type list."""
    return find_identity_entities(blocks, {"accepted_types": accepted})


def has_microdata(html: str) -> bool:
    soup = soup_of(html)
    return bool(soup and soup.find(attrs={"itemscope": True}))


# --- Identity links ------------------------------------------------------------------------------
def normalize_name(text) -> str:
    return _NON_ALNUM_RE.sub("", str(text or "").lower())


def name_segments(text) -> list:
    """A name split on its separators, so a brand and its tagline are comparable."""
    parts = [p.strip() for p in _NAME_SPLIT_RE.split(str(text or "")) if p.strip()]
    return [p for p in parts if normalize_name(p)]


def brand_tokens(url: str | None, og: dict, entity_names) -> set:
    """Normalized ways this site refers to itself, used to recognise its own profile links.

    Domain-derived rather than platform-derived. `facebook.com/marysbakery` is Mary's profile for
    the same reason `facebook.com/eff` is EFF's, and neither needs the platform to be on a list —
    which is what stops this becoming a check that only works for the sites we happened to test.
    """
    tokens = set()
    host = (urlparse(url or "").hostname or "").lower()
    labels = [l for l in host.split(".") if l not in ("www", "com", "org", "net", "co", "uk",
                                                      "io", "gov", "edu", "us", "de", "fr")]
    for label in labels:
        token = normalize_name(label)
        if len(token) >= 3:
            tokens.add(token)
    for source in list(entity_names) + [og.get("og:site_name"), og.get("og:title")]:
        for segment in name_segments(source):
            token = normalize_name(segment)
            if len(token) >= 3:
                tokens.add(token)
    return tokens


MAX_PROFILE_SEGMENTS = 2
MAX_HANDLE_SUFFIX = 6

# Site chrome. Identity links live in the masthead or the footer on essentially every small
# business site, so chrome membership recognises a profile whose handle does not match the domain
# — @ThePSF for python.org, /gcbakery for grandcentralbakery.com — which a token rule alone misses.
CHROME_TAGS = ("footer", "header", "nav", "aside")
CHROME_ROLES = ("contentinfo", "banner", "navigation")

# Last path segments that are never a handle, so a footer link to a video or a login page is not
# mistaken for the brand's profile.
NON_HANDLE_SEGMENTS = {"watch", "search", "results", "home", "login", "signin", "signup",
                       "register", "privacy", "terms", "help", "support", "sitemap", "feed",
                       "rss", "index", "explore", "trending", "download", "pricing"}


def _in_chrome(tag) -> bool:
    try:
        for parent in tag.parents:
            if getattr(parent, "name", None) in CHROME_TAGS:
                return True
            role = parent.get("role") if hasattr(parent, "get") else None
            if isinstance(role, str) and role.strip().lower() in CHROME_ROLES:
                return True
    except Exception:
        return False
    return False


def _known_platform(host: str, authority_hosts, social_hosts) -> bool:
    return any(host == d or host.endswith("." + d) for d in authority_hosts) or \
           any(host == d or host.endswith("." + d) for d in social_hosts)


def _is_profile_path(path: str, tokens: set, *, any_handle: bool = False) -> bool:
    """Does this URL look like the brand's own profile, rather than a page that mentions it?

    Shape, not domain. A profile lives at the top of a site — `/eff`, `/@eff`, `/company/eff` —
    while an article that happens to contain the brand name is buried deeper. Matching the brand
    token anywhere in the path claimed `wiki.qt.io/Qt_for_Python` and a dated blog post as
    python.org's own profiles, which would inflate the score of any brand with a common name.
    """
    segments = [s for s in (path or "").split("/") if s.strip()]
    if not segments or len(segments) > MAX_PROFILE_SEGMENTS:
        return False
    handle = normalize_name(segments[-1])
    if not handle or handle in NON_HANDLE_SEGMENTS:
        return False
    if any_handle:
        return True
    for token in tokens:
        if not token:
            continue
        if handle == token:
            return True
        # `/efforg` for eff.org: a handle may carry a short suffix, but not a whole other phrase.
        if handle.startswith(token) and len(handle) - len(token) <= MAX_HANDLE_SUFFIX:
            return True
    return False


def _is_share_link(url: str, markers) -> bool:
    lowered = url.lower()
    if any(m in lowered for m in markers):
        return True
    # Share widgets carry the page being shared as a parameter; profiles never do.
    return "?u=http" in lowered or "&url=http" in lowered or "?url=http" in lowered


def collect_identity_links(html: str, page_url: str | None, og: dict, blocks: list,
                           entity_names, thresholds: dict) -> dict:
    """Every carrier through which this page anchors its identity elsewhere on the web.

    `sameAs` is a schema.org property, but the SIGNAL it encodes — "this entity is also that
    profile" — is expressed at least four ways in the wild, and reading only the first one
    reported `unknown` for sites that visibly do corroborate themselves.
    """
    defaults = DEFAULT_THRESHOLDS["entity.sameas_authority"]
    markers = thresholds.get("share_path_markers", defaults["share_path_markers"])
    authority_hosts = [d.lower() for d in thresholds.get("authority_domains",
                                                         defaults["authority_domains"])]
    social_hosts = [d.lower() for d in thresholds.get("social_profile_domains",
                                                      defaults["social_profile_domains"])]
    declared, rel_me, profiles = [], [], []

    # 1. schema.org sameAs, on ANY node. Restricting this to accepted types meant a `Dentist`
    #    node's sameAs links were invisible purely because of what the node called itself.
    for block in blocks:
        for node in _flatten(block):
            if not isinstance(node, dict):
                continue
            raw = node.get("sameAs") or node.get("sameas") or []
            if isinstance(raw, str):
                raw = [raw]
            declared += [str(x) for x in raw if x and str(x).startswith("http")]

    soup = soup_of(html)
    if soup is not None:
        # 2. microdata sameAs, and 3. rel="me", both explicit machine-readable identity claims.
        for tag in soup.find_all(attrs={"itemprop": re.compile("^sameas$", re.I)}):
            href = tag.get("href") or tag.get("content")
            if href and str(href).startswith("http"):
                declared.append(str(href))
        for tag in soup.find_all(["a", "link"], attrs={"rel": True}):
            rels = [r.lower() for r in (tag.get("rel") or [])]
            href = tag.get("href")
            if "me" in rels and href and str(href).startswith("http"):
                rel_me.append(str(href))

        # 4. Ordinary profile links. Recognised structurally: an off-site link whose path carries
        #    this brand's own name. No platform list, so it works for a bakery on a network that
        #    did not exist when this was written.
        tokens = brand_tokens(page_url, og, entity_names)
        site_host = (urlparse(page_url or "").hostname or "").lower().removeprefix("www.")
        seen = set()
        for tag in soup.find_all("a", href=True):
            href = str(tag["href"])
            if not href.startswith("http"):
                continue
            parsed = urlparse(href)
            host = (parsed.hostname or "").lower().removeprefix("www.")
            if not host or host == site_host or host.endswith("." + site_host if site_host else "\0"):
                continue
            if _is_share_link(href, markers):
                continue
            # Two independent routes, because neither covers the web on its own. The token rule
            # needs the handle to resemble the domain; the chrome rule needs the site to use
            # semantic landmarks. EFF satisfies only the first, python.org only the second.
            by_token = _is_profile_path(parsed.path, tokens)
            by_chrome = (_in_chrome(tag)
                         and _is_profile_path(parsed.path, tokens, any_handle=True)
                         and _known_platform(host, authority_hosts, social_hosts))
            if not (by_token or by_chrome):
                continue
            key = (host, normalize_name(parsed.path))
            if key not in seen:
                seen.add(key)
                profiles.append(href)

    return {"declared": _dedupe(declared), "rel_me": _dedupe(rel_me),
            "profiles": _dedupe(profiles)[:20]}


def _dedupe(items) -> list:
    out, seen = [], set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def classify_links(links, thresholds: dict) -> dict:
    """Split identity links into third-party records and self-published profiles."""
    authority = [d.lower() for d in thresholds.get(
        "authority_domains", DEFAULT_THRESHOLDS["entity.sameas_authority"]["authority_domains"])]
    social = [d.lower() for d in thresholds.get(
        "social_profile_domains",
        DEFAULT_THRESHOLDS["entity.sameas_authority"]["social_profile_domains"])]

    def matches(host, domains):
        return any(host == d or host.endswith("." + d) for d in domains)

    found = {"authority": [], "social": [], "other": []}
    for link in links:
        host = (urlparse(link).hostname or "").lower().removeprefix("www.")
        if not host:
            continue
        if matches(host, authority):
            found["authority"].append(link)
        elif matches(host, social):
            found["social"].append(link)
        else:
            found["other"].append(link)
    return found


# --- checks -----------------------------------------------------------------------------------
def check_structured_data_present(pages_data) -> dict:
    """Is there machine-readable STRUCTURED DATA, in the schema.org sense?

    Open Graph deliberately does not rescue this check any more. Open Graph is a social-preview
    format carrying no schema.org type and no entity graph, and it was already earning credit in
    two other checks in this category — `opengraph_identity` scored its presence and
    `organization_declared` scored it as a weak identity. One signal was paying out three times
    across 46 points of critical weight, which flattened the category onto a fixed point: every
    Open-Graph-only site scored exactly 58.9 at exactly 0.56 coverage, whatever else it published.
    """
    for url, blocks, _og, _micro, _html in pages_data:
        if blocks:
            return result("entity.structured_data_present", "pass", measurement=len(blocks),
                          page_url=url, selector='script[type="application/ld+json"]',
                          evidence=f"{len(blocks)} JSON-LD block(s) found")
    for url, _b, _og, micro, _html in pages_data:
        if micro:
            return result("entity.structured_data_present", "partial", measurement="microdata",
                          page_url=url,
                          evidence="No JSON-LD, but microdata is present. JSON-LD is what answer "
                                   "engines read most reliably.")
    url = pages_data[0][0] if pages_data else None
    return result("entity.structured_data_present", "fail", measurement=0, page_url=url,
                  selector="head",
                  evidence="No JSON-LD or microdata on any audited page, so there is no "
                           "machine-readable description of what this page is about")


def check_organization_declared(pages_data, thresholds) -> dict:
    """Is there a machine-readable statement of WHO this page is about?

    Graded by the strength of the declaration, not merely its presence in JSON-LD. Open Graph
    names an entity but gives it no schema.org type and no sameAs anchor, so it earns partial
    credit — treating it as equivalent to declaring nothing was wrong, and its `verified` failure
    also suppressed three downstream checks as collateral.
    """
    accepted = thresholds.get("accepted_types", DEFAULT_THRESHOLDS[
        "entity.organization_declared"]["accepted_types"])

    # Strongest signal: a typed schema.org entity anywhere in the structured-data graph.
    for url, blocks, _og, _micro, _html in pages_data:
        entities = find_identity_entities(blocks, thresholds)
        if entities:
            names = [e.get("name") for e in entities if e.get("name")]
            types = sorted({t for e in entities for t in _types_of(e)})
            return result("entity.organization_declared", "pass",
                          measurement=", ".join(types), page_url=url,
                          selector='script[type="application/ld+json"]',
                          evidence=f"Declared entity type(s) {', '.join(types)}"
                                   + (f", name: {names[0]}" if names else ""))

    # Weaker but real: Open Graph names the site even without a typed entity.
    for url, _blocks, og, _micro, _html in pages_data:
        site_name = (og.get("og:site_name") or "").strip()
        og_title = (og.get("og:title") or "").strip()
        if site_name or (og_title and og.get("og:url")):
            named = site_name or og_title
            return result("entity.organization_declared", "partial",
                          measurement=f"open_graph:{named}", page_url=url,
                          selector='meta[property="og:site_name"]',
                          evidence=f"Identity declared only via Open Graph (\"{named}\"). This names "
                                   f"the site but carries no schema.org type and no sameAs links, so "
                                   f"an AI cannot corroborate it against an authoritative source.")

    url = pages_data[0][0] if pages_data else None
    return result("entity.organization_declared", "fail", measurement=0, page_url=url,
                  evidence=f"No entity of an accepted identity type ({', '.join(accepted)}) declared, "
                           f"and no Open Graph site identity either")


def _sameas_of(entities) -> list:
    links = []
    for e in entities:
        raw = e.get("sameAs") or e.get("sameas") or []
        if isinstance(raw, str):
            raw = [raw]
        links.extend([str(x) for x in raw if x])
    return links


def check_sameas_present(pages_data, thresholds, accepted, link_index) -> dict:
    """Does this page anchor its identity anywhere else on the web?

    Graded by the STRENGTH of the anchor, not by whether it happens to be expressed as a schema.org
    `sameAs` array. An explicit machine-readable claim — `sameAs`, microdata, `rel="me"` — is a
    pass. Ordinary links to the brand's own profiles are real corroboration a crawler can follow,
    but they are not a declaration of sameness, so they are partial. Nothing anywhere is a fail,
    and it is a VERIFIED fail: "we looked and there is none", not "we could not tell".
    """
    minimum = int(thresholds.get("min_links", 1))
    for url, links in link_index:
        explicit = links["declared"] + links["rel_me"]
        if len(explicit) >= minimum:
            carrier = "sameAs" if links["declared"] else 'rel="me"'
            return result("entity.sameas_present", "pass", measurement=len(explicit), page_url=url,
                          evidence=f"{len(explicit)} declared identity link(s) via {carrier}: "
                                   f"{', '.join(explicit[:3])}")
    for url, links in link_index:
        if len(links["profiles"]) >= minimum:
            return result("entity.sameas_present", "partial", measurement=len(links["profiles"]),
                          page_url=url,
                          evidence=f"{len(links['profiles'])} link(s) to this brand's own profiles "
                                   f"({', '.join(links['profiles'][:3])}), but none declared as "
                                   f"sameAs, so an AI must infer the connection rather than read it")
    url = link_index[0][0] if link_index else (pages_data[0][0] if pages_data else None)
    return result("entity.sameas_present", "fail", measurement=0, page_url=url,
                  selector='script[type="application/ld+json"]',
                  evidence="No sameAs, rel=\"me\" or profile links on any audited page, so this "
                           "identity is not connected to any other record of it on the web")


def check_sameas_authority(pages_data, thresholds, accepted, link_index) -> dict:
    """Does the identity chain reach a record somebody ELSE maintains?

    A directory or registry listing counts in full, not as a lesser tier. A local business will
    never have a Wikidata entry, and its Google Business, Yelp or companies-registry record is
    precisely the third-party anchor that separates it from every other business of the same name.
    Grading only encyclopedia entries would fail the entire small-business web for something it
    cannot obtain.

    Self-published profiles are real but weaker: the brand controls both ends, so they corroborate
    that the account exists without settling which entity it belongs to. Those are partial.
    """
    saw_any = False
    for url, links in link_index:
        all_links = links["declared"] + links["rel_me"] + links["profiles"]
        if not all_links:
            continue
        saw_any = True
        found = classify_links(all_links, thresholds)
        if found["authority"]:
            return result("entity.sameas_authority", "pass", measurement=len(found["authority"]),
                          page_url=url,
                          evidence=f"Identity is anchored to third-party record(s): "
                                   f"{', '.join(found['authority'][:3])}")
        if found["social"]:
            return result("entity.sameas_authority", "partial", measurement=len(found["social"]),
                          page_url=url,
                          evidence=f"Identity links reach only self-published profiles "
                                   f"({', '.join(found['social'][:3])}). The brand controls both "
                                   f"ends, so these cannot settle which organisation this is.")
        return result("entity.sameas_authority", "fail", measurement=0, page_url=url,
                      evidence=f"Identity links exist but none reach a record maintained by anyone "
                               f"else: {', '.join(found['other'][:3])}")
    url = link_index[0][0] if link_index else (pages_data[0][0] if pages_data else None)
    if saw_any:
        return result("entity.sameas_authority", "unknown", page_url=url, reason="not evaluated")
    return result("entity.sameas_authority", "fail", measurement=0, page_url=url,
                  evidence="No identity links of any kind, so there is no chain to anchor")


def check_opengraph_identity(pages_data, thresholds) -> dict:
    required = [p.lower() for p in thresholds.get("required_properties", DEFAULT_THRESHOLDS[
        "entity.opengraph_identity"]["required_properties"])]
    best = None
    for url, _blocks, og, _micro, _html in pages_data:
        present = [p for p in required if p in og]
        if best is None or len(present) > len(best[1]):
            best = (url, present, og)
        if len(present) == len(required):
            return result("entity.opengraph_identity", "pass",
                          measurement=f"{len(present)}/{len(required)}", page_url=url,
                          evidence=f"Open Graph identity tags present: {', '.join(present)}")
    if best and best[1]:
        url, present, _og = best
        missing = [p for p in required if p not in present]
        return result("entity.opengraph_identity", "partial",
                      measurement=f"{len(present)}/{len(required)}", page_url=url,
                      evidence=f"Present: {', '.join(present)}; missing: {', '.join(missing)}")
    url = pages_data[0][0] if pages_data else None
    return result("entity.opengraph_identity", "fail", measurement=f"0/{len(required)}",
                  page_url=url, evidence="No Open Graph identity tags found")


def _title_of(html: str) -> str:
    soup = soup_of(html)
    try:
        return soup.title.get_text(strip=True) if soup and soup.title else ""
    except Exception:
        return ""


def _names_agree(first: str, second: str) -> bool:
    """Do two stated names refer to the same brand?

    Compared by SEGMENT, because a name and the same name with a tagline are not a contradiction.
    Whole-string comparison read "Bakeshop" against the site's own
    "Bakeshop | NE Portland Retail and Wholesale Bakery" as two conflicting brands and failed a
    high-severity check on a bakery whose markup was fine.
    """
    left = {normalize_name(s) for s in name_segments(first)} | {normalize_name(first)}
    right = {normalize_name(s) for s in name_segments(second)} | {normalize_name(second)}
    left = {s for s in left if s}
    right = {s for s in right if s}
    if left & right:
        return True
    return any(a and b and (a in b or b in a) for a in left for b in right)


def check_name_consistency(pages_data, accepted, thresholds) -> dict:
    """Contradictory names actively teach an AI the wrong association — worse than no data.

    Only names that plausibly belong to THIS site are compared. A site built by an agency
    publishes the agency as a `Person`; that is not a rival brand, and reading it as one failed a
    correctly marked-up bakery for "conflicting entity names" against its own web developer.
    """
    for url, blocks, og, _micro, html in pages_data:
        entities = find_identity_entities(blocks, {"accepted_types": accepted})
        entity_names = [str(e.get("name") or e.get("legalName")).strip()
                        for e in entities if e.get("name") or e.get("legalName")]
        site_name = (og.get("og:site_name") or "").strip()
        strong = _dedupe([n for n in entity_names + [site_name] if n])
        weak = _dedupe([n for n in [(og.get("og:title") or "").strip(), _title_of(html)] if n])
        domain_tokens = brand_tokens(url, {}, [])

        if not strong and not weak:
            continue

        if not strong:
            return result("entity.name_consistency", "partial", measurement=len(weak),
                          page_url=url, selector='meta[property="og:site_name"]',
                          evidence=f"The page states a name only in its title (\"{weak[0]}\"), "
                                   f"never in a machine-readable identity field, so there is "
                                   f"nothing for an AI to read as the brand's name")

        conflicts = [n for n in strong[1:] if not _names_agree(strong[0], n)]
        if conflicts:
            # A name that matches the domain or the title is this site's; the others belong to
            # somebody else and are not a contradiction.
            def is_ours(name):
                tokens = {normalize_name(s) for s in name_segments(name)} | {normalize_name(name)}
                return (any(t and any(t in d or d in t for d in domain_tokens) for t in tokens)
                        or any(_names_agree(name, w) for w in weak))

            ours = [n for n in strong if is_ours(n)]
            if len(ours) == 1:
                return result("entity.name_consistency", "pass", measurement=len(ours),
                              page_url=url,
                              evidence=f"Named consistently as \"{ours[0]}\"; other names in the "
                                       f"markup belong to third parties such as the author or the "
                                       f"site's developer")
            return result("entity.name_consistency", "fail", measurement=len(strong),
                          page_url=url, selector='script[type="application/ld+json"]',
                          evidence=f"Conflicting entity names across markup: {sorted(strong)}")

        corroborated = any(_names_agree(strong[0], w) for w in weak) or any(
            normalize_name(s) in domain_tokens or any(normalize_name(s) in d for d in domain_tokens)
            for s in name_segments(strong[0]))
        if len(strong) >= int(thresholds.get("min_sources", 2)) or corroborated:
            return result("entity.name_consistency", "pass", measurement=len(strong),
                          page_url=url,
                          evidence=f"Entity name stated consistently as: {strong[0]}")
        return result("entity.name_consistency", "partial", measurement=len(strong),
                      page_url=url,
                      evidence=f"\"{strong[0]}\" is stated once and matches neither the page title "
                               f"nor the domain, so nothing corroborates it")

    url = pages_data[0][0] if pages_data else None
    return result("entity.name_consistency", "fail", measurement=0, page_url=url,
                  selector="title",
                  evidence="The page states no name at all — no entity name, no og:site_name and "
                           "no title — so an AI has nothing to attach facts to")


# --- input handling ---------------------------------------------------------------------------
def artifact_from_html_file(path: str) -> dict:
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    return {"requested_url": f"file://{path}",
            "pages": [{"url": f"file://{path}", "role": "homepage", "status": "ok",
                       "raw": {"status": 200, "html": html, "byte_size": len(html),
                               "content_type": "text/html", "html_sha256": ""}}]}


def analyze(artifact: dict, registry: dict) -> list:
    if isinstance(artifact, dict) and artifact.get("blocked_before_fetch"):
        return finalize([result(c, "unknown", reason="blocked_before_fetch") for c in CHECKS])

    pages = safe_pages(artifact)
    usable = [p for p in pages if p.get("status") == "ok" and page_html(p)]
    if not usable:
        reason = "no page could be read"
        if pages and pages[0].get("blocked_kind"):
            reason = f"page blocked: {pages[0]['blocked_kind']}"
        url = pages[0].get("url") if pages else None
        return finalize([result(c, "unknown", reason=reason, page_url=url) for c in CHECKS], url)

    # Prefer the rendered DOM when available: entity markup is sometimes injected by tag managers.
    pages_data = []
    for p in usable:
        html = rendered_html(p)
        try:
            pages_data.append((p.get("url"), extract_jsonld(html), extract_opengraph(html),
                               has_microdata(html), html))
        except Exception as exc:  # a single unparseable page must not sink the others
            log.warning("extraction failed for %s: %s", p.get("url"), exc)
            pages_data.append((p.get("url"), [], {}, False, ""))
    if not pages_data:
        return finalize([], None)

    org_thresholds = thresholds_for("entity.organization_declared", registry)
    accepted = org_thresholds.get(
        "accepted_types", DEFAULT_THRESHOLDS["entity.organization_declared"]["accepted_types"])
    authority_thresholds = thresholds_for("entity.sameas_authority", registry)

    # Identity links are gathered once per page and shared by both sameAs checks, so the two can
    # never disagree about what the page actually links to.
    link_index = []
    for url, blocks, og, _micro, html in pages_data:
        names = [e.get("name") or e.get("legalName")
                 for e in find_identity_entities(blocks, org_thresholds)]
        try:
            link_index.append((url, collect_identity_links(html, url, og, blocks,
                                                           [n for n in names if n],
                                                           authority_thresholds)))
        except Exception as exc:
            log.warning("identity link collection failed for %s: %s", url, exc)
            link_index.append((url, {"declared": [], "rel_me": [], "profiles": []}))

    out = []
    for check_id, fn, args in (
        ("entity.structured_data_present", check_structured_data_present, (pages_data,)),
        ("entity.organization_declared", check_organization_declared,
         (pages_data, org_thresholds)),
        ("entity.sameas_present", check_sameas_present,
         (pages_data, thresholds_for("entity.sameas_present", registry), accepted, link_index)),
        ("entity.sameas_authority", check_sameas_authority,
         (pages_data, authority_thresholds, accepted, link_index)),
        ("entity.opengraph_identity", check_opengraph_identity,
         (pages_data, thresholds_for("entity.opengraph_identity", registry))),
        ("entity.name_consistency", check_name_consistency,
         (pages_data, accepted, thresholds_for("entity.name_consistency", registry))),
    ):
        try:
            out.append(fn(*args))
        except Exception as exc:
            log.warning("%s failed: %s", check_id, exc)
            out.append(result(check_id, "unknown", page_url=pages_data[0][0],
                              reason=f"analyzer error: {type(exc).__name__}"))
    return finalize(out, pages_data[0][0])


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
