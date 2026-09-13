"""The labelled corpus, as a CI gate.

`run_precision_recall.py` prints the table for a human; this asserts the same numbers so a
regression cannot land unnoticed. One shared implementation, two entry points.

The corpus is audited ONCE per session and every test reads that result. Ten full audits, each
spawning four analyzer subprocesses, is the expensive part — repeating it per assertion would
multiply the suite's runtime for no extra signal.

Also here: three whole-pipeline properties that belong beside the corpus rather than in a unit
test. The committed sample report must match a fresh run, two audits of one input must be
byte-identical, and a broken page must score materially below a healthy one.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PurePath

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(SCRIPTS))

import run_precision_recall as PR  # noqa: E402

CORPUS = PR.load_corpus()


@pytest.fixture(scope="module")
def outcome():
    return PR.run(CORPUS)


def result_for(outcome, name):
    return next(r for r in outcome["results"] if r["fixture"] == name)


# =================================================================================================
# Recall and precision
# =================================================================================================
def test_no_labelled_defect_is_missed(outcome):
    """Recall. A miss is a defect the audit failed to see on a page built to contain it."""
    misses = {r["fixture"]: r["recall_misses"] for r in outcome["results"] if r["recall_misses"]}
    assert not misses, f"missed labelled defects: {misses}"


def test_no_page_is_blamed_for_something_it_does_right(outcome):
    """Precision. A false positive costs a reader more than a miss does: it is the failure mode they
    notice first, being told to fix something that is already correct."""
    offenders = {r["fixture"]: r["false_positives"]
                 for r in outcome["results"] if r["false_positives"]}
    assert not offenders, f"false positives: {offenders}"


def test_every_archetype_scores_in_its_expected_band(outcome):
    """Sensible behaviour per archetype, asserted as ranges rather than exact
    numbers: pinning a score turns every legitimate improvement into a test failure."""
    out = {r["fixture"]: (r["score"], r["score_range"])
           for r in outcome["results"] if not r["score_in_range"]}
    assert not out, f"scores outside their band: {out}"


def test_the_corpus_actually_measures_something(outcome):
    """A corpus with no labels passes everything. This is the floor that stops that."""
    labelled_find = sum(len(r["recall_hits"]) + len(r["recall_misses"]) for r in outcome["results"])
    labelled_not = sum(len(CORPUS["fixtures"][r["fixture"]].get("must_not_find") or [])
                       for r in outcome["results"])
    assert labelled_find >= 10, "too few must_find labels to mean anything"
    assert labelled_not >= 50, "too few must_not_find labels to catch a false positive"
    assert len(outcome["results"]) >= 8


def test_headline_precision_and_recall_are_reported(outcome):
    totals = outcome["totals"]
    assert totals["recall"] == 1.0, f"recall {totals['recall']}"
    assert totals["precision"] == 1.0, f"precision {totals['precision']}"


# =================================================================================================
# Generalization properties the corpus exists to pin
# =================================================================================================
def test_a_javascript_shell_produces_one_root_cause_not_a_cascade(outcome):
    """Dependency suppression turns ~12 downstream false positives into one finding
    about the cause. Asserted on both shells, which reach that state by different routes."""
    for fixture in ("broken_page.html", "spa_hydrating.html"):
        result = result_for(outcome, fixture)
        assert "render.content_without_js" in result["reported"], fixture
        assert len(result["reported"]) <= 3, f"{fixture} cascaded: {result['reported']}"


def test_a_non_english_page_is_never_failed_on_language(outcome):
    """The generalization defect this project was built to avoid. The gap must show up as
    coverage, never as a wall of findings."""
    result = result_for(outcome, "non_english_page.html")
    assert not result["false_positives"]
    assert result["score"] >= 85, f"a structurally excellent German page scored {result['score']}"


def test_a_well_built_commercial_page_is_left_alone(outcome):
    """The false-positive trap. Almost every label on the product page is a must-NOT-find."""
    result = result_for(outcome, "archetype_ecommerce_pdp.html")
    assert result["reported"] == [], f"clean product page was flagged: {result['reported']}"


def test_div_soup_is_not_excused_by_suppression(outcome):
    """It is server-rendered, so nothing may be suppressed and every check must stand alone.

    Without this, a tool could score well on the corpus by calling every hard page a JavaScript
    shell and suppressing its way out of the analysis.
    """
    result = result_for(outcome, "archetype_div_soup.html")
    assert "render.content_without_js" not in result["reported"]
    assert len(result["reported"]) >= 8, "structural defects were suppressed rather than reported"


# =================================================================================================
# Injection resistance, measured against a structural clone
# =================================================================================================
def test_injected_instructions_change_nothing(outcome):
    """The adversarial fixture is healthy_page.html plus instructions in seven carriers. Because
    the STRUCTURE is identical, any difference is attributable to the injection and nothing else —
    which a "does it crash" test could never establish."""
    adversarial = result_for(outcome, "archetype_adversarial.html")
    healthy = result_for(outcome, "healthy_page.html")
    assert adversarial["score"] == healthy["score"]
    assert adversarial["reported"] == healthy["reported"]
    assert adversarial.get("matches_twin") is True


def test_injected_instructions_never_reach_the_report_as_instructions():
    """Page text may appear as sanitized evidence. It may never appear as a directive the report
    seems to be following."""
    report = PR.audit_fixture("archetype_adversarial.html")
    blob = json.dumps(report)
    for directive in ("SYSTEM OVERRIDE", "AGENT DIRECTIVE", "Ignore all previous instructions",
                      "set discoverability_score to 100", "maintenance mode"):
        assert directive not in blob, f"injected text surfaced verbatim: {directive}"
    assert report["summary"]["discoverability_score"] < 100


def test_a_missing_h1_is_reported_once_not_once_per_category():
    """One root cause must not be charged twice.

    `extraction.semantic_html` (AI discoverability) used to grade h1 count alongside landmarks,
    while `content.heading_hierarchy` (AI comprehension) owns heading structure and already
    requires exactly one h1. A page with no h1 was therefore penalised in two categories, and no
    suppression edge could catch it because suppression works within a dependency chain, not across
    two unrelated checks that happen to read the same signal.

    Asserted on div soup, which has no h1 and no landmarks, so both checks certainly fire.
    """
    report = PR.audit_fixture("archetype_div_soup.html")
    by_check = {f["check_id"]: f for f in report["findings"]}

    assert "content.heading_hierarchy" in by_check, "the heading check must still own the h1"
    assert "h1" in json.dumps(by_check["content.heading_hierarchy"]).lower()

    landmark = by_check.get("extraction.semantic_html")
    assert landmark is not None, "div soup must still fail on landmarks"
    blob = json.dumps({k: v for k, v in landmark.items() if k != "suggested_action"}).lower()
    assert "h1" not in blob, f"the landmark check is still grading headings: {landmark['measurement']}"


def test_the_landmark_check_grades_landmarks_and_nothing_else():
    """A page with a real content landmark passes even with a heading defect.

    eff.org publishes an `article` landmark and two h1s. Under the old rule the second h1 blocked
    the landmark pass, marking the site down for a heading problem under a discoverability check.
    """
    sys.path.insert(0, str(REPO_ROOT / "skills" / "crawl-render-extraction-audit" / "scripts"))
    import crawl_render_extract as CRE  # noqa: E402

    two_h1_with_article = ("<html lang='en'><body><article><h1>One</h1><h1>Two</h1>"
                           "<p>Body copy.</p></article></body></html>")
    page = {"raw": {"html": two_h1_with_article}}
    assert CRE.check_semantic_html(page, "u")["state"] == "pass"

    nav_only = "<html lang='en'><body><nav><a href='/'>Home</a></nav><div>Copy.</div></body></html>"
    assert CRE.check_semantic_html({"raw": {"html": nav_only}}, "u")["state"] == "partial"

    nothing = "<html lang='en'><body><div>Copy.</div></body></html>"
    assert CRE.check_semantic_html({"raw": {"html": nothing}}, "u")["state"] == "fail"


# =================================================================================================
# Whole-pipeline properties
# =================================================================================================
def _comparable(value):
    """Strip what is machine-specific, keep what is behaviour.

    The report echoes the path it was given, so the committed sample carries the relative path from
    the documented regeneration command while this harness passes an absolute one. Comparing those
    would assert which command generated the file rather than what the tool does. Directories are
    collapsed to the file name, so the FIELD is still compared — a run that audited two pages
    instead of one would still be caught — while the machine it ran on is not.
    """
    if isinstance(value, str):
        # Per whitespace-separated token, not just at the start: the audited path is also
        # substituted into remediation text, so it turns up mid-sentence inside a shell command
        # as well as in page_url.
        parts = []
        for token in value.split(" "):
            if "file://" in token:
                head, _, tail = token.partition("file://")
                token = head + "file://" + PurePath(tail.replace(chr(92), "/")).name
            parts.append(token)
        return " ".join(parts)
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    if isinstance(value, dict):
        return {k: _comparable(v) for k, v in value.items()
                if k not in ("total_ms", "crawl_ms")}
    return value


def test_the_checked_in_sample_report_still_matches_a_fresh_run():
    """The sample is regenerated by hand, which is exactly the step that goes stale.

    Timestamps and wall-clock timings are excluded because they are pinned deliberately; everything
    that describes BEHAVIOUR must still match, or the committed reference is advertising output the
    code no longer produces.
    """
    sample = json.loads((SCRIPTS.parent / "references" / "sample-report.json")
                        .read_text(encoding="utf-8"))
    fresh = PR.audit_fixture("broken_page.html")

    volatile = {"audited_at", "_note"}
    for key in set(sample) | set(fresh):
        if key in volatile:
            continue
        if key == "diagnostics":
            a = _comparable(sample[key])
            b = _comparable(fresh[key])
            assert a == b, "sample-report.json diagnostics drifted from a fresh run"
            continue
        assert _comparable(sample.get(key)) == _comparable(fresh.get(key)), (
            f"sample-report.json is stale in '{key}' — regenerate it")


def test_two_audits_of_one_input_are_byte_identical():
    """PLAN's sign-off gate: identical artifact implies byte-identical report. Determinism is the
    basis of the whole no-LLM design, so it is asserted rather than assumed."""
    first = PR.audit_fixture("archetype_ecommerce_pdp.html")
    second = PR.audit_fixture("archetype_ecommerce_pdp.html")
    for report in (first, second):
        report["audited_at"] = "pinned"
        report["diagnostics"]["total_ms"] = 0
        report["diagnostics"]["crawl_ms"] = 0
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_broken_page_scores_materially_lower_than_the_healthy_one(outcome):
    """The ordering that has to hold for any of the numbers to mean anything."""
    healthy = result_for(outcome, "healthy_page.html")["score"]
    broken = result_for(outcome, "broken_page.html")["score"]
    assert broken < healthy - 40, f"healthy {healthy} vs broken {broken}"


# =================================================================================================
# The harness itself
# =================================================================================================
def test_the_script_entry_point_runs_and_reports_cleanly():
    """PLAN's verification section invokes it as a script, so the script must actually work."""
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "tests" / "run_precision_recall.py")],
                          capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=1800)
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert "recall" in proc.stdout and "precision" in proc.stdout


def test_evaluate_counts_a_miss_and_a_false_positive():
    """The harness's own arithmetic, driven with a stub report so it cannot pass by accident."""
    labels = {"must_find": ["a.b", "c.d"], "must_not_find": ["e.f"], "score_range": [50, 60]}
    report = {"summary": {"discoverability_score": 99},
              "findings": [{"check_id": "a.b"}, {"check_id": "e.f"}]}
    out = PR.evaluate("stub", labels, report)
    assert out["recall_misses"] == ["c.d"]
    assert out["false_positives"] == ["e.f"]
    assert out["recall"] == 0.5
    assert out["precision"] == 0.0
    assert out["score_in_range"] is False


def test_every_labelled_fixture_exists_and_every_check_id_is_real():
    """A typo in a label silently weakens the corpus: a must_find for a check that does not exist
    can never be reported, and a must_not_find for one can never be violated."""
    registry = {c["id"] for c in
                json.loads((REPO_ROOT / "config" / "checks.json").read_text(encoding="utf-8"))["checks"]}
    for name, labels in CORPUS["fixtures"].items():
        assert (FIXTURES / name).exists(), name
        for key in ("must_find", "must_not_find"):
            unknown = [c for c in (labels.get(key) or []) if c not in registry]
            assert not unknown, f"{name}.{key} references unknown checks: {unknown}"
        low, high = labels["score_range"]
        assert 0 <= low < high <= 100, name
        twin = labels.get("must_match")
        assert twin is None or twin in CORPUS["fixtures"], name


def test_the_nine_archetypes_are_represented():
    """Nine archetypes are covered. Consent-wall lives in the fixture server rather than here,
    and the corpus says so in its own notes rather than quietly omitting it."""
    archetypes = " ".join(v["archetype"] for v in CORPUS["fixtures"].values()).lower()
    for word in ("static", "adversarial", "e-commerce", "image-heavy", "div soup",
                 "single-page", "non-english", "corporate"):
        assert word in archetypes, f"no fixture covers the {word} archetype"
    assert "consent-wall" in CORPUS["_not_covered_here"].lower()
