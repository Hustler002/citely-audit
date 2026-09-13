"""The orchestrator entrypoint.

This is the first point at which Citely is a usable tool, so these tests pin the properties a
consumer depends on: stdout is machine-readable, the report always validates, the run is
deterministic, and no failure anywhere takes the audit down.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import _scoring as S  # noqa: E402
import fixture_server  # noqa: E402
import run_audit  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
ENTRYPOINT = SCRIPTS / "run_audit.py"
REPORT_SCHEMA = json.loads(
    (REPO_ROOT / "skills" / "audit-orchestrator" / "references" / "report-schema.json")
    .read_text(encoding="utf-8"))


def run_cli(*args, timeout=180):
    return subprocess.run([sys.executable, str(ENTRYPOINT), *args],
                          capture_output=True, text=True, timeout=timeout,
                          cwd=str(REPO_ROOT))


@pytest.fixture(scope="module")
def config():
    return S.load_config()


@pytest.fixture(scope="module")
def local_config(config):
    """Config permitting the localhost fixture server (documented test-only escape hatch)."""
    return {**config, "fetch": {**config["fetch"], "allow_private_hosts": True}}


@pytest.fixture(scope="module")
def broken_report(config):
    return run_audit.audit(None, str(FIXTURES / "broken_page.html"), config)


@pytest.fixture(scope="module")
def healthy_report(config):
    return run_audit.audit(None, str(FIXTURES / "healthy_page.html"), config)


# --- Output contract -------------------------------------------------------------------------
def test_stdout_is_only_json():
    """A consumer must be able to pipe stdout straight into a JSON parser."""
    proc = run_cli("--html-file", str(FIXTURES / "healthy_page.html"))
    assert proc.returncode == 0
    report = json.loads(proc.stdout)          # would raise if logs leaked into stdout
    assert report["site"] == "healthy_page.html"


def test_logs_go_to_stderr():
    proc = run_cli("--html-file", str(FIXTURES / "broken_page.html"))
    assert proc.stdout.lstrip().startswith("{")
    for line in proc.stdout.splitlines():
        assert not line.startswith(("INFO", "WARNING", "ERROR"))


@pytest.mark.parametrize("fixture", ["healthy_page.html", "broken_page.html",
                                     "non_english_page.html", "spa_hydrating.html"])
def test_report_validates_against_schema(fixture, config):
    report = run_audit.audit(None, str(FIXTURES / fixture), config)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert run_audit.validate_report(report) == []


@pytest.mark.parametrize("fixture", ["healthy_page.html", "broken_page.html",
                                     "non_english_page.html"])
def test_summary_invariant_holds(fixture, config):
    s = run_audit.audit(None, str(FIXTURES / fixture), config)["summary"]
    assert s["total_findings"] == s["critical"] + s["high"] + s["medium"]


def test_mandatory_floor_fields_present(healthy_report):
    """The brief's mandated schema floor, checked explicitly rather than only via the schema."""
    assert set(healthy_report) >= {"site", "audited_at", "summary", "findings"}
    assert set(healthy_report["summary"]) >= {"total_findings", "critical", "high", "medium"}
    for finding in healthy_report["findings"]:
        assert set(finding) >= {"id", "title", "severity", "evidence", "suggested_action"}
        assert set(finding["suggested_action"]) >= {"summary", "priority"}


# --- Determinism -------------------------------------------------------------------------------
def test_repeat_runs_are_identical_except_timestamps(config):
    a = run_audit.audit(None, str(FIXTURES / "broken_page.html"), config)
    b = run_audit.audit(None, str(FIXTURES / "broken_page.html"), config)
    for report in (a, b):
        report.pop("audited_at", None)
        report["diagnostics"].pop("total_ms", None)
        report["diagnostics"].pop("crawl_ms", None)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_finding_ids_are_sequential_and_severity_ordered(broken_report):
    findings = broken_report["findings"]
    assert [f["id"] for f in findings] == [f"F-{i:03d}" for i in range(1, len(findings) + 1)]
    rank = {"critical": 0, "high": 1, "medium": 2}
    severities = [rank[f["severity"]] for f in findings]
    assert severities == sorted(severities)


# --- Findings quality ---------------------------------------------------------------------------
def test_findings_state_the_problem_not_the_ideal(broken_report):
    """A finding titled 'Mobile viewport is declared' when it is missing reads backwards.

    Registry `title`/`plain_summary` describe what GOOD looks like; findings must use the failure
    phrasing instead.
    """
    titles = [f["title"] for f in broken_report["findings"]]
    assert titles, "fixture should produce findings"
    assert not any(t == "Mobile viewport is declared" for t in titles)
    viewport = [f for f in broken_report["findings"] if f["check_id"] == "orientation.viewport_meta"]
    if viewport:
        assert viewport[0]["title"] == "Mobile viewport is not declared"


def test_every_finding_carries_the_reasoning_chain(broken_report):
    """Every finding carries its full reasoning chain: signal -> measurement -> threshold ->
    evidence -> impact -> remediation."""
    for f in broken_report["findings"]:
        assert f["signal"] and f["impact"] and f["evidence"]
        assert f["suggested_action"]["summary"]
        assert f["check_id"] and f["category"] and f["confidence"] in ("verified", "heuristic")


def test_partial_state_produces_a_finding(config):
    """A partial is a real defect (e.g. an over-long title) and must not be silently dropped."""
    html = ("<html lang='en'><head><title>" + "x" * 200 + "</title></head>"
            "<body><main><h1>H</h1><p>Some content here for the page body.</p></main></body></html>")
    tmp = REPO_ROOT / "tests" / "fixtures" / "_tmp_partial.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        report = run_audit.audit(None, str(tmp), config)
        ids = {f["check_id"] for f in report["findings"]}
        assert "content.title_descriptive" in ids
    finally:
        tmp.unlink(missing_ok=True)


def test_points_recoverable_never_inverts_severity(broken_report):
    """A medium finding must not outrank a critical one.

    It did: the denominator was the currently-measurable weight, so in a suppressed scan a medium
    viewport finding scored +50.0 against a critical render finding at +20.0.
    """
    by_sev = {}
    for f in broken_report["findings"]:
        by_sev.setdefault(f["severity"], []).append(f["points_recoverable"])
    if "critical" in by_sev and "medium" in by_sev:
        assert max(by_sev["critical"]) > max(by_sev["medium"])


def test_points_recoverable_is_stable_under_suppression(config):
    """The same check must be worth the same points whether or not others were suppressed."""
    broken = run_audit.audit(None, str(FIXTURES / "broken_page.html"), config)
    spa = run_audit.audit(None, str(FIXTURES / "spa_hydrating.html"), config)

    def pts(report, check_id):
        return next((f["points_recoverable"] for f in report["findings"]
                     if f["check_id"] == check_id), None)

    a, b = pts(broken, "render.content_without_js"), pts(spa, "render.content_without_js")
    if a is not None and b is not None:
        assert a == b


# --- Scoring surfaced correctly -------------------------------------------------------------------
def test_category_coverage_accompanies_category_scores(healthy_report):
    s = healthy_report["summary"]
    assert set(s["category_coverage"]) == set(s["category_scores"])


def test_thin_scan_is_exposed_by_coverage(config):
    """A client-rendered shell suppresses most checks; coverage must reveal it."""
    report = run_audit.audit(None, str(FIXTURES / "spa_hydrating.html"), config)
    assert report["summary"]["coverage"] < 0.5


def test_non_english_site_is_not_penalised(config):
    """Generalization, end-to-end: a good German page scores well, with the gap shown as coverage."""
    report = run_audit.audit(None, str(FIXTURES / "non_english_page.html"), config)
    assert report["summary"]["discoverability_score"] > 80
    assert report["summary"]["coverage"] < 1.0
    assert report["diagnostics"]["language_supported"] is False


# --- `partial` semantics ---------------------------------------------------------------------------
def test_complete_scan_is_not_marked_partial(healthy_report):
    """Suppression and i18n gating are the model working, NOT an incomplete scan.

    Marking any unknown as partial made a fully successful audit report partial=True with a bogus
    reason of 'render_failed'.
    """
    assert healthy_report["partial"] is False
    assert healthy_report["partial_reason"] is None


def test_blocked_page_is_marked_partial(local_config):
    with fixture_server.running() as base:
        report = run_audit.audit(f"{base}/consent-wall", None, local_config, force_tier="heuristic")
    assert report["partial"] is True
    assert report["partial_reason"] == "blocked"


# --- Resilience ------------------------------------------------------------------------------------
def test_missing_analyzer_does_not_crash_the_audit(config, monkeypatch):
    monkeypatch.setattr(run_audit, "ANALYZERS",
                        [("does-not-exist", "nope.py")] + run_audit.ANALYZERS[1:])
    report = run_audit.audit(None, str(FIXTURES / "healthy_page.html"), config)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert any(e["type"] == "missing" for e in report["diagnostics"]["errors"])
    assert report["partial"] is True and report["partial_reason"] == "analyzer_failed"


def test_unreachable_host_still_produces_a_valid_report(config):
    report = run_audit.audit("http://127.0.0.1:1/", None, config)
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert report["diagnostics"]["errors"]


def test_the_page_list_names_only_pages_that_did_not_load(healthy_report, local_config):
    """Loaded pages are already in pages_checked; the page list exists to surface exceptions.

    An entry per successful page used to restate every audited URL with status "ok", which buried
    the one line a reader needs when a page was blocked or failed.
    """
    diagnostics = healthy_report["diagnostics"]
    assert diagnostics["pages_checked"], "every audited URL must still be listed"
    assert diagnostics["pages"] == []

    with fixture_server.running() as base:
        blocked = run_audit.audit(f"{base}/consent-wall", None, local_config, force_tier="heuristic")
    pages = blocked["diagnostics"]["pages"]
    assert pages, "a blocked page must never be dropped from the page list"
    assert all(p["status"] != "ok" for p in pages), pages
    assert {p["url"] for p in pages} <= set(blocked["diagnostics"]["pages_checked"])
    assert any(p["status"] == "blocked" and p["reason"] == "consent_wall" for p in pages), pages


def test_unknown_check_ids_from_an_analyzer_are_ignored(config):
    registry = S.load_registry()
    errors = []
    rows = [{"check_id": "not.a.real.check", "state": "fail"},
            {"check_id": "access.http_ok", "state": "pass"}]
    results = run_audit.to_check_results(rows, registry, errors)
    assert [r.check_id for r in results] == ["access.http_ok"]
    assert any(e["type"] == "unknown_check" for e in errors)


def test_bad_state_from_an_analyzer_is_ignored(config):
    registry = S.load_registry()
    errors = []
    results = run_audit.to_check_results(
        [{"check_id": "access.http_ok", "state": "definitely-not-a-state"}], registry, errors)
    assert results == []
    assert any(e["type"] == "bad_state" for e in errors)


# --- Security --------------------------------------------------------------------------------------
def test_cli_exposes_no_ssrf_bypass_flag():
    """`allow_private_hosts` disables the internal-network guard and must be config-only."""
    proc = run_cli("--help")
    assert "private" not in proc.stdout.lower()
    assert "insecure" not in proc.stdout.lower()


def test_ssrf_guard_active_by_default(config):
    assert config["fetch"]["allow_private_hosts"] is False
    report = run_audit.audit("http://169.254.169.254/latest/meta-data/", None, config)
    assert any("non-public" in (e.get("message") or "") or e.get("type") == "unsafe_url"
               for e in report["diagnostics"]["errors"])


def test_analyzer_env_excludes_secrets(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-propagate")
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-propagate")
    env = run_audit._analyzer_env()
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "PATH" in env


# --- CI mode -----------------------------------------------------------------------------------------
def test_ci_mode_fails_on_critical_findings():
    proc = run_cli("--html-file", str(FIXTURES / "broken_page.html"), "--ci")
    report = json.loads(proc.stdout)
    assert report["summary"]["critical"] > 0
    assert proc.returncode == 1


def test_ci_mode_passes_a_clean_site():
    proc = run_cli("--html-file", str(FIXTURES / "healthy_page.html"), "--ci")
    report = json.loads(proc.stdout)
    assert report["summary"]["critical"] == 0
    assert proc.returncode == 0


# --- Live pipeline ------------------------------------------------------------------------------------
def test_end_to_end_against_fixture_server(local_config):
    with fixture_server.running() as base:
        report = run_audit.audit(f"{base}/healthy", None, local_config, force_tier="heuristic")
    Draft202012Validator(REPORT_SCHEMA).validate(report)
    assert report["summary"]["discoverability_score"] > 60
    assert report["diagnostics"]["robots_checked"] is True


# --- Finding wording must match the check STATE ----------------------------------------------------
# Two mirrored defects found in production runs:
#   * A FAILING check used the positive title ("Mobile viewport is declared" when it was missing).
#   * github.com: a PARTIAL check used the absolute failure title ("No organization or person entity
#     declared") while its own evidence said identity WAS declared via Open Graph.

def _registry_raw():
    return json.loads((REPO_ROOT / "config" / "checks.json").read_text(encoding="utf-8"))["checks"]


def _checks_that_can_emit_partial():
    """Derived from analyzer source, so the guard stays correct as checks change."""
    import re
    emitting = set()
    for script in (REPO_ROOT / "skills").glob("*/scripts/*.py"):
        src = script.read_text(encoding="utf-8")
        for cid in re.findall(r'result\(\s*"([a-z_]+\.[a-z_]+)"\s*,\s*"partial"', src):
            emitting.add(cid)
    return emitting


def test_checks_emitting_partial_have_partial_wording():
    """A partial must never be described with an absolute 'No X' failure title."""
    by_id = {c["id"]: c for c in _registry_raw()}
    emitting = _checks_that_can_emit_partial()
    assert emitting, "expected to find checks that emit partial"

    offenders = []
    for cid in sorted(emitting):
        check = by_id.get(cid)
        if check is None:
            continue
        if not check.get("partial_title"):
            fail_title = check.get("failure_title", "")
            if fail_title.startswith(("No ", "Few ")):
                offenders.append(f"{cid}: partial would read {fail_title!r}")
    assert not offenders, "absolute failure wording reused for a partial: " + "; ".join(offenders)


def test_partial_finding_uses_partial_wording(config, tmp_path):
    """End-to-end: an Open-Graph-only page must not be told it declared no entity."""
    page = tmp_path / "og_only.html"
    page.write_text(
        '<html lang="en"><head><title>GitHub</title>'
        '<meta property="og:site_name" content="GitHub">'
        '<meta property="og:url" content="https://github.com/">'
        '<meta name="viewport" content="width=device-width"></head>'
        '<body><main><h1>GitHub</h1><p>GitHub is where over 100 million developers build '
        'software together every single day of the year.</p></main></body></html>',
        encoding="utf-8")

    report = run_audit.audit(None, str(page), config)
    org = next((f for f in report["findings"]
                if f["check_id"] == "entity.organization_declared"), None)
    assert org is not None
    assert org["title"] == "Identity is declared only weakly"
    assert "No organization" not in org["title"]
    # The title must not contradict its own evidence.
    assert "Open Graph" in org["evidence"]


def test_open_graph_identity_earns_partial_credit(config, tmp_path):
    """Open Graph names an entity, so it must score above a page declaring nothing at all."""
    common = ('<meta name="viewport" content="width=device-width"></head><body><main><h1>Acme</h1>'
              '<p>Acme Corp builds analytics tools for product teams across Europe.</p>'
              '</main></body></html>')
    og = tmp_path / "og.html"
    og.write_text('<html lang="en"><head><title>Acme Corp analytics</title>'
                  '<meta property="og:site_name" content="Acme Corp">'
                  '<meta property="og:url" content="https://acme.test/">' + common, encoding="utf-8")
    bare = tmp_path / "bare.html"
    bare.write_text('<html lang="en"><head><title>Acme Corp analytics</title>' + common,
                    encoding="utf-8")

    og_score = run_audit.audit(None, str(og), config)["summary"]["category_scores"]["entity_trust"]
    bare_score = run_audit.audit(None, str(bare), config)["summary"]["category_scores"]["entity_trust"]
    assert og_score > bare_score, "Open Graph identity must beat declaring nothing"


def test_open_graph_still_scores_below_full_json_ld(config, tmp_path):
    """...but it must NOT approach a real schema.org entity with sameAs anchoring."""
    common = ('<meta name="viewport" content="width=device-width"></head><body><main><h1>Acme</h1>'
              '<p>Acme Corp builds analytics tools for product teams across Europe.</p>'
              '</main></body></html>')
    og = tmp_path / "og2.html"
    og.write_text('<html lang="en"><head><title>Acme Corp analytics</title>'
                  '<meta property="og:site_name" content="Acme Corp">'
                  '<meta property="og:url" content="https://acme.test/">' + common, encoding="utf-8")
    rich = tmp_path / "rich.html"
    rich.write_text('<html lang="en"><head><title>Acme Corp analytics</title>'
                    '<meta property="og:site_name" content="Acme Corp">'
                    '<meta property="og:type" content="website">'
                    '<meta property="og:title" content="Acme Corp">'
                    '<meta property="og:url" content="https://acme.test/">'
                    '<script type="application/ld+json">'
                    '{"@context":"https://schema.org","@type":"Organization","name":"Acme Corp",'
                    '"sameAs":["https://www.wikidata.org/wiki/Q42"]}</script>' + common,
                    encoding="utf-8")

    og_score = run_audit.audit(None, str(og), config)["summary"]["category_scores"]["entity_trust"]
    rich_score = run_audit.audit(None, str(rich), config)["summary"]["category_scores"]["entity_trust"]
    assert rich_score > og_score, "full JSON-LD + sameAs must outrank Open Graph alone"
