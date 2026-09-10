"""Entity Trust must work for the average website, not for the sites we happened to audit.

Every site we had tested was a large tech or advocacy organisation, and the category had quietly
specialised to them. Auditing eff.org surfaced the symptom: entity_trust 58.9 at 0.56 coverage —
and github.com and a small dental practice returned exactly the same two numbers. They were a
FIXED POINT, forced by arithmetic:

    (22*0.5 + 24*0.5 + 10*1.0) / (22 + 24 + 10) = 58.9      56/100 = 0.56

Any site with Open Graph and no JSON-LD landed there, whatever it actually published. The number
carried no information about the site at all.

Underneath were five detection defects, each found by profiling ten real sites rather than by
reasoning about one:

  1. `sameAs` was read only from a JSON-LD node, so a page linking to its own profiles reported
     `unknown` — "we could not tell" — about something plainly visible.
  2. Identity types were a five-name allowlist. schema.org has roughly 200 LocalBusiness subtypes,
     so a real dental practice publishing a correct `Dentist` node with sameAs scored 39.3, worse
     than a site with no markup whatsoever.
  3. Names were compared whole, so "Bakeshop" and the same bakery's own
     "Bakeshop | NE Portland Retail and Wholesale Bakery" were reported as conflicting brands.
  4. Every entity in the graph was a naming candidate, so the web agency that built a bakery's site
     was read as a rival brand and failed a high-severity check.
  5. Open Graph paid out three times across 46 points of critical weight, which is what pinned the
     category to its fixed point.

These tests are written against SHAPES, not sites. The fixtures they use are archetypes precisely
so that fixing one site cannot break another.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for sub in ("entity-corroboration-audit", "audit-orchestrator"):
    sys.path.insert(0, str(REPO_ROOT / "skills" / sub / "scripts"))

import _scoring as S  # noqa: E402
import entity_corroboration as ENT  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
REGISTRY = {c["id"]: c
            for c in json.loads((REPO_ROOT / "config" / "checks.json").read_text(encoding="utf-8"))["checks"]}
WEIGHTS = {c: REGISTRY[c]["weight"] for c in ENT.CHECKS}
CREDIT = {"pass": 1.0, "partial": 0.5, "fail": 0.0}

STRUCT = "entity.structured_data_present"
ORG = "entity.organization_declared"
SAME = "entity.sameas_present"
AUTH = "entity.sameas_authority"
NAME = "entity.name_consistency"


def artifact_for_fixture(name):
    """Build an artifact carrying the fixture's REAL url, since brand tokens derive from it."""
    html = (FIXTURES / name).read_text(encoding="utf-8")
    match = re.search(r'og:url" content="([^"]+)"', html)
    url = match.group(1) if match else "https://example.test/"
    return {"requested_url": url,
            "pages": [{"url": url, "role": "homepage", "status": "ok",
                       "raw": {"status": 200, "html": html, "content_type": "text/html",
                               "byte_size": len(html), "html_sha256": ""}}]}


def states_for(name):
    return {r["check_id"]: r["state"] for r in ENT.analyze(artifact_for_fixture(name), REGISTRY)}


def evidence_for(name, check_id):
    rows = {r["check_id"]: r for r in ENT.analyze(artifact_for_fixture(name), REGISTRY)}
    return rows[check_id]["evidence"] or rows[check_id].get("reason") or ""


def category_score(name):
    st = states_for(name)
    scored = sum(WEIGHTS[c] for c in ENT.CHECKS if st[c] in CREDIT)
    credit = sum(WEIGHTS[c] * CREDIT[st[c]] for c in ENT.CHECKS if st[c] in CREDIT)
    return (round(credit / scored * 100, 1) if scored else 0.0, round(scored / 100, 3))


# =================================================================================================
# The small-business web
# =================================================================================================
def test_localbusiness_subtype_outside_the_allowlist_is_recognised():
    """A plumber marks up as `Plumber`, not `Organization`.

    schema.org has roughly 200 LocalBusiness subtypes. A five-name allowlist is wrong for almost
    all of them, and a real dental practice with a correct `Dentist` node and sameAs links scored
    39.3 out of 100 because of it.
    """
    st = states_for("entity_local_business.html")
    assert st[ORG] == "pass"
    assert st[SAME] == "pass"
    assert "Plumber" in evidence_for("entity_local_business.html", ORG)


def test_identity_is_recognised_without_the_type_being_listed_at_all():
    """The rule that actually generalises: a name plus real-world contact facts is an identity
    claim, whatever the node calls itself — including types invented after this was written."""
    node = {"@type": "ArtisanalCheesemonger", "name": "Pellworm Cheese",
            "telephone": "+49 4844 1122", "address": {"addressLocality": "Pellworm"}}
    found = ENT.find_identity_entities([node], {"accepted_types": ["Organization"]})
    assert [n.get("name") for n in found] == ["Pellworm Cheese"]


def test_a_page_title_node_is_not_an_identity():
    """`WebSite` and `WebPage` name a document, not an owner. Counting them created the name
    conflicts that failed correctly marked-up sites."""
    nodes = [{"@type": "WebSite", "name": "Acme | Widgets for everyone", "url": "https://a.test"},
             {"@type": "WebPage", "name": "About us"}]
    assert ENT.find_identity_entities(nodes, {"accepted_types": ["Organization"]}) == []


def test_a_directory_listing_anchors_identity_in_full():
    """A local business will never have a Wikidata entry. Its Google Business or Yelp record is
    the third-party anchor it actually can obtain, and grading only encyclopedias would fail the
    entire small-business web for something unobtainable."""
    st = states_for("entity_directory_listed.html")
    assert st[AUTH] == "pass"
    assert category_score("entity_directory_listed.html") == (100.0, 1.0)


def test_self_published_profiles_alone_are_partial_not_a_failure():
    """Real corroboration, but the brand controls both ends, so it cannot settle which Acme this
    is. Partial rather than fail, so a small shop with a Facebook page is not punished."""
    st = states_for("entity_non_english_business.html")
    assert st[SAME] == "pass"
    assert st[AUTH] == "partial"


def test_language_does_not_change_a_structural_verdict():
    """Entity markup is language-independent; a Spanish bakery must be graded like an English one."""
    spanish = states_for("entity_non_english_business.html")
    english = states_for("entity_local_business.html")
    for check in (STRUCT, ORG, SAME, NAME):
        assert spanish[check] == english[check], check


# =================================================================================================
# sameAs is a signal, not a JSON-LD property
# =================================================================================================
def test_profile_links_count_when_the_handle_does_not_match_the_domain():
    """Ravenswood Books links to @rbooksgower and /gowerstbooks. Neither resembles the domain, so a
    brand-token rule alone misses them; they are recognised by living in the footer instead."""
    st = states_for("entity_social_only.html")
    assert st[SAME] == "partial"
    assert st[AUTH] == "partial"


def test_declared_links_outrank_inferred_ones():
    """A `sameAs` array states that the profiles ARE the entity. A bare link only implies it."""
    assert states_for("entity_local_business.html")[SAME] == "pass"
    assert states_for("entity_social_only.html")[SAME] == "partial"


def test_rel_me_is_an_explicit_identity_claim():
    html = ('<html lang="en"><head><title>Wolde Ceramics</title>'
            '<link rel="me" href="https://mastodon.social/@wolde"></head><body>x</body></html>')
    artifact = {"requested_url": "https://wolde.example/",
                "pages": [{"url": "https://wolde.example/", "role": "homepage", "status": "ok",
                           "raw": {"status": 200, "html": html}}]}
    st = {r["check_id"]: r["state"] for r in ENT.analyze(artifact, REGISTRY)}
    assert st[SAME] == "pass"


def test_sameas_is_read_from_any_node_not_only_an_accepted_type():
    """The dental practice's links lived on a `Dentist` node and were invisible purely because of
    what the node called itself."""
    node = {"@type": "Dentist", "name": "MK Dental",
            "sameAs": ["https://www.facebook.com/mkdental"]}
    links = ENT.collect_identity_links("<html></html>", "https://mk.example/", {}, [node], [],
                                       ENT.DEFAULT_THRESHOLDS[AUTH])
    assert links["declared"] == ["https://www.facebook.com/mkdental"]


# =================================================================================================
# Precision: what must NOT count
# =================================================================================================
def test_share_widgets_are_not_identity_links():
    """Every CMS emits these. Counting them would hand a pass to essentially the whole web."""
    st = states_for("entity_share_widgets_only.html")
    assert st[SAME] == "fail"
    assert st[AUTH] == "fail"


def test_a_share_widget_in_the_footer_is_still_not_a_profile():
    """The discriminating case, and the reason the share rule exists at all.

    In the article body a share link is already excluded for being neither brand-named nor in
    chrome. Put the same widget in the footer, where identity links legitimately live, and only
    the share rule stands between it and a passing grade — so that is where it must be tested.
    """
    html = ('<html lang="en"><head><title>Kestrel Joinery</title>'
            '<meta property="og:site_name" content="Kestrel Joinery"></head><body>'
            "<main><p>Bespoke staircases.</p></main>"
            '<footer><a href="https://twitter.com/intent/tweet?url=https://kestrel.example/">'
            "Tweet this</a>"
            '<a href="https://www.facebook.com/sharer/sharer.php?u=https://kestrel.example/">'
            "Share</a></footer></body></html>")
    artifact = {"requested_url": "https://kestrel.example/",
                "pages": [{"url": "https://kestrel.example/", "role": "homepage", "status": "ok",
                           "raw": {"status": 200, "html": html}}]}
    st = {r["check_id"]: r["state"] for r in ENT.analyze(artifact, REGISTRY)}
    assert st[SAME] == "fail", "a share button was counted as the brand's own profile"


def test_an_article_that_merely_mentions_the_brand_is_not_a_profile():
    """`wiki.qt.io/Qt_for_Python` and a dated blog post were both claimed as python.org's own
    profiles, because the brand token appeared somewhere in the path."""
    tokens = {"python", "pythonorg"}
    assert not ENT._is_profile_path("/Qt_for_Python", tokens)
    assert not ENT._is_profile_path("/2026/09/inaugural-python-packaging-council.html", tokens)
    assert not ENT._is_profile_path("/python/cpython/issues", tokens)
    assert ENT._is_profile_path("/python", tokens)
    assert ENT._is_profile_path("/company/python", tokens)


def test_a_short_handle_suffix_is_allowed_but_a_whole_phrase_is_not():
    tokens = {"eff"}
    assert ENT._is_profile_path("/efforg", tokens)
    assert ENT._is_profile_path("/@eff", tokens)
    assert not ENT._is_profile_path("/effective-altruism-forum", tokens)


def test_generic_destinations_are_never_handles():
    assert not ENT._is_profile_path("/watch", {"acme"}, any_handle=True)
    assert not ENT._is_profile_path("/login", {"acme"}, any_handle=True)


# =================================================================================================
# Name comparison: the two false-positive classes
# =================================================================================================
def test_a_tagline_is_not_a_conflicting_name():
    """"Bakeshop" against the same bakery's "Bakeshop | NE Portland Retail and Wholesale Bakery"
    was reported as two conflicting brands, failing a high-severity check on correct markup."""
    assert ENT._names_agree("Bakeshop", "Bakeshop | NE Portland Retail and Wholesale Bakery")
    assert ENT._names_agree("GitHub", "GitHub · Change is constant")
    assert not ENT._names_agree("Initech Ltd", "Globex Corporation")


def test_the_agency_that_built_the_site_is_not_a_rival_brand():
    """A `Person` reached through `author` is the developer or the byline, not the business."""
    st = states_for("entity_agency_built.html")
    assert st[NAME] == "pass"
    assert st[ORG] == "pass"
    assert "Marchetti" in evidence_for("entity_agency_built.html", ORG)
    assert "BrightPixel" not in evidence_for("entity_agency_built.html", ORG)


def test_a_syndicated_authors_organization_is_not_the_sites_identity():
    """The discriminating case for the author rule.

    When the third party is a `Person`, it is already dropped for being a Person alongside an
    Organization. Make the third party an Organization — a wire service credited as `author` — and
    only the authorship rule can tell the two apart.
    """
    nodes = [{"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "@id": "https://ridgeline.example/#org",
         "name": "Ridgeline Outfitters", "telephone": "+1 406 555 0147",
         "address": {"addressLocality": "Bozeman"}},
        {"@type": "Organization", "@id": "https://newswire.example/#org",
         "name": "Cascade Newswire", "telephone": "+1 206 555 0110",
         "address": {"addressLocality": "Seattle"}},
        {"@type": "Article", "headline": "Winter stock has landed",
         "author": {"@id": "https://newswire.example/#org"}}]}]
    found = ENT.find_identity_entities(nodes, ENT.DEFAULT_THRESHOLDS[ORG])
    names = [n.get("name") for n in found]
    assert "Ridgeline Outfitters" in names
    assert "Cascade Newswire" not in names, "a credited author was read as the site's own identity"


def test_the_sites_own_organization_survives_being_an_articles_publisher():
    """Every major CMS points an Article's `publisher` at the site's own Organization. Treating
    that as a third-party reference deleted exactly the node the category looks for."""
    assert "publisher" not in ENT.THIRD_PARTY_KEYS
    assert states_for("entity_agency_built.html")[ORG] == "pass"


def test_genuinely_contradictory_names_still_fail():
    """The check must keep the failure it exists for."""
    html = ('<html><head><meta property="og:site_name" content="Globex Corporation">'
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Organization","name":"Initech Ltd"}'
            "</script></head><body>x</body></html>")
    artifact = {"requested_url": "https://e.test/",
                "pages": [{"url": "https://e.test/", "role": "homepage", "status": "ok",
                           "raw": {"status": 200, "html": html}}]}
    row = next(r for r in ENT.analyze(artifact, REGISTRY) if r["check_id"] == NAME)
    assert row["state"] == "fail"
    assert "Globex" in row["evidence"] and "Initech" in row["evidence"]


# =================================================================================================
# The fixed point is gone
# =================================================================================================
@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("entity_*.html")))
def test_every_archetype_is_fully_measured(name):
    """Coverage below 1.0 here means a check reported `unknown` for something it can measure."""
    _score, coverage = category_score(name)
    assert coverage == 1.0, f"{name} left {round((1 - coverage) * 100)} points unmeasured"


def test_archetypes_do_not_collapse_onto_one_number():
    """The defect in one line. Open-Graph-only sites all returned 58.9 at 0.56, so the figure said
    nothing about the site. Distinct markup must produce distinct scores."""
    scores = {n.name: category_score(n.name)[0] for n in FIXTURES.glob("entity_*.html")}
    assert len(set(scores.values())) >= 4, scores
    assert max(scores.values()) - min(scores.values()) > 40, scores


def test_open_graph_alone_no_longer_pays_out_three_times():
    """`opengraph_identity` scored its presence, `organization_declared` scored it as weak identity,
    and `structured_data_present` was rescued to partial by it — 46 points of critical weight
    resting on one signal, which is what pinned the category to a constant."""
    st = states_for("entity_share_widgets_only.html")
    assert st[STRUCT] == "fail", "Open Graph must not stand in for structured data"
    assert st[ORG] == "partial", "but it is still real, weak identity"


def test_a_well_marked_up_small_business_outscores_a_bare_one():
    """The property that matters, stated as an ordering rather than as memorised numbers."""
    good, _ = category_score("entity_local_business.html")
    bare, _ = category_score("entity_share_widgets_only.html")
    assert good > bare + 40


# =================================================================================================
# Registry coherence
# =================================================================================================
def test_entity_checks_no_longer_suppress_each_other():
    """These edges encoded a DETECTION limit, not a causal one: sameAs and name were unmeasurable
    only because we looked for them in one place. Left in, a single fail wiped 44 points of the
    category to `unknown` as collateral."""
    for check_id in ENT.CHECKS:
        deps = REGISTRY[check_id]["depends_on"]
        assert not any(d.startswith("entity.") for d in deps), f"{check_id} depends on {deps}"


def test_registry_and_analyzer_agree_on_entity_thresholds():
    for check_id, embedded in ENT.DEFAULT_THRESHOLDS.items():
        actual = REGISTRY[check_id].get("threshold") or {}
        for key, value in embedded.items():
            if key.startswith("_"):
                continue
            assert actual.get(key) == value, f"{check_id}.{key} drifted from the registry"


def _resolve(name):
    """Run the fixture through the REAL scoring engine, where suppression actually happens."""
    registry, config = S.load_registry(), S.load_config()
    rows = ENT.analyze(artifact_for_fixture(name), REGISTRY)
    # The entity category depends on the render check, which another analyzer owns; supply the
    # passing state it would produce for a server-rendered page.
    rows = rows + [{"check_id": "render.content_without_js", "state": "pass", "measurement": None,
                    "evidence": None, "selector": None, "page_url": None, "reason": None},
                   {"check_id": "access.http_ok", "state": "pass", "measurement": None,
                    "evidence": None, "selector": None, "page_url": None, "reason": None}]
    results = [S.CheckResult(check_id=r["check_id"], state=r["state"],
                             measurement=str(r["measurement"]) if r["measurement"] is not None else None,
                             evidence=r["evidence"], selector=r["selector"],
                             page_url=r["page_url"], reason=r["reason"]) for r in rows]
    resolved = S.resolve_states(results, registry, config)
    return resolved, S.category_coverage(resolved, registry, config)


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("entity_*.html")))
def test_scoring_engine_agrees_the_category_is_fully_measured(name):
    """End-to-end through the real engine, not just the analyzer.

    Dependency suppression runs here, and it is what turned measurable checks into `unknown` in
    the first place. Asserting only the analyzer's own output would miss a reintroduced edge.
    """
    resolved, cat_coverage = _resolve(name)
    for check_id in ENT.CHECKS:
        assert resolved[check_id].state != "unknown", (
            f"{name}: {check_id} was suppressed back to unknown "
            f"({resolved[check_id].reason})")
    assert cat_coverage["entity_trust"] == 1.0, name
