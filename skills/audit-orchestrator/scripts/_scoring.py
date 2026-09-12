#!/usr/bin/env python3
"""Citely scoring engine — the capability model.

PURE. No network, no rendering, no side effects. Given a set of raw check states this module
produces category scores, an overall score, score confidence, deterministic finding ids, and the
`points_recoverable` ranking that drives remediation priority.

Design, and why it replaces `100 - sum(penalty)`:
  * Bounded and comparable  — a weighted mean cannot run away as findings accumulate.
  * No double counting      — dependency suppression collapses correlated failures.
  * Missing != negative     — `unknown` / `not_applicable` leave the DENOMINATOR entirely rather
                              than scoring zero, so an unmeasurable check lowers confidence, not score.
  * Confidence is explicit  — surfaced as `score_confidence` instead of silently shrinking numbers.
  * Hard to game            — states depend on structural facts, so adding filler text changes nothing.

Determinism contract: identical input states + identical config => byte-identical output. Every
iteration order is explicitly sorted; no set iteration or dict-insertion order leaks into results.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# --- Check states -----------------------------------------------------------------------------
PASS = "pass"
PARTIAL = "partial"
FAIL = "fail"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"

VALID_STATES = frozenset({PASS, PARTIAL, FAIL, NOT_APPLICABLE, UNKNOWN})

# Reasons a check was forced to UNKNOWN. Surfaced in the report for auditability.
REASON_SUPPRESSED = "suppressed_by_failed_prerequisite"
REASON_LANGUAGE = "language_unsupported_or_undetected"
REASON_NOT_MEASURED = "not_measured"
REASON_BLOCKED = "blocked_before_fetch"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHECKS_PATH = _REPO_ROOT / "config" / "checks.json"
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "scoring-config.json"


class ScoringConfigError(ValueError):
    """Raised when the registry or config is internally inconsistent (fail fast, never silently)."""


# --- Data model -------------------------------------------------------------------------------
@dataclass(frozen=True)
class Check:
    id: str
    category: str
    weight: float
    severity: str
    confidence_class: str
    kind: str
    depends_on: tuple[str, ...]
    requires_language: bool
    threshold: dict | None
    title: str
    signal: str
    impact: str
    plain_summary: str


@dataclass
class CheckResult:
    """A measured state for one check, before suppression is applied."""
    check_id: str
    state: str
    measurement: str | None = None
    evidence: str | None = None
    selector: str | None = None
    page_url: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in VALID_STATES:
            raise ScoringConfigError(
                f"{self.check_id}: invalid state {self.state!r}; expected one of {sorted(VALID_STATES)}"
            )


@dataclass
class ResolvedCheck:
    """A check state after suppression and language gating."""
    check_id: str
    state: str
    original_state: str
    reason: str | None = None
    suppressed_by: str | None = None
    measurement: str | None = None
    evidence: str | None = None
    selector: str | None = None
    page_url: str | None = None


@dataclass
class Registry:
    checks: dict[str, Check]
    order: tuple[str, ...] = field(default=())

    def by_category(self, category: str) -> list[Check]:
        return [self.checks[cid] for cid in self.order if self.checks[cid].category == category]

    @property
    def categories(self) -> list[str]:
        return sorted({c.category for c in self.checks.values()})


# --- Loading ----------------------------------------------------------------------------------
def load_registry(path: str | Path = DEFAULT_CHECKS_PATH) -> Registry:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    checks: dict[str, Check] = {}
    order: list[str] = []
    for entry in raw.get("checks", []):
        cid = entry["id"]
        if cid in checks:
            raise ScoringConfigError(f"duplicate check id: {cid}")
        checks[cid] = Check(
            id=cid,
            category=entry["category"],
            weight=float(entry["weight"]),
            severity=entry["severity"],
            confidence_class=entry["confidence_class"],
            kind=entry["kind"],
            depends_on=tuple(entry.get("depends_on", [])),
            requires_language=bool(entry.get("requires_language", False)),
            threshold=entry.get("threshold"),
            title=entry["title"],
            signal=entry["signal"],
            impact=entry["impact"],
            plain_summary=entry["plain_summary"],
        )
        order.append(cid)

    registry = Registry(checks=checks, order=tuple(order))
    _validate_registry(registry)
    return registry


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate_registry(registry: Registry) -> None:
    """Fail fast on registry drift: unknown deps, bad enums, cycles, non-100 category weights."""
    for cid in sorted(registry.checks):
        check = registry.checks[cid]
        if check.severity not in ("critical", "high", "medium"):
            raise ScoringConfigError(f"{cid}: severity {check.severity!r} outside the strict 3-tier set")
        if check.confidence_class not in ("verified", "heuristic"):
            raise ScoringConfigError(f"{cid}: bad confidence_class {check.confidence_class!r}")
        if check.kind not in ("structural", "statistical"):
            raise ScoringConfigError(f"{cid}: bad kind {check.kind!r}")
        if check.weight <= 0:
            raise ScoringConfigError(f"{cid}: weight must be positive, got {check.weight}")
        for dep in check.depends_on:
            if dep not in registry.checks:
                raise ScoringConfigError(f"{cid}: depends_on unknown check {dep!r}")

    # Category weights must sum to 100 so per-category weights read as percentages.
    for category in registry.categories:
        total = sum(c.weight for c in registry.by_category(category))
        if abs(total - 100.0) > 1e-6:
            raise ScoringConfigError(f"category {category!r} weights sum to {total}, expected 100")

    _assert_acyclic(registry)


def _assert_acyclic(registry: Registry) -> None:
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {cid: WHITE for cid in registry.checks}

    def visit(cid: str, stack: list[str]) -> None:
        if colour[cid] == GREY:
            cycle = " -> ".join(stack[stack.index(cid):] + [cid])
            raise ScoringConfigError(f"dependency cycle detected: {cycle}")
        if colour[cid] == BLACK:
            return
        colour[cid] = GREY
        for dep in sorted(registry.checks[cid].depends_on):
            visit(dep, stack + [cid])
        colour[cid] = BLACK

    for cid in sorted(registry.checks):
        visit(cid, [])


def _topological_order(registry: Registry) -> list[str]:
    """Dependencies before dependents; ties broken by check id for determinism."""
    resolved: list[str] = []
    seen: set[str] = set()

    def visit(cid: str) -> None:
        if cid in seen:
            return
        for dep in sorted(registry.checks[cid].depends_on):
            visit(dep)
        seen.add(cid)
        resolved.append(cid)

    for cid in sorted(registry.checks):
        visit(cid)
    return resolved


# --- Resolution (suppression + language gating) -----------------------------------------------
def resolve_states(
    results: Iterable[CheckResult],
    registry: Registry,
    config: dict,
    *,
    language_supported: bool = True,
    blocked_before_fetch: bool = False,
) -> dict[str, ResolvedCheck]:
    """Apply language gating then dependency suppression, in topological order.

    A check not present in `results` is `unknown` (not measured) rather than a failure — the audit
    never punishes a site for something it did not look at.

    `blocked_before_fetch` covers the case where the page was never retrieved at all (our own
    auditor UA disallowed by robots.txt, per the operational gate). Every check then resolves to
    `unknown` with reason `blocked_before_fetch`, so the report attributes the whole scan to one
    root cause instead of listing two dozen indistinguishable "not measured" entries.
    """
    if blocked_before_fetch:
        return {
            cid: ResolvedCheck(
                check_id=cid, state=UNKNOWN, original_state=UNKNOWN, reason=REASON_BLOCKED
            )
            for cid in registry.order
        }

    by_id: dict[str, CheckResult] = {}
    for r in results:
        if r.check_id not in registry.checks:
            raise ScoringConfigError(f"result for unknown check id {r.check_id!r} (registry drift)")
        if r.check_id in by_id:
            raise ScoringConfigError(f"duplicate result for check {r.check_id!r}")
        by_id[r.check_id] = r

    suppression = config.get("suppression", {})
    suppression_on = suppression.get("enabled", True)
    require_verified = suppression.get("require_verified_prerequisite", True)

    resolved: dict[str, ResolvedCheck] = {}

    for cid in _topological_order(registry):
        check = registry.checks[cid]
        raw = by_id.get(cid)

        if raw is None:
            # Not reported by any analyzer. Default reason is `not_measured`, but if a prerequisite
            # blocks this check it was unmeasurable by construction — attribute it to the root cause
            # instead, which is strictly more informative. Checks with no blocking prerequisite keep
            # `not_measured`, so genuine analyzer gaps still surface rather than being hidden.
            rc = ResolvedCheck(
                check_id=cid, state=UNKNOWN, original_state=UNKNOWN, reason=REASON_NOT_MEASURED
            )
            if suppression_on:
                blocker = _find_blocking_prerequisite(check, resolved, registry, require_verified)
                if blocker is not None:
                    rc.reason = REASON_SUPPRESSED
                    rc.suppressed_by = blocker
            resolved[cid] = rc
            continue

        rc = ResolvedCheck(
            check_id=cid,
            state=raw.state,
            original_state=raw.state,
            reason=raw.reason,
            measurement=raw.measurement,
            evidence=raw.evidence,
            selector=raw.selector,
            page_url=raw.page_url,
        )

        # i18n gate: a language-dependent check on an unsupported/undetected language is UNKNOWN,
        # never FAIL. This is what stops non-English sites producing a wall of false positives.
        if check.requires_language and not language_supported:
            rc.state = UNKNOWN
            rc.reason = REASON_LANGUAGE
            resolved[cid] = rc
            continue

        if suppression_on:
            blocker = _find_blocking_prerequisite(check, resolved, registry, require_verified)
            if blocker is not None:
                rc.state = UNKNOWN
                rc.reason = REASON_SUPPRESSED
                rc.suppressed_by = blocker
        resolved[cid] = rc

    return resolved


def _find_blocking_prerequisite(
    check: Check,
    resolved: dict[str, ResolvedCheck],
    registry: Registry,
    require_verified: bool,
) -> str | None:
    """Return the id of a prerequisite that should silence this check, else None.

    Two blocking cases, both transitive by construction (we walk in topological order):
      1. Prerequisite FAILED — and, under the anti-dodge rule, that failure is `verified`.
      2. Prerequisite was itself suppressed (UNKNOWN via suppression) — we still cannot measure.
    """
    for dep_id in sorted(check.depends_on):
        dep = resolved.get(dep_id)
        if dep is None:
            continue
        if dep.state == FAIL:
            if require_verified and registry.checks[dep_id].confidence_class != "verified":
                # Anti-dodge: a merely heuristic failure may not silence dependents.
                continue
            return dep_id
        if dep.state == UNKNOWN and dep.reason == REASON_SUPPRESSED:
            return dep.suppressed_by or dep_id
    return None


# --- Scoring ----------------------------------------------------------------------------------
def category_scores(
    resolved: dict[str, ResolvedCheck], registry: Registry, config: dict
) -> dict[str, float | None]:
    """Weighted mean over APPLICABLE checks only. None when a category had nothing measurable.

    None is deliberate and must not be coerced to 0.0 — "we could not measure this" and
    "this scored zero" are different claims, and a penalty model conflates them.
    """
    credits = config["state_credits"]
    excluded = set(config.get("excluded_states", [UNKNOWN, NOT_APPLICABLE]))
    scores: dict[str, float | None] = {}

    for category in registry.categories:
        numerator = 0.0
        denominator = 0.0
        for check in registry.by_category(category):
            state = resolved[check.id].state if check.id in resolved else UNKNOWN
            if state in excluded:
                continue
            numerator += check.weight * float(credits[state])
            denominator += check.weight
        scores[category] = None if denominator == 0 else round(100.0 * numerator / denominator, 1)

    return scores


def overall_score(cat_scores: dict[str, float | None], config: dict) -> float | None:
    """Weighted mean of the category scores that could be computed."""
    weights = {k: v for k, v in config["category_weights"].items() if not k.startswith("_")}
    numerator = 0.0
    denominator = 0.0
    for category in sorted(cat_scores):
        score = cat_scores[category]
        if score is None:
            continue
        weight = float(weights.get(category, 0))
        numerator += weight * score
        denominator += weight
    return None if denominator == 0 else round(numerator / denominator, 1)


def score_confidence(resolved: dict[str, ResolvedCheck], registry: Registry, config: dict) -> float:
    """Share of SCORED weight contributed by `verified` checks, in [0, 1].

    This is how confidence is surfaced honestly: the score itself is never silently shrunk.
    """
    excluded = set(config.get("excluded_states", [UNKNOWN, NOT_APPLICABLE]))
    verified = 0.0
    total = 0.0
    for cid in registry.order:
        check = registry.checks[cid]
        state = resolved[cid].state if cid in resolved else UNKNOWN
        if state in excluded:
            continue
        total += check.weight
        if check.confidence_class == "verified":
            verified += check.weight
    return 0.0 if total == 0 else round(verified / total, 3)


def coverage(resolved: dict[str, ResolvedCheck], registry: Registry, config: dict) -> float:
    """Share of total check weight that was actually SCORED, in [0, 1].

    Distinct from `score_confidence`, and both are necessary. Confidence answers "how trustworthy
    are the checks we scored?"; coverage answers "how much of the audit could we run at all?".

    Without this, suppression produces a misleading headline: a client-rendered shell silences most
    checks, so the few survivors can score ~85 with high confidence on a site that is effectively
    invisible to AI. Coverage exposes that the number rests on a small slice of the audit.
    """
    excluded = set(config.get("excluded_states", [UNKNOWN, NOT_APPLICABLE]))
    weights = {k: v for k, v in config["category_weights"].items() if not k.startswith("_")}

    numerator = 0.0
    denominator = 0.0
    for category in registry.categories:
        cat_weight = float(weights.get(category, 0))
        if cat_weight == 0:
            continue
        checks = registry.by_category(category)
        total_w = sum(c.weight for c in checks)
        scored_w = sum(
            c.weight for c in checks
            if (resolved[c.id].state if c.id in resolved else UNKNOWN) not in excluded
        )
        if total_w == 0:
            continue
        numerator += cat_weight * (scored_w / total_w)
        denominator += cat_weight

    return 0.0 if denominator == 0 else round(numerator / denominator, 3)


def category_coverage(resolved: dict[str, ResolvedCheck], registry: Registry,
                      config: dict) -> dict[str, float]:
    """Per-category share of check weight that was actually scored.

    Overall `coverage` is not enough on its own: a CATEGORY headline can read 100.0 while resting on
    a single surviving check. On the client-rendered fixture, Human Orientation scored 100.0 because
    five of its six checks were suppressed and only `viewport_meta` remained — a number a reader
    would reasonably mistake for a clean bill of health. Publishing coverage beside each category
    score makes that self-evident.
    """
    excluded = set(config.get("excluded_states", [UNKNOWN, NOT_APPLICABLE]))
    out: dict[str, float] = {}
    for category in registry.categories:
        checks = registry.by_category(category)
        total = sum(c.weight for c in checks)
        scored = sum(
            c.weight for c in checks
            if (resolved[c.id].state if c.id in resolved else UNKNOWN) not in excluded
        )
        out[category] = 0.0 if total == 0 else round(scored / total, 3)
    return out


def points_recoverable(
    check_id: str, resolved: dict[str, ResolvedCheck], registry: Registry, config: dict
) -> float:
    """Overall-score points regained by bringing this check to `pass`.

    Drives remediation ranking by ROI rather than opinion: "fix this, gain +N points".
    Computed against the CURRENT denominator, so it reflects what is actually measurable now.
    """
    check = registry.checks[check_id]
    credits = config["state_credits"]
    excluded = set(config.get("excluded_states", [UNKNOWN, NOT_APPLICABLE]))
    state = resolved[check_id].state if check_id in resolved else UNKNOWN
    if state in excluded:
        return 0.0

    # Denominator is the FULL category weight, not just what happened to be measurable. Using the
    # measurable subset made the few surviving checks in a suppressed scan look enormous — a medium
    # finding outranking a critical one — which would invert remediation priority.
    denominator = sum(c.weight for c in registry.by_category(check.category))
    if denominator == 0:
        return 0.0

    gap = 1.0 - float(credits[state])
    category_gain = 100.0 * check.weight * gap / denominator

    weights = {k: v for k, v in config["category_weights"].items() if not k.startswith("_")}
    cat_weight = float(weights.get(check.category, 0))
    # Likewise across categories: weight by the configured category weights, not by which
    # categories happened to be measurable, so the figure is comparable between runs.
    total_weight = sum(float(weights.get(cat, 0)) for cat in registry.categories)
    if total_weight == 0:
        return 0.0
    return round(category_gain * cat_weight / total_weight, 1)


def _category_has_applicable(
    category: str, resolved: dict[str, ResolvedCheck], registry: Registry, excluded: set[str]
) -> bool:
    return any(
        (resolved[c.id].state if c.id in resolved else UNKNOWN) not in excluded
        for c in registry.by_category(category)
    )


# --- Findings ---------------------------------------------------------------------------------
def _evidence_digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def assign_finding_ids(findings: list[dict], config: dict) -> list[dict]:
    """Sort deterministically, then assign F-001, F-002, ...

    Sort key: severity rank -> category -> check_id -> evidence digest. The digest tie-breaker
    keeps ordering stable even when two findings share a check id (e.g. across pages), which is
    what makes repeat runs byte-identical and the checked-in sample report trustworthy.
    """
    severity_rank = {s: i for i, s in enumerate(config.get("severity_order", ["critical", "high", "medium"]))}

    def key(f: dict) -> tuple:
        return (
            severity_rank.get(f.get("severity", ""), 99),
            f.get("category", ""),
            f.get("check_id", ""),
            f.get("page_url") or "",
            _evidence_digest(f.get("evidence")),
        )

    ordered = sorted(findings, key=key)
    for i, finding in enumerate(ordered, start=1):
        finding["id"] = f"F-{i:03d}"
    return ordered


def summarize(findings: list[dict], cat_scores: dict[str, float | None],
              overall: float | None, confidence: float, coverage_ratio: float | None = None,
              cat_coverage: dict[str, float] | None = None) -> dict:
    """Build the `summary` block and enforce the mandated invariant.

    The floor requires total_findings == critical + high + medium, so any severity outside the
    strict 3-tier set is a hard error rather than a silently dropped count.
    """
    counts = {"critical": 0, "high": 0, "medium": 0}
    for f in findings:
        sev = f.get("severity")
        if sev not in counts:
            raise ScoringConfigError(f"finding {f.get('id')} has severity {sev!r} outside the 3-tier set")
        counts[sev] += 1

    total = len(findings)
    if total != counts["critical"] + counts["high"] + counts["medium"]:
        raise ScoringConfigError("summary invariant violated: total != critical + high + medium")

    summary = {
        "total_findings": total,
        "critical": counts["critical"],
        "high": counts["high"],
        "medium": counts["medium"],
        "discoverability_score": 0 if overall is None else int(round(overall)),
        "category_scores": {k: cat_scores[k] for k in sorted(cat_scores)},
        "score_confidence": confidence,
    }
    if coverage_ratio is not None:
        summary["coverage"] = coverage_ratio
    if cat_coverage is not None:
        summary["category_coverage"] = {k: cat_coverage[k] for k in sorted(cat_coverage)}
    return summary
