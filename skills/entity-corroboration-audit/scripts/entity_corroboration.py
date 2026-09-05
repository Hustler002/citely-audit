#!/usr/bin/env python3
"""entity-corroboration-audit (mechanic 4) — can an AI tell WHO this page is about, unambiguously?

Pure consumer of the crawl artifact. ZERO network access, no exceptions. The optional Wikidata
lookup lives in the orchestrator's fetch stage (left of the artifact boundary) and arrives here
pre-fetched as `external_corroboration`.

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

MAX_EVIDENCE = 300
MAX_JSONLD_BYTES = 512 * 1024      # a single block larger than this is not legitimate markup
MAX_JSONLD_DEPTH = 20              # bounds traversal on adversarial nesting

_WS_RE = re.compile(r"\s+")

DEFAULT_THRESHOLDS = {
    "entity.organization_declared": {
        "accepted_types": ["Organization", "Person", "LocalBusiness", "Corporation", "NGO"]},
    "entity.sameas_present": {"min_links": 1},
    "entity.sameas_authority": {"authority_domains": [
        "wikidata.org", "wikipedia.org", "crunchbase.com", "linkedin.com",
        "github.com", "ror.org", "isni.org"]},
    "entity.opengraph_identity": {"required_properties": ["og:title", "og:type", "og:url"]},
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


def find_entities(blocks: list, accepted: list) -> list:
    accepted_lower = {a.lower() for a in accepted}
    found = []
    for block in blocks:
        for node in _flatten(block):
            if not isinstance(node, dict):
                continue
            if any(t.lower() in accepted_lower for t in _types_of(node)):
                found.append(node)
    return found


def has_microdata(html: str) -> bool:
    soup = soup_of(html)
    return bool(soup and soup.find(attrs={"itemscope": True}))


# --- checks -----------------------------------------------------------------------------------
def check_structured_data_present(pages_data) -> dict:
    for url, blocks, og, micro, _html in pages_data:
        if blocks:
            return result("entity.structured_data_present", "pass", measurement=len(blocks),
                          page_url=url, selector='script[type="application/ld+json"]',
                          evidence=f"{len(blocks)} JSON-LD block(s) found")
    for url, _b, og, micro, _html in pages_data:
        if micro or og:
            kind = "microdata" if micro else "Open Graph"
            return result("entity.structured_data_present", "partial", measurement=kind,
                          page_url=url,
                          evidence=f"No JSON-LD, but {kind} markup is present")
    url = pages_data[0][0] if pages_data else None
    return result("entity.structured_data_present", "fail", measurement=0, page_url=url,
                  evidence="No JSON-LD, microdata or Open Graph markup found on any audited page")


def check_organization_declared(pages_data, thresholds) -> dict:
    accepted = thresholds.get("accepted_types", DEFAULT_THRESHOLDS[
        "entity.organization_declared"]["accepted_types"])
    for url, blocks, _og, _micro, _html in pages_data:
        entities = find_entities(blocks, accepted)
        if entities:
            names = [e.get("name") for e in entities if e.get("name")]
            types = sorted({t for e in entities for t in _types_of(e)})
            return result("entity.organization_declared", "pass",
                          measurement=", ".join(types), page_url=url,
                          selector='script[type="application/ld+json"]',
                          evidence=f"Declared entity type(s) {', '.join(types)}"
                                   + (f", name: {names[0]}" if names else ""))
    url = pages_data[0][0] if pages_data else None
    return result("entity.organization_declared", "fail", measurement=0, page_url=url,
                  evidence=f"No entity of an accepted identity type ({', '.join(accepted)}) declared")


def _sameas_of(entities) -> list:
    links = []
    for e in entities:
        raw = e.get("sameAs") or e.get("sameas") or []
        if isinstance(raw, str):
            raw = [raw]
        links.extend([str(x) for x in raw if x])
    return links


def check_sameas_present(pages_data, thresholds, accepted) -> dict:
    minimum = int(thresholds.get("min_links", 1))
    for url, blocks, _og, _micro, _html in pages_data:
        entities = find_entities(blocks, accepted)
        links = _sameas_of(entities)
        if len(links) >= minimum:
            return result("entity.sameas_present", "pass", measurement=len(links), page_url=url,
                          evidence=f"{len(links)} sameAs link(s): {', '.join(links[:3])}")
        if entities:
            return result("entity.sameas_present", "fail", measurement=len(links), page_url=url,
                          selector='script[type="application/ld+json"]',
                          evidence="Entity declared but carries no sameAs cross-references, "
                                   "so its identity cannot be corroborated elsewhere on the web")
    url = pages_data[0][0] if pages_data else None
    return result("entity.sameas_present", "unknown", page_url=url,
                  reason="no declared entity to carry sameAs")


def check_sameas_authority(pages_data, thresholds, accepted) -> dict:
    domains = [d.lower() for d in thresholds.get("authority_domains", DEFAULT_THRESHOLDS[
        "entity.sameas_authority"]["authority_domains"])]
    saw_links = False
    for url, blocks, _og, _micro, _html in pages_data:
        links = _sameas_of(find_entities(blocks, accepted))
        if not links:
            continue
        saw_links = True
        matched = []
        for link in links:
            host = (urlparse(link).hostname or "").lower()
            if any(host == d or host.endswith("." + d) for d in domains):
                matched.append(link)
        if matched:
            return result("entity.sameas_authority", "pass", measurement=len(matched),
                          page_url=url,
                          evidence=f"sameAs points to authority node(s): {', '.join(matched[:3])}")
        return result("entity.sameas_authority", "fail", measurement=0, page_url=url,
                      evidence=f"sameAs links exist but none reach a recognised authority "
                               f"({', '.join(domains[:4])}…), leaving the identity chain unanchored")
    url = pages_data[0][0] if pages_data else None
    return result("entity.sameas_authority", "unknown", page_url=url,
                  reason="no sameAs links to evaluate" if not saw_links else "not evaluated")


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


def check_name_consistency(pages_data, accepted) -> dict:
    """Contradictory names actively teach an AI the wrong association — worse than no data."""
    for url, blocks, og, _micro, html in pages_data:
        entities = find_entities(blocks, accepted)
        names = {str(e["name"]).strip() for e in entities if e.get("name")}
        if not names:
            continue

        site_name = (og.get("og:site_name") or "").strip()
        candidates = set(names)
        if site_name:
            candidates.add(site_name)

        normalized = {_WS_RE.sub(" ", c).strip().lower() for c in candidates if c}
        if len(normalized) <= 1:
            return result("entity.name_consistency", "pass", measurement=len(normalized),
                          page_url=url,
                          evidence=f"Entity name stated consistently as: {sorted(candidates)[0]}")
        return result("entity.name_consistency", "fail", measurement=len(normalized),
                      page_url=url, selector='script[type="application/ld+json"]',
                      evidence=f"Conflicting entity names across markup: {sorted(candidates)}")
    url = pages_data[0][0] if pages_data else None
    return result("entity.name_consistency", "unknown", page_url=url,
                  reason="no named entity to compare")


# --- input handling ---------------------------------------------------------------------------
def artifact_from_html_file(path: str) -> dict:
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    return {"requested_url": f"file://{path}",
            "pages": [{"url": f"file://{path}", "role": "homepage", "status": "ok",
                       "raw": {"status": 200, "html": html, "byte_size": len(html),
                               "content_type": "text/html", "html_sha256": ""}}]}


def analyze(artifact: dict, registry: dict) -> list:
    checks = ["entity.structured_data_present", "entity.organization_declared",
              "entity.sameas_present", "entity.sameas_authority",
              "entity.opengraph_identity", "entity.name_consistency"]

    if artifact.get("blocked_before_fetch"):
        return [result(c, "unknown", reason="blocked_before_fetch") for c in checks]

    pages = artifact.get("pages") or []
    usable = [p for p in pages if p.get("status") == "ok" and (p.get("raw") or {}).get("html")]
    if not usable:
        reason = "no page could be read"
        if pages and pages[0].get("blocked_kind"):
            reason = f"page blocked: {pages[0]['blocked_kind']}"
        return [result(c, "unknown", reason=reason,
                       page_url=pages[0].get("url") if pages else None) for c in checks]

    # Prefer the rendered DOM when available: entity markup is sometimes injected by tag managers.
    pages_data = []
    for p in usable:
        rendered = p.get("rendered") or {}
        html = rendered.get("html") if rendered.get("available") else None
        html = html or (p.get("raw") or {}).get("html") or ""
        pages_data.append((p.get("url"), extract_jsonld(html), extract_opengraph(html),
                           has_microdata(html), html))

    accepted = thresholds_for("entity.organization_declared", registry).get(
        "accepted_types", DEFAULT_THRESHOLDS["entity.organization_declared"]["accepted_types"])

    out = []
    for fn, args in (
        (check_structured_data_present, (pages_data,)),
        (check_organization_declared, (pages_data, thresholds_for("entity.organization_declared", registry))),
        (check_sameas_present, (pages_data, thresholds_for("entity.sameas_present", registry), accepted)),
        (check_sameas_authority, (pages_data, thresholds_for("entity.sameas_authority", registry), accepted)),
        (check_opengraph_identity, (pages_data, thresholds_for("entity.opengraph_identity", registry))),
        (check_name_consistency, (pages_data, accepted)),
    ):
        try:
            out.append(fn(*args))
        except Exception as exc:
            log.warning("%s failed: %s", fn.__name__, exc)
            out.append(result(fn.__name__, "unknown", reason=f"analyzer error: {exc}"))
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
