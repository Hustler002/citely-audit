"""Scoring spine tests.

Covers the properties the capability model must hold: determinism, dependency
suppression, the anti-dodge rule, unknown-excluded-from-denominator, bounds, monotonicity, i18n
gating, and the mandated summary invariant.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"))

import _scoring as S  # noqa: E402


@pytest.fixture(scope="module")
def registry():
    return S.load_registry()


@pytest.fixture(scope="module")
def config():
    return S.load_config()


def results_all(registry, state):
    return [S.CheckResult(check_id=cid, state=state) for cid in registry.order]


# --- Registry integrity -----------------------------------------------------------------------
def test_registry_loads_and_validates(registry):
    assert len(registry.checks) == 24
    assert registry.categories == [
        "ai_comprehension", "ai_discoverability", "entity_trust", "human_orientation",
    ]


def test_every_category_has_equal_check_count(registry):
    """Engagement sits at parity with discoverability: a visitor who bounces off a correct AI
    citation is as much a failure as never being cited at all."""
    counts = {c: len(registry.by_category(c)) for c in registry.categories}
    assert set(counts.values()) == {6}, counts


def test_category_weights_sum_to_100(registry):
    for category in registry.categories:
        total = sum(c.weight for c in registry.by_category(category))
        assert total == pytest.approx(100.0), f"{category} sums to {total}"


def test_registry_rejects_unknown_dependency(tmp_path):
    bad = {"checks": [{
        "id": "a.b", "category": "x", "weight": 100, "severity": "high",
        "confidence_class": "verified", "kind": "structural", "depends_on": ["does.not.exist"],
        "requires_language": False, "threshold": None, "title": "t", "signal": "s",
        "impact": "i", "plain_summary": "p",
    }]}
    p = tmp_path / "checks.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(S.ScoringConfigError, match="unknown check"):
        S.load_registry(p)


def test_registry_rejects_dependency_cycle(tmp_path):
    def mk(cid, deps):
        return {
            "id": cid, "category": "c", "weight": 50, "severity": "high",
            "confidence_class": "verified", "kind": "structural", "depends_on": deps,
            "requires_language": False, "threshold": None, "title": "t", "signal": "s",
            "impact": "i", "plain_summary": "p",
        }
    p = tmp_path / "checks.json"
    p.write_text(json.dumps({"checks": [mk("a", ["b"]), mk("b", ["a"])]}), encoding="utf-8")
    with pytest.raises(S.ScoringConfigError, match="cycle"):
        S.load_registry(p)


# --- Bounds -----------------------------------------------------------------------------------
def test_all_pass_scores_100(registry, config):
    resolved = S.resolve_states(results_all(registry, S.PASS), registry, config)
    cats = S.category_scores(resolved, registry, config)
    assert all(v == 100.0 for v in cats.values())
    assert S.overall_score(cats, config) == 100.0


def test_all_fail_scores_0(registry, config):
    """Every check independently measured as failing, with suppression disabled."""
    cfg = {**config, "suppression": {"enabled": False}}
    resolved = S.resolve_states(results_all(registry, S.FAIL), registry, cfg)
    cats = S.category_scores(resolved, registry, cfg)
    assert all(v == 0.0 for v in cats.values())
    assert S.overall_score(cats, cfg) == 0.0


def test_scores_stay_within_bounds(registry, config):
    import random
    rng = random.Random(1234)
    states = [S.PASS, S.PARTIAL, S.FAIL, S.UNKNOWN, S.NOT_APPLICABLE]
    for _ in range(200):
        results = [S.CheckResult(cid, rng.choice(states)) for cid in registry.order]
        resolved = S.resolve_states(results, registry, config)
        cats = S.category_scores(resolved, registry, config)
        for score in cats.values():
            assert score is None or 0.0 <= score <= 100.0
        overall = S.overall_score(cats, config)
        assert overall is None or 0.0 <= overall <= 100.0


# --- Missing != negative ----------------------------------------------------------------------
def test_unknown_leaves_denominator_rather_than_scoring_zero(registry, config):
    """A category with one pass and one unknown scores 100, not 50."""
    cat = "entity_trust"
    checks = registry.by_category(cat)
    results = [S.CheckResult(checks[0].id, S.PASS)] + [
        S.CheckResult(c.id, S.UNKNOWN) for c in checks[1:]
    ]
    # Disable suppression so we isolate the denominator behaviour.
    cfg = {**config, "suppression": {"enabled": False}}
    resolved = S.resolve_states(results, registry, cfg)
    assert S.category_scores(resolved, registry, cfg)[cat] == 100.0


def test_category_with_nothing_measurable_is_none_not_zero(registry, config):
    resolved = S.resolve_states(results_all(registry, S.UNKNOWN), registry, config)
    cats = S.category_scores(resolved, registry, config)
    assert all(v is None for v in cats.values())
    assert S.overall_score(cats, config) is None


def test_not_applicable_also_excluded(registry, config):
    cat = "human_orientation"
    checks = registry.by_category(cat)
    results = [S.CheckResult(checks[0].id, S.FAIL)] + [
        S.CheckResult(c.id, S.NOT_APPLICABLE) for c in checks[1:]
    ]
    cfg = {**config, "suppression": {"enabled": False}}
    resolved = S.resolve_states(results, registry, cfg)
    assert S.category_scores(resolved, registry, cfg)[cat] == 0.0


# --- Dependency suppression -------------------------------------------------------------------
def test_failed_prerequisite_suppresses_dependents(registry, config):
    """The CSR-shell case: one root cause must not become a dozen findings."""
    results = [S.CheckResult("access.http_ok", S.PASS),
               S.CheckResult("render.content_without_js", S.FAIL)]
    results += [S.CheckResult(cid, S.FAIL) for cid in registry.order
                if cid not in {"access.http_ok", "render.content_without_js"}]
    resolved = S.resolve_states(results, registry, config)

    assert resolved["render.content_without_js"].state == S.FAIL
    # Direct dependents are silenced...
    assert resolved["extraction.semantic_html"].state == S.UNKNOWN
    assert resolved["extraction.semantic_html"].reason == S.REASON_SUPPRESSED
    # ...and so are transitive ones (entity.sameas_present -> organization -> structured_data).
    assert resolved["entity.sameas_present"].state == S.UNKNOWN
    # Checks that do not depend on it are untouched.
    assert resolved["access.ai_crawlers_allowed"].state == S.FAIL


def test_suppression_records_the_blocking_check(registry, config):
    results = [S.CheckResult("access.http_ok", S.FAIL)]
    resolved = S.resolve_states(results, registry, config)
    assert resolved["render.content_without_js"].suppressed_by == "access.http_ok"


def _synthetic_registry(tmp_path, blocker_confidence):
    """Two checks where `dependent` depends on `blocker`.

    The real registry has no check depending on a heuristic one, so the anti-dodge rule cannot be
    exercised against it. A synthetic registry tests the rule directly rather than skipping it.
    """
    def mk(cid, confidence, deps):
        return {
            "id": cid, "category": "c", "weight": 50, "severity": "high",
            "confidence_class": confidence, "kind": "structural", "depends_on": deps,
            "requires_language": False, "threshold": None, "title": "t", "signal": "s",
            "impact": "i", "plain_summary": "p",
        }
    p = tmp_path / "checks.json"
    p.write_text(json.dumps({"checks": [
        mk("blocker", blocker_confidence, []),
        mk("dependent", "verified", ["blocker"]),
    ]}), encoding="utf-8")
    return S.load_registry(p)


def test_anti_dodge_heuristic_failure_does_not_suppress(tmp_path, config):
    """A HEURISTIC prerequisite failure must NOT silence dependents — this keeps recall honest.

    Without this rule, suppression could be used to dodge hard calls by hanging checks off a
    low-confidence prerequisite, converting real misses into innocuous `unknown`s.
    """
    reg = _synthetic_registry(tmp_path, "heuristic")
    resolved = S.resolve_states(
        [S.CheckResult("blocker", S.FAIL), S.CheckResult("dependent", S.FAIL)], reg, config
    )
    assert resolved["dependent"].state == S.FAIL
    assert resolved["dependent"].suppressed_by is None


def test_verified_failure_does_suppress(tmp_path, config):
    """Control case: the same structure with a VERIFIED blocker must suppress."""
    reg = _synthetic_registry(tmp_path, "verified")
    resolved = S.resolve_states(
        [S.CheckResult("blocker", S.FAIL), S.CheckResult("dependent", S.FAIL)], reg, config
    )
    assert resolved["dependent"].state == S.UNKNOWN
    assert resolved["dependent"].suppressed_by == "blocker"


def test_anti_dodge_can_be_disabled(tmp_path, config):
    """With require_verified_prerequisite off, a heuristic failure suppresses again."""
    reg = _synthetic_registry(tmp_path, "heuristic")
    cfg = {**config, "suppression": {"enabled": True, "require_verified_prerequisite": False}}
    resolved = S.resolve_states(
        [S.CheckResult("blocker", S.FAIL), S.CheckResult("dependent", S.FAIL)], reg, cfg
    )
    assert resolved["dependent"].state == S.UNKNOWN


def test_suppression_collapses_finding_count(registry, config):
    """Concrete statement of the anti-double-count property."""
    results = [S.CheckResult(cid, S.FAIL) for cid in registry.order]
    with_supp = S.resolve_states(results, registry, config)
    without = S.resolve_states(results, registry, {**config, "suppression": {"enabled": False}})
    fails_with = sum(1 for r in with_supp.values() if r.state == S.FAIL)
    fails_without = sum(1 for r in without.values() if r.state == S.FAIL)
    assert fails_with < fails_without


# --- Robots: AI-crawler access vs our own fetch gate -------------------------------------------
def test_ai_crawler_check_has_no_dependents(registry):
    """Blocking GPTBot must NOT suppress content checks.

    A site can block AI crawlers while remaining perfectly readable by us, so this check gates
    nothing. That is the opposite of the old `access.robots_allows` semantics, where our own
    fetch permission was (wrongly) modelled as a scored signal.
    """
    dependents = [c.id for c in registry.checks.values()
                  if "access.ai_crawlers_allowed" in c.depends_on]
    assert dependents == []


def test_ai_crawler_block_does_not_reduce_coverage_of_content(registry, config):
    """A GPTBot block is a critical finding, but the rest of the audit still runs fully."""
    results = [S.CheckResult(cid, S.PASS) for cid in registry.order
               if cid != "access.ai_crawlers_allowed"]
    results.append(S.CheckResult("access.ai_crawlers_allowed", S.FAIL))
    resolved = S.resolve_states(results, registry, config)
    assert S.coverage(resolved, registry, config) == 1.0
    cats = S.category_scores(resolved, registry, config)
    assert cats["ai_comprehension"] == 100.0          # untouched
    assert cats["ai_discoverability"] < 100.0          # penalised, correctly


def test_blocked_before_fetch_marks_every_check(registry, config):
    """Our own UA disallowed: one root cause, not 24 indistinguishable 'not measured' entries."""
    resolved = S.resolve_states([], registry, config, blocked_before_fetch=True)
    assert len(resolved) == len(registry.checks)
    assert all(r.state == S.UNKNOWN for r in resolved.values())
    assert all(r.reason == S.REASON_BLOCKED for r in resolved.values())


def test_blocked_before_fetch_yields_null_scores_not_zero(registry, config):
    resolved = S.resolve_states([], registry, config, blocked_before_fetch=True)
    cats = S.category_scores(resolved, registry, config)
    assert all(v is None for v in cats.values())
    assert S.overall_score(cats, config) is None
    assert S.coverage(resolved, registry, config) == 0.0


def test_blocked_before_fetch_overrides_supplied_results(registry, config):
    """Defensive: if the gate tripped, stale analyzer output must not leak into the score."""
    resolved = S.resolve_states(
        [S.CheckResult("access.http_ok", S.PASS)], registry, config, blocked_before_fetch=True
    )
    assert resolved["access.http_ok"].state == S.UNKNOWN


# --- i18n gating ------------------------------------------------------------------------------
def test_unsupported_language_yields_unknown_never_fail(registry, config):
    """English-only heuristics must not fail a non-English site."""
    results = results_all(registry, S.FAIL)
    resolved = S.resolve_states(results, registry, config, language_supported=False)
    lang_checks = [c.id for c in registry.checks.values() if c.requires_language]
    assert lang_checks, "registry should contain language-dependent checks"
    for cid in lang_checks:
        assert resolved[cid].state == S.UNKNOWN
        assert resolved[cid].reason == S.REASON_LANGUAGE


def test_language_independent_checks_still_score_on_unsupported_language(registry, config):
    results = results_all(registry, S.PASS)
    resolved = S.resolve_states(results, registry, config, language_supported=False)
    cats = S.category_scores(resolved, registry, config)
    # Every category retains measurable, language-independent checks.
    assert all(v == 100.0 for v in cats.values())


# --- Monotonicity -----------------------------------------------------------------------------
def test_improving_a_check_never_lowers_its_category(registry, config):
    cfg = {**config, "suppression": {"enabled": False}}
    for cid in registry.order:
        category = registry.checks[cid].category
        scores = []
        for state in (S.FAIL, S.PARTIAL, S.PASS):
            results = [S.CheckResult(c, S.PASS if c != cid else state) for c in registry.order]
            resolved = S.resolve_states(results, registry, cfg)
            scores.append(S.category_scores(resolved, registry, cfg)[category])
        assert scores == sorted(scores), f"{cid} not monotonic: {scores}"


# --- Determinism ------------------------------------------------------------------------------
def test_identical_input_yields_identical_output(registry, config):
    import random
    rng = random.Random(99)
    states = [S.PASS, S.PARTIAL, S.FAIL, S.UNKNOWN]
    results = [S.CheckResult(cid, rng.choice(states)) for cid in registry.order]

    def run():
        resolved = S.resolve_states(results, registry, config)
        cats = S.category_scores(resolved, registry, config)
        return json.dumps({
            "cats": cats,
            "overall": S.overall_score(cats, config),
            "confidence": S.score_confidence(resolved, registry, config),
        }, sort_keys=True)

    assert run() == run()


def test_finding_ids_are_deterministic_and_severity_ordered(config):
    findings = [
        {"check_id": "z.medium", "severity": "medium", "category": "b", "evidence": "e1"},
        {"check_id": "a.critical", "severity": "critical", "category": "a", "evidence": "e2"},
        {"check_id": "m.high", "severity": "high", "category": "a", "evidence": "e3"},
    ]
    first = S.assign_finding_ids([dict(f) for f in findings], config)
    second = S.assign_finding_ids([dict(f) for f in reversed(findings)], config)
    assert [f["id"] for f in first] == ["F-001", "F-002", "F-003"]
    assert [f["check_id"] for f in first] == [f["check_id"] for f in second]
    assert first[0]["severity"] == "critical"


def test_finding_ids_stable_when_check_ids_collide(config):
    """Same check across two pages must still order deterministically."""
    findings = [
        {"check_id": "x", "severity": "high", "category": "a", "evidence": "beta", "page_url": "/b"},
        {"check_id": "x", "severity": "high", "category": "a", "evidence": "alpha", "page_url": "/a"},
    ]
    a = S.assign_finding_ids([dict(f) for f in findings], config)
    b = S.assign_finding_ids([dict(f) for f in reversed(findings)], config)
    assert [f["page_url"] for f in a] == [f["page_url"] for f in b]


# --- Confidence -------------------------------------------------------------------------------
def test_score_confidence_is_verified_share(registry, config):
    resolved = S.resolve_states(results_all(registry, S.PASS), registry, config)
    conf = S.score_confidence(resolved, registry, config)
    assert 0.0 < conf < 1.0  # registry mixes verified and heuristic checks

    verified_only = [S.CheckResult(c.id, S.PASS) for c in registry.checks.values()
                     if c.confidence_class == "verified"]
    resolved2 = S.resolve_states(verified_only, registry, {**config, "suppression": {"enabled": False}})
    assert S.score_confidence(resolved2, registry, config) == 1.0


# --- Coverage ---------------------------------------------------------------------------------
def test_coverage_full_when_everything_measured(registry, config):
    resolved = S.resolve_states(results_all(registry, S.PASS), registry, config)
    assert S.coverage(resolved, registry, config) == 1.0


def test_coverage_zero_when_nothing_measured(registry, config):
    resolved = S.resolve_states(results_all(registry, S.UNKNOWN), registry, config)
    assert S.coverage(resolved, registry, config) == 0.0


def test_coverage_exposes_high_score_on_thin_scan(registry, config):
    """The motivating case: a CSR shell suppresses most checks, so a high score rests on very little.

    score_confidence cannot reveal this (the survivors are all `verified`), which is exactly why
    coverage exists as a separate signal.
    """
    results = [
        S.CheckResult("access.http_ok", S.PASS),
        S.CheckResult("access.ai_crawlers_allowed", S.PASS),
        S.CheckResult("access.indexable", S.PASS),
        S.CheckResult("render.content_without_js", S.FAIL),
        S.CheckResult("orientation.viewport_meta", S.PASS),
    ]
    resolved = S.resolve_states(results, registry, config)
    cats = S.category_scores(resolved, registry, config)
    overall = S.overall_score(cats, config)
    conf = S.score_confidence(resolved, registry, config)
    cov = S.coverage(resolved, registry, config)

    # Two categories are entirely unmeasurable -> null, never 0.
    assert cats["ai_comprehension"] is None
    assert cats["entity_trust"] is None
    # The headline looks healthy and confidence is perfect...
    assert overall > 80
    assert conf == 1.0
    # ...but coverage shows it rests on a small slice of the audit.
    assert cov < 0.30


def test_coverage_and_confidence_are_independent(registry, config):
    """All-heuristic scan: full-ish coverage of those checks, but low confidence."""
    heuristic_ids = [c.id for c in registry.checks.values() if c.confidence_class == "heuristic"]
    cfg = {**config, "suppression": {"enabled": False}}
    resolved = S.resolve_states(
        [S.CheckResult(cid, S.PASS) for cid in heuristic_ids], registry, cfg
    )
    assert S.score_confidence(resolved, registry, cfg) == 0.0
    assert 0.0 < S.coverage(resolved, registry, cfg) < 1.0


def test_summarize_includes_coverage_when_provided():
    summary = S.summarize([], {"ai_discoverability": 50.0}, 50.0, 0.9, coverage_ratio=0.42)
    assert summary["coverage"] == 0.42


def test_summarize_omits_coverage_when_absent():
    assert "coverage" not in S.summarize([], {"ai_discoverability": 50.0}, 50.0, 0.9)


# --- Points recoverable -----------------------------------------------------------------------
def test_points_recoverable_zero_for_passing_check(registry, config):
    resolved = S.resolve_states(results_all(registry, S.PASS), registry, config)
    for cid in registry.order:
        assert S.points_recoverable(cid, resolved, registry, config) == 0.0


def test_points_recoverable_positive_for_failing_check(registry, config):
    cfg = {**config, "suppression": {"enabled": False}}
    resolved = S.resolve_states(results_all(registry, S.FAIL), registry, cfg)
    heaviest = max(registry.checks.values(), key=lambda c: c.weight)
    assert S.points_recoverable(heaviest.id, resolved, registry, cfg) > 0.0


def test_points_recoverable_ranks_by_weight(registry, config):
    """Heavier failing checks must offer more recoverable points within a category."""
    cfg = {**config, "suppression": {"enabled": False}}
    resolved = S.resolve_states(results_all(registry, S.FAIL), registry, cfg)
    cat = "entity_trust"
    ranked = sorted(registry.by_category(cat), key=lambda c: c.weight, reverse=True)
    pts = [S.points_recoverable(c.id, resolved, registry, cfg) for c in ranked]
    assert pts == sorted(pts, reverse=True)


# --- Summary invariant ------------------------------------------------------------------------
def test_summary_enforces_mandated_invariant():
    findings = [
        {"id": "F-001", "severity": "critical"},
        {"id": "F-002", "severity": "high"},
        {"id": "F-003", "severity": "medium"},
        {"id": "F-004", "severity": "medium"},
    ]
    summary = S.summarize(findings, {"ai_discoverability": 50.0}, 50.0, 0.8)
    assert summary["total_findings"] == 4
    assert summary["total_findings"] == summary["critical"] + summary["high"] + summary["medium"]
    assert summary["discoverability_score"] == 50


def test_summary_rejects_out_of_tier_severity():
    with pytest.raises(S.ScoringConfigError, match="outside the 3-tier"):
        S.summarize([{"id": "F-001", "severity": "low"}], {}, None, 0.0)


def test_summary_handles_unscorable_overall():
    summary = S.summarize([], {"ai_discoverability": None}, None, 0.0)
    assert summary["total_findings"] == 0
    assert summary["discoverability_score"] == 0


# --- Input validation -------------------------------------------------------------------------
def test_invalid_state_rejected():
    with pytest.raises(S.ScoringConfigError, match="invalid state"):
        S.CheckResult("access.http_ok", "definitely-not-a-state")


def test_unknown_check_id_rejected(registry, config):
    with pytest.raises(S.ScoringConfigError, match="unknown check id"):
        S.resolve_states([S.CheckResult("no.such.check", S.PASS)], registry, config)


def test_duplicate_result_rejected(registry, config):
    with pytest.raises(S.ScoringConfigError, match="duplicate result"):
        S.resolve_states(
            [S.CheckResult("access.http_ok", S.PASS), S.CheckResult("access.http_ok", S.FAIL)],
            registry, config,
        )
