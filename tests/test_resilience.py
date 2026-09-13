"""Resilience suite — hostile and malformed inputs across the acquisition and analysis layers.

Written after an adversarial probe found three real defects:

  1. ORCHESTRATOR CRASH — on an internal check error, an analyzer emitted the FUNCTION NAME as
     `check_id`. That id is not in the registry, so `_scoring.resolve_states` raised
     ScoringConfigError and the entire audit died. The genuine check also vanished silently.
  2. CRASH ON MALFORMED ARTIFACT — a `pages` value that was a dict rather than a list made every
     analyzer raise AttributeError (iterating a dict yields its string keys).
  3. FALSE NEGATIVES — wrongly-typed fields (raw.html as an int, geometry.headings as None, a
     heading `top` as a string) silently converted real checks into `unknown`.

The guarantee these tests pin: **an analyzer always emits exactly its declared checks, every id
valid, whatever it is fed.** That is what makes the orchestrator safe to build on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
for sub in ("audit-orchestrator", "crawl-render-extraction-audit", "entity-corroboration-audit",
            "quotability-density-audit", "engagement-orientation-audit"):
    sys.path.insert(0, str(REPO_ROOT / "skills" / sub / "scripts"))

import _page_select as PS  # noqa: E402
import _safe_fetch as SF  # noqa: E402
import _scoring as S  # noqa: E402
import crawl_render_extract as CRE  # noqa: E402
import engagement_orientation as ENG  # noqa: E402
import entity_corroboration as ENT  # noqa: E402
import quotability_density as QD  # noqa: E402

ANALYZERS = [CRE, ENT, QD, ENG]
RESULT_SCHEMA = json.loads(
    (REPO_ROOT / "skills" / "audit-orchestrator" / "references" / "check-result-schema.json")
    .read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw_registry():
    data = json.loads((REPO_ROOT / "config" / "checks.json").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["checks"]}


@pytest.fixture(scope="module")
def registry():
    return S.load_registry()


@pytest.fixture(scope="module")
def config():
    return S.load_config()


def art(html="<html lang='en'><body><p>x</p></body></html>", **kw):
    page = {"url": "https://e.test/", "role": "homepage", "status": "ok",
            "raw": {"status": 200, "content_type": "text/html", "html": html}}
    page.update(kw.pop("page", {}))
    return {"requested_url": "https://e.test/", "pages": [page],
            "language": {"detected": "en", "source": "html_lang", "supported": True},
            "ai_crawlers": {"determinable": True, "allowed": {}, "blocked": []}, **kw}


# --- The core contract ---------------------------------------------------------------------------
MALFORMED_ARTIFACTS = {
    "pages is a dict": {"pages": {"url": "x"}},
    "pages is None": {"pages": None},
    "pages is a string": {"pages": "nope"},
    "pages holds strings": {"pages": ["a", "b"]},
    "artifact is None": None,
    "artifact is a list": [],
    "artifact is a string": "not an artifact",
    "empty artifact": {},
    "raw missing": {"pages": [{"url": "u", "role": "homepage", "status": "ok"}]},
    "raw is a list": {"pages": [{"url": "u", "role": "homepage", "status": "ok", "raw": []}]},
    "html is an int": {"pages": [{"url": "u", "role": "homepage", "status": "ok",
                                  "raw": {"status": 200, "html": 12345}}]},
    "html is None": {"pages": [{"url": "u", "role": "homepage", "status": "ok",
                                "raw": {"status": 200, "html": None}}]},
    "language is None": {**art(), "language": None},
    "language is a list": {**art(), "language": []},
    "ai_crawlers is a list": {**art(), "ai_crawlers": []},
}


@pytest.mark.parametrize("label", sorted(MALFORMED_ARTIFACTS))
@pytest.mark.parametrize("module", ANALYZERS, ids=lambda m: m.__name__)
def test_malformed_artifact_never_crashes(module, label, raw_registry):
    module.analyze(MALFORMED_ARTIFACTS[label], raw_registry)


@pytest.mark.parametrize("label", sorted(MALFORMED_ARTIFACTS))
@pytest.mark.parametrize("module", ANALYZERS, ids=lambda m: m.__name__)
def test_always_emits_exactly_its_declared_checks(module, label, raw_registry):
    results = module.analyze(MALFORMED_ARTIFACTS[label], raw_registry)
    assert [r["check_id"] for r in results] == module.CHECKS


@pytest.mark.parametrize("label", sorted(MALFORMED_ARTIFACTS))
@pytest.mark.parametrize("module", ANALYZERS, ids=lambda m: m.__name__)
def test_output_always_schema_valid(module, label, raw_registry):
    Draft202012Validator(RESULT_SCHEMA).validate(
        module.analyze(MALFORMED_ARTIFACTS[label], raw_registry))


@pytest.mark.parametrize("label", sorted(MALFORMED_ARTIFACTS))
def test_scoring_engine_accepts_output_from_malformed_input(label, registry, config, raw_registry):
    """The orchestration guarantee: whatever the analyzers are fed, the orchestrator can score it.

    This is the test that would have caught the function-name-as-check_id defect.
    """
    rows = []
    for module in ANALYZERS:
        rows.extend(module.analyze(MALFORMED_ARTIFACTS[label], raw_registry))
    results = [S.CheckResult(check_id=r["check_id"], state=r["state"], reason=r["reason"])
               for r in rows]
    resolved = S.resolve_states(results, registry, config)   # raises on an unknown id
    assert len(resolved) == len(registry.checks)


def test_internal_check_error_reports_against_the_real_check(raw_registry, monkeypatch):
    """An exception inside one check must not invent a check id, nor lose the real one."""
    def boom(*a, **k):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(CRE, "check_semantic_html", boom)
    results = CRE.analyze(art(), raw_registry)

    assert [r["check_id"] for r in results] == CRE.CHECKS
    row = next(r for r in results if r["check_id"] == "extraction.semantic_html")
    assert row["state"] == "unknown"
    assert "analyzer error" in row["reason"]
    # The exception message itself is NOT leaked into the report.
    assert "simulated internal failure" not in (row["reason"] or "")


def test_every_check_id_belongs_to_the_registry(raw_registry):
    for module in ANALYZERS:
        for row in module.analyze(art(), raw_registry):
            assert row["check_id"] in raw_registry


# --- Geometry from a hostile page -----------------------------------------------------------------
BAD_GEOMETRY = {
    "geometry is a list": [],
    "geometry is a string": "nope",
    "fields are None": {"viewport_height": None, "body_font_px": None, "headings": None,
                        "interactive": None, "overlays": None, "above_fold_text": None},
    "wrong scalar types": {"viewport_height": "800", "body_font_px": "big",
                           "headings": "no", "interactive": {}, "overlays": 5},
    "top is a string": {"viewport_height": 800,
                        "headings": [{"tag": "h1", "top": "120", "text": "T"}],
                        "interactive": [], "overlays": []},
    "rows are not dicts": {"viewport_height": 800, "headings": ["h1", 2, None],
                           "interactive": [[]], "overlays": [None]},
    "booleans as numbers": {"viewport_height": True, "body_font_px": False,
                            "headings": [], "interactive": [], "overlays": []},
    "coverage is a string": {"viewport_height": 800, "headings": [], "interactive": [],
                             "overlays": [{"tag": "div", "coverage": "lots"}]},
}


@pytest.mark.parametrize("label", sorted(BAD_GEOMETRY))
def test_hostile_geometry_never_crashes(label, raw_registry):
    artifact = art(page={"rendered": {"available": True, "mode": "playwright",
                                      "html": "<html lang='en'><body><h1>x</h1></body></html>",
                                      "geometry": BAD_GEOMETRY[label]}})
    results = ENG.analyze(artifact, raw_registry)
    assert [r["check_id"] for r in results] == ENG.CHECKS
    Draft202012Validator(RESULT_SCHEMA).validate(results)


def test_safe_geometry_normalizes_types():
    page = {"rendered": {"available": True, "geometry": {
        "viewport_height": "800", "body_font_px": 16, "headings": [{"tag": "h1", "top": "x"}],
        "interactive": "no", "overlays": None, "above_fold_text": 42}}}
    geo = ENG.safe_geometry(page)
    assert geo["viewport_height"] is None      # a string is not a usable pixel value
    assert geo["body_font_px"] == 16
    assert geo["headings"][0]["top"] is None
    assert geo["interactive"] == [] and geo["overlays"] == []
    assert geo["above_fold_text"] == ""


def test_booleans_are_not_treated_as_numbers():
    """bool is a subclass of int in Python; a True viewport height must not become 1px."""
    page = {"rendered": {"available": True,
                         "geometry": {"viewport_height": True, "body_font_px": False}}}
    geo = ENG.safe_geometry(page)
    assert geo["viewport_height"] is None
    assert geo["body_font_px"] is None


# --- Hostile HTML / structured data -----------------------------------------------------------------
HOSTILE_HTML = {
    "empty": "",
    "no body": "<html><head><title>t</title></head></html>",
    "deep nesting": "<div>" * 2000 + "x" + "</div>" * 2000,
    "unclosed tags": "<div>" * 5000,
    "10k images": "<html><body>" + "".join(f"<img src='p{i}.png'>" for i in range(10000)) + "</body></html>",
    "no punctuation": "<html lang='en'><body><p>" + ("word " * 5000) + "</p></body></html>",
    "nul and control chars": "<html lang='en'><body><p>a" + chr(0) + chr(7) + "b</p></body></html>",
    "type is a dict": '<html><head><script type="application/ld+json">{"@type":{"a":1},"name":"X"}</script></head><body>x</body></html>',
    "name is a dict": '<html><head><script type="application/ld+json">{"@type":"Organization","name":{"@value":"X"}}</script></head><body>x</body></html>',
    "sameAs is a dict": '<html><head><script type="application/ld+json">{"@type":"Organization","name":"X","sameAs":{"a":"b"}}</script></head><body>x</body></html>',
    "sameAs non-strings": '<html><head><script type="application/ld+json">{"@type":"Organization","name":"X","sameAs":[1,null,{"a":1}]}</script></head><body>x</body></html>',
    "jsonld top-level null": '<html><head><script type="application/ld+json">null</script></head><body>x</body></html>',
    "jsonld is a string": '<html><head><script type="application/ld+json">"hello"</script></head><body>x</body></html>',
    "numeric entity name": '<html><head><script type="application/ld+json">{"@type":"Organization","name":12345}</script></head><body>x</body></html>',
}


@pytest.mark.parametrize("label", sorted(HOSTILE_HTML))
@pytest.mark.parametrize("module", ANALYZERS, ids=lambda m: m.__name__)
def test_hostile_html_never_crashes(module, label, raw_registry):
    results = module.analyze(art(HOSTILE_HTML[label]), raw_registry)
    assert [r["check_id"] for r in results] == module.CHECKS
    Draft202012Validator(RESULT_SCHEMA).validate(results)


# --- Sitemap / XML attacks ---------------------------------------------------------------------------
XML_ATTACKS = {
    "billion laughs": b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">
      <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
      <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
      <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">]>
      <urlset><url><loc>&lol4;</loc></url></urlset>""",
    "external entity": b"""<?xml version="1.0"?><!DOCTYPE foo [
      <!ENTITY xxe SYSTEM "file:///etc/passwd">]><urlset><url><loc>&xxe;</loc></url></urlset>""",
    "not xml": b"<html>404 page</html>",
    "truncated gzip": b"\x1f\x8b\x08\x00 truncated",
    "empty loc": b"<?xml version='1.0'?><urlset><url><loc></loc></url></urlset>",
    "nul bytes": b"<?xml version='1.0'?><urlset><url><loc>https://e.test/\x00x</loc></url></urlset>",
}


@pytest.mark.parametrize("label", sorted(XML_ATTACKS))
def test_xml_attacks_degrade_silently(label, config):
    """A malicious or broken sitemap must yield nothing, never an exception or a file read."""
    pages, children = PS.parse_sitemap(XML_ATTACKS[label], config)
    assert isinstance(pages, list) and isinstance(children, list)
    for url in pages:
        assert "root:" not in url and "/etc/passwd" not in url


def test_huge_sitemap_is_capped(config):
    xml = ("<?xml version='1.0'?><urlset"
           + "".join(f"<url><loc>https://e.test/p{i}</loc></url>" for i in range(50000))
           + "</urlset>").encode()
    pages, _ = PS.parse_sitemap(xml, config)
    assert len(pages) <= int(config["page_selection"]["max_sitemap_urls_scanned"])


# --- URL handling -------------------------------------------------------------------------------------
HOSTILE_URLS = [
    "https://e.test:99999/", "//protocol-relative.test/", "https:///triple",
    "https://user@:80/", "https://:pass@e.test/", "http://e.test\\@evil.com/",
    "https://e.test/%00", "https://" + "a" * 5000 + ".test/", "not-a-url-at-all",
    "https://e.test/\n\rX-Injected: 1", "", "http://[::1]/", "https://e.test:0/",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_safe_get_never_raises(url, config):
    """safe_get is the orchestrator's boundary: it must ALWAYS return a result, never raise."""
    result = SF.safe_get(url, config)
    assert result.ok is False
    assert result.error_kind is not None


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_validate_url_raises_only_the_documented_error(url, config):
    """validate_url may reject, but only via UnsafeURLError — anything else would escape safe_get."""
    try:
        SF.validate_url(url, config)
    except SF.UnsafeURLError:
        pass
    except Exception as exc:
        pytest.fail(f"unexpected {type(exc).__name__} for {url!r}: {exc}")


def test_page_selection_survives_hostile_nav(config):
    cases = ["<nav><a href='javascript:alert(1)'>x</a></nav>",
             "<nav><a href='//evil.test/x'>x</a></nav>",
             "<nav><a href='   '>x</a></nav>",
             "<nav>" + "".join(f"<a href='/p{i}'>x</a>" for i in range(5000)) + "</nav>"]
    for html in cases:
        pages = PS.select_pages("https://e.test/", html, config)
        assert pages[0].role == "homepage"
        assert len(pages) <= int(config["page_selection"]["max_pages"])
        assert all(p.url.startswith("http") for p in pages)


def test_page_selection_survives_bad_base_url(config):
    pages = PS.select_pages("not-a-url", "<nav><a href='/x'>y</a></nav>", config)
    assert len(pages) >= 1


# --- Homepage anchoring ---------------------------------------------------------------------------
# A blocked or failed homepage must never be papered over with another page's verdicts. This was a
# real false negative: a consent wall on the homepage scored 58.1 at 0.805 coverage, because the
# analyzers silently graded /pricing instead. It now reads 0.0 at 0.08 coverage with the real reason.

HOMEPAGE_AUTHORITATIVE = [CRE, QD, ENG]   # ENT deliberately excluded: it accepts any-page evidence


def _artifact_with_blocked_homepage(kind="consent_wall"):
    return {"requested_url": "https://e.test/",
            "language": {"detected": "en", "source": "html_lang", "supported": True},
            "ai_crawlers": {"determinable": True, "allowed": {}, "blocked": []},
            "pages": [
                {"url": "https://e.test/", "role": "homepage", "status": "blocked",
                 "blocked_kind": kind,
                 "raw": {"status": 200, "html": "<html lang='en'><body>We use cookies</body></html>"}},
                {"url": "https://e.test/pricing", "role": "other", "status": "ok",
                 "raw": {"status": 200,
                         "html": "<html lang='en'><head><title>A perfectly fine pricing page</title>"
                                 "</head><body><main><h1>Pricing</h1><p>Plans from $10 per month "
                                 "for teams of any size, billed annually.</p></main></body></html>"}},
            ]}


@pytest.mark.parametrize("module", HOMEPAGE_AUTHORITATIVE, ids=lambda m: m.__name__)
def test_blocked_homepage_is_not_masked_by_another_page(module, raw_registry):
    results = module.analyze(_artifact_with_blocked_homepage(), raw_registry)
    assert all(r["state"] == "unknown" for r in results), \
        "a healthy secondary page must not stand in for a blocked homepage"
    assert all("consent_wall" in (r["reason"] or "") for r in results)


@pytest.mark.parametrize("module", HOMEPAGE_AUTHORITATIVE, ids=lambda m: m.__name__)
def test_errored_homepage_reports_its_real_reason(module, raw_registry):
    artifact = _artifact_with_blocked_homepage()
    artifact["pages"][0] = {"url": "https://e.test/", "role": "homepage", "status": "error",
                            "skip_reason": "content_type"}
    results = module.analyze(artifact, raw_registry)
    assert all(r["state"] == "unknown" for r in results)
    assert any("content_type" in (r["reason"] or "") for r in results)


def test_entity_analyzer_still_uses_any_page(raw_registry):
    """Entity markup legitimately lives on /about, so this analyzer is deliberately NOT anchored."""
    artifact = _artifact_with_blocked_homepage()
    artifact["pages"][1]["raw"]["html"] = (
        '<html lang="en"><head><script type="application/ld+json">'
        '{"@type":"Organization","name":"Acme"}</script></head><body>x</body></html>')
    states = {r["check_id"]: r["state"] for r in ENT.analyze(artifact, raw_registry)}
    assert states["entity.organization_declared"] == "pass"


def test_homepage_identified_by_role_not_position(raw_registry):
    """Page order is not guaranteed; the homepage is whichever page carries role='homepage'."""
    artifact = _artifact_with_blocked_homepage()
    artifact["pages"].reverse()          # homepage now last
    results = CRE.analyze(artifact, raw_registry)
    assert all("consent_wall" in (r["reason"] or "") for r in results)
