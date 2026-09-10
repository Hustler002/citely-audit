"""Phase 1 — schema contract tests.

Guards the two cross-boundary contracts: the report schema (public output) and the crawl-artifact
schema (orchestrator -> analyzers). The most important property is that the MANDATED FLOOR from the
brief can never be weakened by a future edit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
REFS = REPO_ROOT / "skills" / "audit-orchestrator" / "references"
REPORT_SCHEMA_PATH = REFS / "report-schema.json"
ARTIFACT_SCHEMA_PATH = REFS / "crawl-artifact-schema.json"
SAMPLE_REPORT_PATH = REFS / "sample-report.json"

sys.path.insert(0, str(REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"))
import _scoring as S  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report_schema():
    return load(REPORT_SCHEMA_PATH)


@pytest.fixture(scope="module")
def artifact_schema():
    return load(ARTIFACT_SCHEMA_PATH)


def minimal_report() -> dict:
    """Exactly the mandated floor — nothing more."""
    return {
        "site": "example.com",
        "audited_at": "2026-09-04T14:32:00Z",
        "summary": {"total_findings": 1, "critical": 1, "high": 0, "medium": 0},
        "findings": [{
            "id": "F-001",
            "title": "Key facts require client-side rendering",
            "severity": "critical",
            "evidence": "Raw HTML body contained 0 characters of visible text.",
            "suggested_action": {"summary": "Server-render the homepage.", "priority": "critical"},
        }],
    }


# --- Schemas are themselves valid --------------------------------------------------------------
def test_schemas_are_valid_json_schema(report_schema, artifact_schema):
    Draft202012Validator.check_schema(report_schema)
    Draft202012Validator.check_schema(artifact_schema)


# --- The mandated floor ------------------------------------------------------------------------
def test_minimal_floor_report_validates(report_schema):
    """A report containing ONLY the brief's mandated fields must pass."""
    Draft202012Validator(report_schema).validate(minimal_report())


@pytest.mark.parametrize("missing", ["site", "audited_at", "summary", "findings"])
def test_missing_floor_field_is_rejected(report_schema, missing):
    report = minimal_report()
    del report[missing]
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


@pytest.mark.parametrize("missing", ["total_findings", "critical", "high", "medium"])
def test_missing_summary_floor_field_is_rejected(report_schema, missing):
    report = minimal_report()
    del report["summary"][missing]
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


@pytest.mark.parametrize("missing", ["id", "title", "severity", "evidence", "suggested_action"])
def test_missing_finding_floor_field_is_rejected(report_schema, missing):
    report = minimal_report()
    del report["findings"][0][missing]
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


def test_low_severity_is_rejected(report_schema):
    """Strict 3-tier: `low` must not be representable, or the summary invariant breaks."""
    report = minimal_report()
    report["findings"][0]["severity"] = "low"
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


def test_bad_timestamp_format_rejected(report_schema):
    report = minimal_report()
    report["audited_at"] = "2026-09-04 14:32:00"  # not RFC3339 Z
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


def test_bad_finding_id_pattern_rejected(report_schema):
    report = minimal_report()
    report["findings"][0]["id"] = "1"
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


# --- v3 additive fields ------------------------------------------------------------------------
def test_full_v3_report_validates(report_schema):
    report = minimal_report()
    report["summary"].update({
        "discoverability_score": 66,
        "category_scores": {
            "ai_discoverability": 40.0,
            "ai_comprehension": None,
            "entity_trust": 75.5,
            "human_orientation": 88.0,
        },
        "score_confidence": 0.82,
    })
    report["diagnostics"] = {
        "total_ms": 4200, "playwright_available": False, "render_mode": "heuristic",
        "pages": [{"url": "https://example.com/", "status": "ok", "http_status": 200}],
        "language_detected": "en", "language_supported": True, "errors": [],
    }
    report["partial"] = True
    report["partial_reason"] = "budget"
    report["findings"][0].update({
        "category": "ai_discoverability", "check_id": "render.content_without_js",
        "confidence": "verified", "plain_summary": "Your content is built by the browser.",
        "measurement": 0, "threshold": 500, "selector": "#root",
        "page_url": "https://example.com/", "impact": "Crawlers see an empty shell.",
        "points_recoverable": 18.4,
    })
    report["findings"][0]["suggested_action"]["validation"] = "Run curl and confirm text is present."
    report["recommendations"] = [{
        "id": "R-001", "title": "Add FAQPage schema", "type": "proactive",
        "category": "ai_comprehension",
        "summary": "Your FAQ prose is one block away from being directly quotable.",
        "rationale": "The content already exists; only the markup is missing.",
    }]
    Draft202012Validator(report_schema).validate(report)


def test_category_score_null_is_allowed(report_schema):
    """null means 'not measurable' and must be distinguishable from 0."""
    report = minimal_report()
    report["summary"]["category_scores"] = {"ai_discoverability": None}
    Draft202012Validator(report_schema).validate(report)


def test_unknown_category_score_key_rejected(report_schema):
    report = minimal_report()
    report["summary"]["category_scores"] = {"not_a_category": 50}
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


def test_partial_reason_enum_enforced(report_schema):
    report = minimal_report()
    report["partial_reason"] = "because_i_said_so"
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


def test_recommendation_id_pattern_enforced(report_schema):
    report = minimal_report()
    report["recommendations"] = [{"id": "F-001", "title": "t", "summary": "s"}]
    with pytest.raises(Exception):
        Draft202012Validator(report_schema).validate(report)


# --- Scoring output conforms to the schema -----------------------------------------------------
def test_summarize_output_validates_against_schema(report_schema):
    """The scoring engine's summary block must satisfy the schema it feeds."""
    registry = S.load_registry()
    config = S.load_config()
    resolved = S.resolve_states(
        [S.CheckResult(cid, S.PASS) for cid in registry.order], registry, config
    )
    cats = S.category_scores(resolved, registry, config)
    summary = S.summarize(
        [{"id": "F-001", "severity": "critical"}],
        cats, S.overall_score(cats, config), S.score_confidence(resolved, registry, config),
    )
    report = minimal_report()
    report["summary"] = summary
    report["findings"][0]["severity"] = "critical"
    Draft202012Validator(report_schema).validate(report)


# --- Checked-in sample stays in sync -----------------------------------------------------------
def test_sample_report_validates(report_schema):
    """Catches drift between the schema and the committed example reviewers read."""
    Draft202012Validator(report_schema).validate(load(SAMPLE_REPORT_PATH))


def test_sample_report_satisfies_summary_invariant():
    s = load(SAMPLE_REPORT_PATH)["summary"]
    assert s["total_findings"] == s["critical"] + s["high"] + s["medium"]


# --- Artifact schema ---------------------------------------------------------------------------
def minimal_artifact() -> dict:
    return {
        "requested_url": "https://example.com",
        "final_url": "https://example.com/",
        "fetched_at": "2026-09-04T14:32:00Z",
        "robots": {"checked": True, "allowed": True},
        "pages": [{"url": "https://example.com/", "role": "homepage", "status": "ok"}],
        "timing": {"fetch_ms": 120, "total_ms": 900},
    }


def test_minimal_artifact_validates(artifact_schema):
    Draft202012Validator(artifact_schema).validate(minimal_artifact())


def test_artifact_requires_at_least_one_page(artifact_schema):
    artifact = minimal_artifact()
    artifact["pages"] = []
    with pytest.raises(Exception):
        Draft202012Validator(artifact_schema).validate(artifact)


def test_artifact_blocked_page_kind_enum(artifact_schema):
    artifact = minimal_artifact()
    artifact["pages"][0].update({"status": "blocked", "blocked_kind": "consent_wall"})
    Draft202012Validator(artifact_schema).validate(artifact)

    artifact["pages"][0]["blocked_kind"] = "some_other_wall"
    with pytest.raises(Exception):
        Draft202012Validator(artifact_schema).validate(artifact)


def test_artifact_external_corroboration_optional(artifact_schema):
    artifact = minimal_artifact()
    artifact["external_corroboration"] = None
    Draft202012Validator(artifact_schema).validate(artifact)

    artifact["external_corroboration"] = {
        "attempted": True, "succeeded": False, "source": "wikidata", "error": "timeout",
    }
    Draft202012Validator(artifact_schema).validate(artifact)
