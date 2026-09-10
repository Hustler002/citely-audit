#!/usr/bin/env python3
"""Structural, language-neutral page selection.

Picks which pages an audit covers: the homepage always, then a small sample of additional pages.

Selection is STRUCTURAL, never lexical. PLAN.md §8 forbids assuming URL conventions, so we do not
look for /about, /faq, /contact or any other word. Those matches are English-only and would silently
degrade every non-English site to homepage-only — the same class of bug as the English-only content
heuristics. Instead we rank by two signals that behave identically in any language:

  1. Membership of the homepage's primary navigation (the site's own statement of what matters).
  2. Shallow URL path depth (/pricing outranks /blog/2024/03/some-post).

Sitemap.xml is a FALLBACK, used only when navigation yields too few candidates, and it degrades
silently: a missing, malformed, gzipped, oversized or index-only sitemap must never fail the audit.

Consequently pages are not assigned semantic roles. Everything except the homepage is recorded as
`other`, because guessing "this is the about page" from a URL is exactly the lexical assumption we
are avoiding.
"""
from __future__ import annotations

import gzip
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

log = logging.getLogger("page_select")

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass
class PageCandidate:
    url: str
    role: str            # "homepage" | "other"
    source: str          # "homepage" | "nav" | "sitemap"
    depth: int = 0


# --- URL helpers ------------------------------------------------------------------------------
def normalize_url(url: str) -> str:
    """Drop the fragment and any trailing empty query so dedup is reliable."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    path = p.path or "/"
    return urlunparse((p.scheme, p.netloc, path, p.params, p.query, ""))


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def same_site(a: str, b: str) -> bool:
    """Same host, ignoring a leading www.

    Deliberately conservative: subdomains are treated as different sites. blog.example.com is often
    a separate stack with separate markup, and wandering onto it would make the audit's scope
    unpredictable on unseen sites.
    """
    ha, hb = _host(a), _host(b)
    return bool(ha) and ha == hb


def path_depth(url: str) -> int:
    try:
        segments = [s for s in (urlparse(url).path or "/").split("/") if s]
    except ValueError:
        return 99
    return len(segments)


def _excluded_extension(url: str, config: dict) -> bool:
    exts = config.get("page_selection", {}).get("exclude_extensions", [])
    try:
        path = (urlparse(url).path or "").lower()
    except ValueError:
        return True
    return any(path.endswith(ext) for ext in exts)


def is_auditable(url: str, homepage_url: str, config: dict) -> bool:
    """Same site, http(s), not an asset, within depth budget."""
    ps = config.get("page_selection", {})
    try:
        scheme = urlparse(url).scheme
    except ValueError:
        return False
    if scheme not in ("http", "https"):
        return False
    if not same_site(url, homepage_url):
        return False
    if _excluded_extension(url, config):
        return False
    return path_depth(url) <= int(ps.get("max_path_depth", 2))


# --- Navigation extraction --------------------------------------------------------------------
def extract_nav_links(html: str, base_url: str, config: dict) -> list:
    """Links inside the page's primary navigation, in document order.

    Falls back to all in-page links when no navigation landmark exists, so template-less or
    unusual markup still yields candidates instead of nothing.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover - dependency is pinned
        return []

    ps = config.get("page_selection", {})
    selectors = ps.get("nav_selectors", ["nav", "header nav", "[role=navigation]", "header"])

    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html or "", "html.parser")
        except Exception:
            return []

    anchors = []
    for selector in selectors:
        try:
            for container in soup.select(selector):
                anchors.extend(container.find_all("a", href=True))
        except Exception:
            continue
        if anchors:
            break

    if not anchors:  # no nav landmark — fall back to the whole document
        try:
            anchors = soup.find_all("a", href=True)
        except Exception:
            anchors = []

    out = []
    seen = set()
    for a in anchors:
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


# --- Sitemap parsing --------------------------------------------------------------------------
def parse_sitemap(raw: bytes, config: dict) -> tuple:
    """Return (page_urls, child_sitemap_urls). Never raises — a bad sitemap yields ([], []).

    Handles gzip, urlset, and sitemapindex. Size-capped before parsing so an enormous sitemap
    cannot exhaust memory.
    """
    ps = config.get("page_selection", {})
    max_bytes = int(ps.get("max_sitemap_bytes", 2 * 1024 * 1024))
    max_urls = int(ps.get("max_sitemap_urls_scanned", 500))

    if not raw:
        return [], []

    if raw[:2] == b"\x1f\x8b":  # gzip magic
        try:
            raw = gzip.decompress(raw)
        except Exception as exc:
            log.warning("sitemap gzip decompression failed: %s", exc)
            return [], []

    if len(raw) > max_bytes:
        raw = raw[:max_bytes]  # truncate; a partial parse is better than none

    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        log.warning("sitemap parse failed: %s", exc)
        return [], []

    pages, children = [], []
    tag = root.tag.lower()

    if tag.endswith("sitemapindex"):
        for sm in root.iter():
            if sm.tag.lower().endswith("loc") and sm.text:
                children.append(sm.text.strip())
    else:
        for loc in root.iter():
            if loc.tag.lower().endswith("loc") and loc.text:
                pages.append(loc.text.strip())
                if len(pages) >= max_urls:
                    break

    return pages, children


def collect_sitemap_urls(sitemap_urls: list, config: dict, fetch) -> list:
    """Fetch sitemaps (following an index one level) and return candidate page URLs."""
    ps = config.get("page_selection", {})
    max_children = int(ps.get("max_sitemap_index_children", 3))
    follow_index = bool(ps.get("follow_sitemap_index", True))

    collected = []
    for sm_url in sitemap_urls[:max_children or 1]:
        result = fetch(sm_url)
        if result is None or getattr(result, "error", None) or not getattr(result, "body", b""):
            continue
        pages, children = parse_sitemap(result.body, config)
        collected.extend(pages)
        if follow_index and children and not pages:
            for child in children[:max_children]:
                child_result = fetch(child)
                if child_result is None or getattr(child_result, "error", None):
                    continue
                child_pages, _ = parse_sitemap(child_result.body, config)
                collected.extend(child_pages)
    return collected


# --- Selection --------------------------------------------------------------------------------
def select_pages(homepage_url: str, homepage_html: str, config: dict, *,
                 fetch=None, robots_sitemaps=None) -> list:
    """Choose up to max_pages pages, homepage first.

    Ranking is purely structural: navigation membership beats sitemap membership, then shallower
    paths win, then original document order. No word in any URL influences the outcome.
    """
    ps = config.get("page_selection", {})
    max_pages = int(ps.get("max_pages", 5))

    homepage_url = normalize_url(homepage_url)
    selected = [PageCandidate(url=homepage_url, role="homepage", source="homepage",
                              depth=path_depth(homepage_url))]
    if max_pages <= 1:
        return selected

    seen = {homepage_url}
    candidates = []

    if ps.get("prefer_nav_links", True):
        for order, url in enumerate(extract_nav_links(homepage_html, homepage_url, config)):
            if url in seen or not is_auditable(url, homepage_url, config):
                continue
            seen.add(url)
            candidates.append((0, path_depth(url), order, url, "nav"))

    need_more = len(candidates) < (max_pages - 1)
    if need_more and ps.get("sitemap_fallback", True) and fetch is not None:
        try:
            sitemaps = list(robots_sitemaps or [])
            if not sitemaps:
                parsed = urlparse(homepage_url)
                sitemaps = [urlunparse((parsed.scheme, parsed.netloc, "/sitemap.xml", "", "", ""))]
            for order, url in enumerate(collect_sitemap_urls(sitemaps, config, fetch)):
                url = normalize_url(url)
                if url in seen or not is_auditable(url, homepage_url, config):
                    continue
                seen.add(url)
                candidates.append((1, path_depth(url), order, url, "sitemap"))
        except Exception as exc:  # degrade silently to whatever we already have
            log.warning("sitemap discovery failed, continuing: %s", exc)

    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    for _src_rank, depth, _order, url, source in candidates[: max_pages - 1]:
        selected.append(PageCandidate(url=url, role="other", source=source, depth=depth))

    return selected
