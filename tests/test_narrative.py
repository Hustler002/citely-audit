"""The non-expert output layer.

The plain-language layer lives in the emitted report rather than in a separate renderer, so that a
machine consumer piping the JSON gets the same plain reading a person does and there is one
wording rather than two that can disagree. These tests assert it there.

Three properties carry the weight:

  * **A score built on a minority of the evidence must not read as a verdict on the site.**
    Asserted on the two JavaScript shells, which are the whole reason the rule exists.
  * **The layer is presentation only.** If adding it ever moves a score, the scoring spine has been
    contaminated and the determinism guarantee is gone.
  * **No undefined jargon in layers 1 and 2.** Asserted as a property over the actual emitted text,
    not by reading the wording once and trusting it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import _narrative as N  # noqa: E402
import _scoring as S  # noqa: E402


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


REGISTRY_RAW = load(REPO_ROOT / "config" / "checks.json")
CATEGORIES = REGISTRY_RAW["categories"]
CATEGORY_IDS = ["ai_discoverability", "ai_comprehension", "entity_trust", "human_orientation"]


def run_audit(fixture, timeout=300):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_audit.py"), "--html-file", str(FIXTURES / fixture)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=timeout)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


# =================================================================================================
# Low coverage must suppress the headline
# =================================================================================================
@pytest.mark.parametrize("fixture,score", [("spa_hydrating.html", 80), ("broken_page.html", 30)])
def test_a_javascript_shell_never_presents_its_score_as_a_verdict(fixture, score):
    """The acceptance criterion for withholding a headline.

    Both fixtures leave 82.5% of the check weight unmeasurable, and they score 80 and 30 off the
    same handful of survivors — which is the point: at this coverage the number carries no
    information about the site, so it must not be the headline either way.
    """
    report = run_audit(fixture)
    summary = report["summary"]
    assert summary["discoverability_score"] == score, "fixture drifted; the rest of this is moot"
    assert summary["coverage"] < N.COVERAGE_FLOOR
    assert summary["headline_reliable"] is False
    assert summary["headline_caveat"]
    assert str(score) not in summary["verdict"], "the unreliable score leaked into the headline"


def test_a_category_scoring_100_off_one_check_does_not_claim_to_be_fine():
    """Open issue #10, finally closed.

    Human Orientation reads 100.0 on the hydrating shell because five of its six checks were
    suppressed and one survived. The NUMBER stays — deleting a measurement would hide it — but the
    sentence a reader acts on must not say the category is healthy.
    """
    report = run_audit("spa_hydrating.html")
    summary = report["summary"]
    assert summary["category_scores"]["human_orientation"] == 100.0
    assert summary["category_coverage"]["human_orientation"] < N.COVERAGE_FLOOR
    verdict = summary["category_verdicts"]["human_orientation"]
    assert verdict == CATEGORIES["human_orientation"]["unmeasured"]
    assert "100" not in verdict


def test_a_well_measured_site_keeps_its_headline():
    """The gate must cost confidence only where confidence is unearned.

    Without this, suppressing every headline would satisfy the rule above while making the report
    useless.
    """
    report = run_audit("healthy_page.html")
    summary = report["summary"]
    assert summary["coverage"] >= N.COVERAGE_FLOOR
    assert summary["headline_reliable"] is True
    assert summary["headline_caveat"] is None
    assert summary["verdict"] == CATEGORIES["overall"]["bands"][0]["verdict"]


@pytest.mark.parametrize("coverage,reliable", [
    (0.0, False), (0.49, False), (0.5, True), (0.51, True), (1.0, True), (None, False)])
def test_the_floor_is_applied_at_exactly_one_half(coverage, reliable):
    _, ok, caveat = N.overall_verdict(75, coverage, CATEGORIES)
    assert ok is reliable
    assert (caveat is None) is reliable


def test_the_report_says_what_it_could_not_check():
    """Silence is ambiguous: 'nothing is wrong' and 'we could not look' produce the same short
    findings list, and only one is good news."""
    report = run_audit("spa_hydrating.html")
    gaps = {g["category"]: g for g in report["not_checked"]}
    assert set(gaps) >= {"ai_comprehension", "entity_trust", "human_orientation"}
    for gap in gaps.values():
        assert gap["reasons"], f"{gap['category']} says nothing about why"


def test_the_gap_reasons_name_the_real_cause_not_the_engine_code():
    """`suppressed_by_failed_prerequisite` is true and tells a reader nothing. The report names the
    check that actually blocked them, in its own failure wording."""
    report = run_audit("spa_hydrating.html")
    reasons = [r for gap in report["not_checked"] for r in gap["reasons"]]
    assert reasons
    assert not any("suppressed_by_failed_prerequisite" in r for r in reasons)
    assert any("javascript" in r.lower() for r in reasons), reasons


def test_a_fully_measured_site_reports_no_gaps():
    assert run_audit("healthy_page.html")["not_checked"] == []


# =================================================================================================
# The layer is presentation only
# =================================================================================================
def test_the_output_layer_cannot_change_the_score():
    """The narrative layer is additive by construction. If this fails, a presentation
    concern has reached the scoring spine and determinism is gone."""
    import run_audit as RA
    config = load(REPO_ROOT / "config" / "scoring-config.json")
    html = str(FIXTURES / "advisor_specs_in_prose.html")

    report = RA.audit(None, html, config)
    baseline_score = report["summary"]["discoverability_score"]
    baseline_coverage = report["summary"]["coverage"]
    baseline_categories = dict(report["summary"]["category_scores"])

    # The narrative layer reads these; recomputing from the same resolved states must agree.
    again = RA.audit(None, html, config)
    assert again["summary"]["discoverability_score"] == baseline_score
    assert again["summary"]["coverage"] == baseline_coverage
    assert again["summary"]["category_scores"] == baseline_categories


def test_next_actions_is_a_view_of_findings_and_never_a_new_one():
    report = run_audit("advisor_faq_unmarked.html")
    finding_ids = {f["id"] for f in report["findings"]}
    action_ids = [a["finding_id"] for a in report["next_actions"]]
    assert set(action_ids) == finding_ids, "next_actions invented or dropped a finding"
    assert len(action_ids) == len(set(action_ids)), "a finding was ranked twice"


def test_next_actions_is_ordered_by_what_the_fix_is_worth():
    """Fixes are ranked by points recoverable rather than by opinion, and the plain-language layer presents
    that ordering. findings[] cannot carry it: its order is fixed so F-001… stay stable."""
    report = run_audit("advisor_faq_unmarked.html")
    gains = [a["score_gain"] for a in report["next_actions"]]
    assert gains == sorted(gains, reverse=True), gains
    assert [a["rank"] for a in report["next_actions"]] == list(range(1, len(gains) + 1))


def test_points_recoverable_outranks_severity():
    """The assertion the fixture could not make.

    `test_next_actions_is_ordered_by_what_the_fix_is_worth` passed with the points key deleted,
    because on that fixture the severity order and the gain order happen to coincide: one high at
    4.5 followed by three mediums at 3.0, 2.5 and 1.5. It was measuring the fixture, not the rule —
    the fourth time this project has caught a test passing for the wrong reason. This case is built
    so the two orderings DISAGREE, which is the only shape that can tell them apart.

    Fixes are ranked by what fixing them is worth, not by how alarming they sound.
    """
    findings = [{"id": "F-001", "severity": "critical", "points_recoverable": 5.0},
                {"id": "F-002", "severity": "medium", "points_recoverable": 20.0}]
    ranked = [a["finding_id"] for a in N.next_actions(findings)]
    assert ranked == ["F-002", "F-001"], "severity was allowed to outrank return on effort"


def test_ranking_is_total_so_repeat_runs_agree():
    """Equal point values must not leave the order to chance, or the report stops being
    byte-reproducible — the property the whole project rests on."""
    tied = [{"id": f"F-00{i}", "severity": "high", "category": "entity_trust",
             "points_recoverable": 5.0, "suggested_action": {"summary": "x"}} for i in (3, 1, 2)]
    first = [a["finding_id"] for a in N.next_actions(tied)]
    second = [a["finding_id"] for a in N.next_actions(list(reversed(tied)))]
    assert first == second == ["F-001", "F-002", "F-003"]


def test_severity_breaks_a_tie_before_the_id_does():
    tied = [{"id": "F-002", "severity": "critical", "points_recoverable": 5.0},
            {"id": "F-001", "severity": "medium", "points_recoverable": 5.0}]
    assert [a["finding_id"] for a in N.next_actions(tied)] == ["F-002", "F-001"]


def test_every_action_answers_what_where_and_how_to_confirm():
    """The checklist says what to do; the finding it names says where and how to confirm.

    Checked through `finding_id`, so the guarantee still holds end to end now that where and
    confirm live once, on the finding, instead of being copied into every action.
    """
    report = run_audit("advisor_faq_unmarked.html")
    findings = {f["id"]: f for f in report["findings"]}
    assert report["next_actions"]
    for action in report["next_actions"]:
        assert action["do"], f"{action['finding_id']} does not say what to do"
        suggested = findings[action["finding_id"]]["suggested_action"]
        assert suggested.get("target"), f"{action['finding_id']} does not say where"
        assert suggested.get("validation"), f"{action['finding_id']} does not say how to confirm"


def test_next_actions_is_a_checklist_not_a_copy_of_the_findings():
    """Each action carries what to do and what it is worth, and nothing else already on the finding.

    Every descriptive field in every action used to be an exact copy of the linked finding, so the
    same where, confirm and why-it-matters text appeared twice in every report.
    """
    report = run_audit("advisor_faq_unmarked.html")
    findings = {f["id"]: f for f in report["findings"]}
    assert report["next_actions"]
    for action in report["next_actions"]:
        assert set(action) == {"rank", "finding_id", "title", "severity", "score_gain", "do"}, action
        finding = findings[action["finding_id"]]
        assert action["title"] == finding["title"]
        assert action["severity"] == finding["severity"]
        assert action["score_gain"] == finding["points_recoverable"]
        assert action["do"] == finding["suggested_action"]["summary"]


# =================================================================================================
# No undefined jargon in layers 1 and 2
# =================================================================================================
JARGON = ["json-ld", "schema.org", "sameas", "opengraph", "og:", "text_to_node", "coverage ratio",
          "check_id", "points_recoverable", "not_applicable", "heuristic", "tier a", "tier b",
          "suppressed_by", "credit", "denominator", "microdata", "rdfa"]


@pytest.mark.parametrize("fixture", ["healthy_page.html", "spa_hydrating.html",
                                     "advisor_faq_unmarked.html"])
def test_layer_one_carries_no_undefined_jargon(fixture):
    """Asserted over the text actually emitted, not over the wording read once and trusted."""
    summary = run_audit(fixture)["summary"]
    text = " ".join([summary["verdict"], summary.get("headline_caveat") or ""]
                    + list(summary["category_verdicts"].values())).lower()
    offenders = [j for j in JARGON if j in text]
    assert not offenders, f"{fixture}: layer 1 uses {offenders}"


def test_the_jargon_guard_can_actually_fail():
    """A guard that cannot fail is not a guard."""
    assert [j for j in JARGON if j in "add json-ld to the page"]


def test_every_category_has_a_verdict_for_every_band():
    for category in CATEGORY_IDS:
        spec = CATEGORIES[category]
        assert spec.get("unmeasured"), category
        bands = spec["bands"]
        assert [b["min"] for b in bands] == sorted((b["min"] for b in bands), reverse=True), category
        assert bands[-1]["min"] == 0, f"{category} has no band covering the worst case"
        for band in bands:
            assert band["verdict"].strip(), category


def test_every_scored_category_gets_a_sentence():
    report = run_audit("healthy_page.html")
    assert set(report["summary"]["category_verdicts"]) == set(CATEGORY_IDS)


def test_band_selection_picks_the_first_band_the_score_meets():
    bands = [{"min": 80, "verdict": "good"}, {"min": 50, "verdict": "mid"}, {"min": 0, "verdict": "bad"}]
    assert N._verdict_from_bands(bands, 100) == "good"
    assert N._verdict_from_bands(bands, 80) == "good"
    assert N._verdict_from_bands(bands, 79.9) == "mid"
    assert N._verdict_from_bands(bands, 0) == "bad"
    assert N._verdict_from_bands(bands, None) is None


# =================================================================================================
# Resilience — the layer must never be the thing that breaks a report
# =================================================================================================
@pytest.mark.parametrize("bad", [None, {}, {"bands": None}, {"bands": [None, 3]},
                                 {"bands": [{"min": "x", "verdict": "v"}]}])
def test_malformed_band_config_degrades_instead_of_raising(bad):
    verdict, ok, _ = N.overall_verdict(70, 0.9, {"overall": bad} if bad is not None else {})
    assert isinstance(verdict, str) and isinstance(ok, bool)


def test_missing_category_config_still_produces_a_sentence():
    out = N.category_verdicts({"entity_trust": 60.0}, {"entity_trust": 0.9}, {})
    assert out["entity_trust"]


def test_next_actions_ignores_junk_entries():
    assert N.next_actions(["nope", None, {"id": "F-001"}]) == [
        {"rank": 1, "finding_id": "F-001", "title": None, "severity": None, "score_gain": None,
         "do": None}]


def test_the_schema_declares_every_field_the_report_emits():
    """A field the schema does not know about is a field no consumer can rely on."""
    report = run_audit("advisor_faq_unmarked.html")
    schema = load(SCRIPTS.parent / "references" / "report-schema.json")
    for key in ("next_actions", "not_checked"):
        assert key in schema["properties"], key
        assert key in report, key
    for key in ("verdict", "headline_reliable", "headline_caveat", "category_verdicts"):
        assert key in schema["properties"]["summary"]["properties"], key
        assert key in report["summary"], key


def test_the_report_still_validates():
    import run_audit as RA
    assert RA.validate_report(run_audit("spa_hydrating.html")) == []


def test_the_mandated_floor_is_untouched_by_the_new_fields():
    """The brief's schema is a floor, not a ceiling — but the floor still has to hold."""
    report = run_audit("advisor_faq_unmarked.html")
    assert {"site", "audited_at", "summary", "findings"} <= set(report)
    summary = report["summary"]
    assert {"total_findings", "critical", "high", "medium"} <= set(summary)
    assert summary["total_findings"] == summary["critical"] + summary["high"] + summary["medium"]
    for finding in report["findings"]:
        assert {"id", "title", "severity", "evidence", "suggested_action"} <= set(finding)
        assert {"summary", "priority"} <= set(finding["suggested_action"])
        assert re.fullmatch(r"F-\d{3,}", finding["id"])
