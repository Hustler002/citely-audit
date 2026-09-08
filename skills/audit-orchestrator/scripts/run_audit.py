#!/usr/bin/env python3
"""Citely audit orchestrator — the marketplace entrypoint.

Usage:
  python run_audit.py --url https://example.com
  python run_audit.py --html-file page.html          # offline, zero network

Pipeline (PLAN.md §3):
  Input -> Safe Fetch -> Render -> Normalized Artifact -> Analysis -> Findings -> Scoring -> Report
           (this file owns ALL network I/O)          |  (pure subprocesses)          |
                                                     +-- single cross-boundary contract

Contract:
  * The report goes to STDOUT and nothing else does. All logs go to stderr, so the output is
    machine-consumable by a pipe.
  * The report always validates against references/report-schema.json.
  * It NEVER crashes. Every stage records its failure into diagnostics.errors[] and the audit
    still emits a valid report — an audit that dies tells the user nothing.

Security note: `fetch.allow_private_hosts` disables the SSRF guard and is deliberately NOT exposed
as a CLI flag. It exists only so the test suite can reach the localhost fixture server, and enabling
it from untrusted input would reintroduce full SSRF exposure. See `_assert_no_ssrf_bypass_via_cli`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import _artifact as artifact_mod
import _render as render_mod
import _safe_fetch as fetch_mod
import _scoring as scoring

log = logging.getLogger("orchestrator")

HERE = Path(__file__).resolve().parent
SKILLS_DIR = HERE.parent.parent
REPO_ROOT = SKILLS_DIR.parent
REFERENCES = HERE.parent / "references"
REPORT_SCHEMA_PATH = REFERENCES / "report-schema.json"
CHECKS_PATH = REPO_ROOT / "config" / "checks.json"
CONFIG_PATH = REPO_ROOT / "config" / "scoring-config.json"

# Analyzer skills, invoked as subprocesses. Order is part of the deterministic finding-id tie-break.
ANALYZERS = [
    ("crawl-render-extraction-audit", "crawl_render_extract.py"),
    ("entity-corroboration-audit", "entity_corroboration.py"),
    ("quotability-density-audit", "quotability_density.py"),
    ("engagement-orientation-audit", "engagement_orientation.py"),
]

ANALYZER_TIMEOUT_S = 60
MAX_ANALYZER_STDOUT = 8 * 1024 * 1024

# States that constitute a reportable defect. `partial` counts: a title that exists but is far too
# long is a genuine, actionable problem, and omitting it would be a miss.
FINDING_STATES = {scoring.FAIL, scoring.PARTIAL}


# --- helpers ------------------------------------------------------------------------------------
def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _analyzer_env() -> dict:
    """Minimal environment for child processes.

    An explicit allowlist rather than os.environ: analyzer subprocesses have no need for the
    parent's tokens or credentials, and passing them along would be gratuitous exposure.
    """
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "PYTHONIOENCODING")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_analyzer(skill: str, script: str, artifact_path: Path, errors: list,
                 deadline: float | None) -> list:
    """Invoke one analyzer skill as a subprocess and parse its check states.

    Any failure — crash, timeout, garbage stdout — degrades that skill's checks to `unknown` via
    the scoring engine's "not measured" path, and is recorded. One broken analyzer must never take
    the audit down with it.
    """
    path = SKILLS_DIR / skill / "scripts" / script
    if not path.exists():
        errors.append({"stage": "analyze", "type": "missing", "message": f"{skill}: script not found"})
        return []

    timeout = ANALYZER_TIMEOUT_S
    if deadline is not None:
        timeout = max(1, min(timeout, int(deadline - time.monotonic())))

    try:
        proc = subprocess.run(
            [sys.executable, str(path), "--artifact", str(artifact_path), "--config", str(CHECKS_PATH)],
            capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT),
            env=_analyzer_env(),
        )
    except subprocess.TimeoutExpired:
        errors.append({"stage": "analyze", "type": "timeout", "message": f"{skill}: exceeded {timeout}s"})
        return []
    except Exception as exc:
        errors.append({"stage": "analyze", "type": "spawn_failed",
                       "message": f"{skill}: {type(exc).__name__}"})
        return []

    if proc.returncode != 0:
        errors.append({"stage": "analyze", "type": "exit_code",
                       "message": f"{skill}: exited {proc.returncode}"})
    if len(proc.stdout) > MAX_ANALYZER_STDOUT:
        errors.append({"stage": "analyze", "type": "oversized_output", "message": skill})
        return []

    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        errors.append({"stage": "analyze", "type": "bad_json",
                       "message": f"{skill}: stdout was not valid JSON"})
        return []
    return rows if isinstance(rows, list) else []


def to_check_results(rows: list, registry, errors: list) -> list:
    """Convert analyzer output to CheckResult, dropping anything the registry does not know.

    Analyzers already guarantee valid ids, but the orchestrator must not trust that: a stale skill
    or a hand-edited artifact could still produce drift, and an unknown id makes scoring raise.
    """
    results, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = row.get("check_id")
        if cid not in registry.checks:
            errors.append({"stage": "analyze", "type": "unknown_check",
                           "message": f"ignored unrecognised check id {cid!r}"})
            continue
        if cid in seen:
            continue
        seen.add(cid)
        try:
            results.append(scoring.CheckResult(
                check_id=cid, state=row.get("state"),
                measurement=row.get("measurement"), evidence=row.get("evidence"),
                selector=row.get("selector"), page_url=row.get("page_url"),
                reason=row.get("reason")))
        except scoring.ScoringConfigError as exc:
            errors.append({"stage": "analyze", "type": "bad_state", "message": str(exc)})
    return results


def build_findings(resolved: dict, registry, config: dict, raw_registry: dict) -> list:
    """Turn failing checks into findings, using the registry as the single source of report wording.

    Each finding carries the full reasoning chain the rubric asks for:
    signal -> measurement -> threshold -> evidence -> impact -> remediation.
    """
    findings = []
    for check_id in registry.order:
        state = resolved.get(check_id)
        if state is None or state.state not in FINDING_STATES:
            continue
        check = registry.checks[check_id]
        meta = raw_registry.get(check_id, {})
        threshold = meta.get("threshold")
        if isinstance(threshold, dict):
            threshold = {k: v for k, v in threshold.items() if not k.startswith("_")}

        # A finding states the PROBLEM, and states it at the right STRENGTH. `title`/`plain_summary`
        # describe what good looks like ("Mobile viewport is declared" for a missing viewport), while
        # the failure wording overstates a partial ("No organization declared" when Open Graph names
        # one). Pick by state, falling back where one phrasing covers both.
        if state.state == scoring.PARTIAL:
            finding_title = (meta.get("partial_title") or meta.get("failure_title") or check.title)
            finding_summary = (meta.get("partial_summary") or meta.get("failure_summary")
                               or check.plain_summary)
        else:
            finding_title = meta.get("failure_title") or check.title
            finding_summary = meta.get("failure_summary") or check.plain_summary

        findings.append({
            "title": finding_title,
            "severity": check.severity,
            "category": check.category,
            "check_id": check_id,
            "confidence": check.confidence_class,
            "plain_summary": finding_summary,
            "signal": check.signal,
            "measurement": state.measurement,
            "threshold": threshold,
            "evidence": state.evidence or f"{check.title}: measured state '{state.state}'",
            "selector": state.selector,
            "page_url": state.page_url,
            "impact": check.impact,
            "points_recoverable": scoring.points_recoverable(check_id, resolved, registry, config),
            "suggested_action": {
                "summary": meta.get("remediation") or check.plain_summary,
                "priority": check.severity,
            },
        })
    return findings


def _partial_reason(artifact: dict, resolved: dict, errors: list) -> tuple:
    """Whether the SCAN was incomplete, and why.

    `partial` means we could not run the audit we intended — not merely that some checks came back
    `unknown`. Dependency suppression and i18n gating are the model working correctly, not an
    incomplete scan, and `coverage` already communicates their effect. Treating any unknown as
    partial would flag nearly every audit as incomplete and make the flag meaningless.
    """
    pages = [p for p in (artifact.get("pages") or []) if isinstance(p, dict)]

    if artifact.get("blocked_before_fetch"):
        return True, "blocked"
    if any(p.get("blocked_kind") for p in pages):
        return True, "blocked"
    if any(p.get("skip_reason") == "budget" or p.get("status") == "skipped" for p in pages):
        return True, "budget"
    if any(e.get("stage") == "analyze" for e in errors):
        return True, "analyzer_failed"
    if any(e.get("stage") == "render" for e in errors):
        return True, "render_failed"
    if any(p.get("skip_reason") == "fetch_failed" for p in pages):
        return True, "fetch_failed"
    if any(p.get("status") == "error" for p in pages):
        return True, "fetch_failed"
    return False, None


def _assert_no_ssrf_bypass_via_cli(parser) -> None:
    """The SSRF kill-switch must be unreachable from the command line.

    `fetch.allow_private_hosts` is a config-file, test-only escape hatch. If it ever became a CLI
    flag, a caller could disable the internal-network protection on untrusted input. This asserts
    the flag does not exist rather than relying on nobody adding it.
    """
    for action in parser._actions:
        for option in action.option_strings:
            if "private" in option or "allow-ssrf" in option or "insecure" in option:
                raise SystemExit(f"refusing to run: CLI exposes an SSRF bypass flag ({option})")


# --- main ---------------------------------------------------------------------------------------
def audit(url: str | None, html_file: str | None, config: dict, *,
          allow_external: bool = False, force_tier: str | None = None) -> dict:
    started = time.monotonic()
    deadline = fetch_mod.deadline_from_config(config, started)
    errors: list = []

    registry = scoring.load_registry(CHECKS_PATH)
    raw_registry = {c["id"]: c for c in load_json(CHECKS_PATH).get("checks", [])}

    # --- acquire -------------------------------------------------------------------------------
    if html_file:
        html = Path(html_file).read_text(encoding="utf-8", errors="replace")
        lang, source = artifact_mod.detect_language(html)
        artifact = {
            "requested_url": f"file://{html_file}", "final_url": f"file://{html_file}",
            "fetched_at": artifact_mod.utc_now(),
            "user_agent": config.get("fetch", {}).get("user_agent", ""),
            "robots": {"checked": False, "allowed": True, "crawl_delay": None,
                       "status": None, "sitemaps": []},
            "ai_crawlers": {"determinable": False, "allowed": {}, "blocked": []},
            "language": {"detected": lang, "source": source,
                         "supported": artifact_mod.language_supported(lang, config)},
            "pages": [{"url": f"file://{html_file}", "role": "homepage", "status": "ok",
                       "raw": {"status": 200, "content_type": "text/html", "byte_size": len(html),
                               "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
                               "html": html},
                       "meta_robots": None, "x_robots_tag": None}],
            "external_corroboration": None, "blocked_before_fetch": False,
            "timing": {"fetch_ms": 0, "render_ms": 0, "total_ms": 0}, "errors": [],
        }
        site = Path(html_file).name
    else:
        artifact = artifact_mod.build_artifact(url, config, deadline=deadline, force_tier=force_tier)
        errors.extend(artifact.get("errors") or [])
        site = (artifact.get("final_url") or url or "").replace("https://", "").replace("http://", "")
        site = site.split("/")[0] or (url or "unknown")

    # --- analyze -------------------------------------------------------------------------------
    rows: list = []
    tmpdir = None
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="citely-")
        os.chmod(tmpdir, 0o700)
        artifact_path = Path(tmpdir) / "artifact.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        for skill, script in ANALYZERS:
            rows.extend(run_analyzer(skill, script, artifact_path, errors, deadline))
    except Exception as exc:
        errors.append({"stage": "analyze", "type": "setup_failed", "message": type(exc).__name__})
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # --- score ---------------------------------------------------------------------------------
    results = to_check_results(rows, registry, errors)
    language_supported = bool((artifact.get("language") or {}).get("supported"))
    resolved = scoring.resolve_states(
        results, registry, config,
        language_supported=language_supported,
        blocked_before_fetch=bool(artifact.get("blocked_before_fetch")))

    cat_scores = scoring.category_scores(resolved, registry, config)
    overall = scoring.overall_score(cat_scores, config)
    findings = scoring.assign_finding_ids(
        build_findings(resolved, registry, config, raw_registry), config)
    summary = scoring.summarize(
        findings, cat_scores, overall,
        scoring.score_confidence(resolved, registry, config),
        coverage_ratio=scoring.coverage(resolved, registry, config),
        cat_coverage=scoring.category_coverage(resolved, registry, config))

    partial, reason = _partial_reason(artifact, resolved, errors)
    pages = [p for p in (artifact.get("pages") or []) if isinstance(p, dict)]
    rendered = (pages[0].get("rendered") or {}) if pages else {}

    return {
        "site": site or "unknown",
        "audited_at": artifact.get("fetched_at") or artifact_mod.utc_now(),
        "summary": summary,
        "diagnostics": {
            "total_ms": int((time.monotonic() - started) * 1000),
            "crawl_ms": (artifact.get("timing") or {}).get("total_ms", 0),
            "playwright_available": bool(rendered.get("available")),
            "render_mode": rendered.get("mode") or render_mod.TIER_B,
            "pages_checked": [p.get("url") for p in pages],
            "pages": [{"url": p.get("url"), "status": p.get("status", "error"),
                       "http_status": (p.get("raw") or {}).get("status"),
                       "reason": p.get("blocked_kind") or p.get("skip_reason")} for p in pages],
            "language_detected": (artifact.get("language") or {}).get("detected"),
            "language_supported": language_supported,
            "user_agent": artifact.get("user_agent", ""),
            "robots_checked": bool((artifact.get("robots") or {}).get("checked")),
            "external_lookup": bool(allow_external),
            "checks_evaluated": sum(1 for r in resolved.values() if r.state != scoring.UNKNOWN),
            "checks_unknown": sum(1 for r in resolved.values() if r.state == scoring.UNKNOWN),
            "errors": errors,
        },
        "partial": partial,
        "partial_reason": reason,
        "findings": findings,
        "recommendations": [],   # populated by remediation-advisor in Phase 7
    }


def validate_report(report: dict) -> list:
    """Schema-validate and check the invariant the schema cannot express."""
    problems = []
    try:
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator(load_json(REPORT_SCHEMA_PATH))
        problems = [e.message for e in validator.iter_errors(report)][:5]
    except ImportError:
        problems.append("jsonschema not installed; schema validation skipped")
    s = report.get("summary", {})
    if s.get("total_findings") != s.get("critical", 0) + s.get("high", 0) + s.get("medium", 0):
        problems.append("summary invariant violated: total != critical + high + medium")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Citely — brand AI-readiness audit.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Homepage URL to audit.")
    source.add_argument("--html-file", help="Local HTML file to audit offline (no network).")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--allow-external", action="store_true",
                        help="Permit the optional Wikidata corroboration lookup.")
    parser.add_argument("--ci", action="store_true",
                        help="Exit non-zero when any critical finding is present.")
    _assert_no_ssrf_bypass_via_cli(parser)
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        config = load_json(Path(args.config))
    except Exception as exc:
        log.error("could not read config %s: %s", args.config, exc)
        return 2

    if config.get("fetch", {}).get("allow_private_hosts"):
        log.warning("fetch.allow_private_hosts is ENABLED — the SSRF guard is disabled. "
                    "This must only ever be true in tests.")

    try:
        report = audit(args.url, args.html_file, config,
                       allow_external=args.allow_external)
    except Exception as exc:
        # Belt and braces: the stages already trap their own failures, but the entrypoint must
        # still emit something valid rather than a traceback.
        log.exception("audit failed unexpectedly")
        report = {"site": args.url or args.html_file or "unknown",
                  "audited_at": artifact_mod.utc_now(),
                  "summary": {"total_findings": 0, "critical": 0, "high": 0, "medium": 0},
                  "findings": [], "partial": True, "partial_reason": "analyzer_failed",
                  "diagnostics": {"errors": [{"stage": "orchestrator", "type": "fatal",
                                              "message": type(exc).__name__}]}}

    for problem in validate_report(report):
        log.error("report validation: %s", problem)

    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

    if args.ci and report.get("summary", {}).get("critical", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
