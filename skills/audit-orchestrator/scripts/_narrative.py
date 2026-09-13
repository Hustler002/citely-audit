#!/usr/bin/env python3
"""The non-expert output layer — pure functions, no I/O, no language model.

The report has to be actionable by someone who is not an SEO engineer. That layer lives in the
ENTRYPOINT'S SCHEMA rather than in a rendering step bolted on afterwards, so a machine consumer
piping the JSON gets the same plain reading as a person, and there is exactly one wording for
both rather than two that can disagree.

Three depths:

  1. `verdict` / `category_verdicts` — one plain sentence each, no undefined jargon.
  2. `next_actions` — the SAME findings, ordered by points recoverable, answering do / where /
     confirm / gain. This is a ranked VIEW, not a second copy: `findings[]` is ordered by severity
     so that `F-001…` stay stable across runs, which means it cannot also be ordered by ROI.
  3. `findings[]` itself — measurement, threshold, selector, confidence. Present, never the headline.

Every sentence comes from `config/checks.json`. Nothing here writes prose, because report wording
living in one place is what has kept it from drifting across six skills.

A score built on a minority of the evidence must not be presented as a verdict on the site.
`headline_reliable` is computed here and carried in the report, so the rule is a property of the
data rather than a convention each consumer is trusted to honour.
"""
from __future__ import annotations

# Below this share of total check weight, the headline is not a statement about the site — it is a
# statement about the few checks that happened to be measurable. Chosen as a principle rather than
# fitted to fixtures: under a half, the majority of the evidence is missing. It separates the two
# JavaScript shells (0.175) from every real page measured, the thinnest of which is 0.698.
COVERAGE_FLOOR = 0.5


def _bands_for(registry_categories: dict, category: str) -> dict:
    entry = registry_categories.get(category)
    return entry if isinstance(entry, dict) else {}


def _verdict_from_bands(bands, score) -> str | None:
    """First band whose `min` the score meets. Bands are ordered high to low."""
    if score is None or not isinstance(bands, list):
        return None
    for band in bands:
        if not isinstance(band, dict):
            continue
        try:
            if float(score) >= float(band.get("min", 0)):
                return band.get("verdict")
        except (TypeError, ValueError):
            continue
    return None


def category_verdicts(cat_scores: dict, cat_coverage: dict, registry_categories: dict) -> dict:
    """One plain sentence per category, chosen by score AND by whether it can be trusted.

    A category scoring 100.0 off a single surviving check is exactly what this guards against. Its number
    stays in the report — removing it would hide the measurement — but the SENTENCE says the
    category could not be assessed, because a confident sentence is what a reader actually acts on.
    """
    out = {}
    for category, score in sorted((cat_scores or {}).items()):
        spec = _bands_for(registry_categories, category)
        coverage = (cat_coverage or {}).get(category)
        thin = coverage is not None and float(coverage) < COVERAGE_FLOOR
        verdict = None if thin else _verdict_from_bands(spec.get("bands"), score)
        out[category] = verdict or spec.get("unmeasured") or "We could not assess this."
    return out


def overall_verdict(score, coverage, registry_categories: dict) -> tuple:
    """The headline sentence, and whether the number beside it can be trusted.

    Returns `(verdict, headline_reliable, caveat)`. The caveat is a string only when the headline is
    unreliable, so a consumer can branch on its presence without parsing prose.
    """
    spec = _bands_for(registry_categories, "overall")
    thin = coverage is None or float(coverage) < COVERAGE_FLOOR

    if score is None:
        return spec.get("unmeasured") or "We could not score this site.", False, spec.get("low_coverage_caveat")
    if thin:
        return (spec.get("unmeasured") or "We could not measure enough of this site to score it.",
                False, spec.get("low_coverage_caveat"))
    return _verdict_from_bands(spec.get("bands"), score) or "", True, None


def next_actions(findings: list, limit: int | None = None) -> list:
    """The findings as a prioritized checklist, ordered by what fixing them is worth.

    `findings[]` is deliberately ordered by severity so `F-001…` stay stable between runs, which
    means it cannot ALSO be ordered by return on effort. This is that second ordering, and it is a
    checklist rather than a copy: each entry says what to do and what it is worth, and `finding_id`
    leads to the rest. Where to make the change, how to confirm it and why it matters live once, on
    the finding, so the two lists can never disagree.

    Ranked by `points_recoverable`, with severity as the tie-break so two equal-value
    fixes still present the more serious one first, and the finding id last so the order is total
    and therefore reproducible.
    """
    severity_rank = {"critical": 0, "high": 1, "medium": 2}
    ranked = sorted(
        (f for f in (findings or []) if isinstance(f, dict)),
        key=lambda f: (-float(f.get("points_recoverable") or 0.0),
                       severity_rank.get(f.get("severity"), 99),
                       f.get("id") or ""))
    if limit is not None:
        ranked = ranked[:limit]

    actions = []
    for position, finding in enumerate(ranked, start=1):
        action = finding.get("suggested_action") or {}
        actions.append({
            "rank": position,
            "finding_id": finding.get("id"),
            "title": finding.get("title"),
            "severity": finding.get("severity"),
            "score_gain": finding.get("points_recoverable"),
            "do": action.get("summary"),
        })
    return actions


def what_could_not_be_checked(resolved: dict, registry, cat_coverage: dict,
                              raw_registry: dict | None = None,
                              reason_text: dict | None = None) -> list:
    """Why the report is quiet where it is quiet.

    A non-expert reading a short findings list cannot tell "nothing is wrong here" from "we could
    not look". Both produce silence and only one is good news, so the gap is stated rather than
    left to be inferred.

    Where a category was suppressed, the ROOT CAUSE is named in its own words rather than the
    engine's. `suppressed_by_failed_prerequisite` is a true statement that tells a reader nothing;
    "your page's content only appears after JavaScript runs" is the same fact and is actionable.
    """
    raw_registry = raw_registry or {}
    reason_text = reason_text or {}
    gaps = []

    for category, coverage in sorted((cat_coverage or {}).items()):
        if coverage is None or float(coverage) >= COVERAGE_FLOOR:
            continue

        blockers, codes = [], set()
        for check_id, state in (resolved or {}).items():
            if check_id not in registry.checks:
                continue
            if registry.checks[check_id].category != category:
                continue
            blocker = getattr(state, "suppressed_by", None)
            if blocker:
                meta = raw_registry.get(blocker) or {}
                blockers.append(meta.get("failure_title")
                                or (registry.checks[blocker].title
                                    if blocker in registry.checks else blocker))
            elif getattr(state, "reason", None):
                codes.add(state.reason)

        reasons = []
        for blocker in sorted(set(blockers)):
            reasons.append(f"blocked by an earlier problem: {blocker.lower()}")
        for code in sorted(codes):
            reasons.append(reason_text.get(code) or code)

        gaps.append({
            "category": category,
            "coverage": round(float(coverage), 3),
            "reasons": reasons[:5],
        })
    return gaps
