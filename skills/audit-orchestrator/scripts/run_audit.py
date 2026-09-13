#!/usr/bin/env python3
"""Citely audit orchestrator — the marketplace entrypoint.

Usage:
  python run_audit.py --url https://example.com
  python run_audit.py --html-file page.html          # offline, zero network

Pipeline:
  Input -> Safe Fetch -> Render -> Normalized Artifact -> Analysis -> Findings -> Scoring -> Report
           (this file owns ALL network I/O)          |  (pure subprocesses)          |
                                                     +-- single cross-boundary contract

Contract:
  * The report goes to STDOUT and nothing else does — see `configure_logging` and
    `stdout_reserved_for_report`, which ENFORCE that rather than relying on everyone remembering.
    All logs go to stderr, so the output is machine-consumable by a pipe.
  * The report always validates against references/report-schema.json.
  * It NEVER crashes. Every stage records its failure into diagnostics.errors[] and the audit
    still emits a valid report — an audit that dies tells the user nothing.

Security note: `fetch.allow_private_hosts` disables the SSRF guard and is deliberately NOT exposed
as a CLI flag. It exists only so the test suite can reach the localhost fixture server, and enabling
it from untrusted input would reintroduce full SSRF exposure. See `_assert_no_ssrf_bypass_via_cli`.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import _artifact as artifact_mod
import _narrative as narrative
import _render as render_mod
import _safe_fetch as fetch_mod
import _scoring as scoring

log = logging.getLogger("orchestrator")

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"
MAX_FORWARDED_STDERR = 20000


def configure_logging(level: int = logging.INFO) -> None:
    """Send every log record to stderr, and mean it.

    `logging.basicConfig` is a SILENT NO-OP when the root logger already has a handler. Verified,
    not assumed: with a dependency calling `basicConfig(stream=sys.stdout)` at import time, our
    call changes nothing, the record lands on STDOUT and even our format is ignored. That is one
    `import` away from putting plain text in front of the JSON a CI job parses.

    `force=True` removes any handler installed before us. It is the load-bearing argument here;
    without it this function is decoration.
    """
    logging.basicConfig(stream=sys.stderr, level=level, force=True, format=LOG_FORMAT)


class _StderrRedirect:
    """A stdout stand-in that diverts everything to stderr and remembers that it happened."""

    def __init__(self, stderr):
        self._stderr = stderr
        self.leaked_chars = 0

    def write(self, text):
        if text:
            self.leaked_chars += len(text)
        return self._stderr.write(text)

    def __getattr__(self, name):
        return getattr(self._stderr, name)


@contextlib.contextmanager
def stdout_reserved_for_report():
    """Hold stdout closed for the duration of the audit, so only the report can reach it.

    `configure_logging` fixes handlers that exist when we start. It cannot fix a library imported
    LATER in the run — Playwright is imported inside the render stage — nor a bare `print()` in any
    dependency, which is not logging at all and carries no stream configuration to correct.

    While this is active `sys.stdout` IS stderr, so a handler constructed mid-run with
    `stream=sys.stdout` binds to stderr too. Anything written is counted and reported, because a
    silent diversion would hide a real bug just as effectively as the leak would cause one.
    """
    real_stdout = sys.stdout
    proxy = _StderrRedirect(sys.stderr)
    sys.stdout = proxy
    try:
        yield real_stdout
    finally:
        sys.stdout = real_stdout
        if proxy.leaked_chars:
            log.warning("%d characters were written to stdout during the audit and were diverted "
                        "to stderr to keep the report parseable", proxy.leaked_chars)


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

# The advisor is NOT an analyzer and is deliberately kept out of the list above: it emits advice,
# not check states, it runs after scoring rather than before it, and it takes a different argument
# set. Folding it into the fan-out would mean one loop pretending two contracts are the same one.
ADVISOR = ("remediation-advisor", "advise.py")

# How a child process's output is decoded. `text=True` alone decodes with
# `locale.getpreferredencoding(False)` — cp1252 on a default Windows install — while we explicitly
# tell the child to WRITE utf-8 via PYTHONIOENCODING. That mismatch has two failure modes, and the
# quiet one is worse:
#
#   * SILENT CORRUPTION, the common case. Any non-ASCII evidence comes back mojibake: "café" as
#     "cafÃ©", Devanagari as "à¤°à¤¾à¤œ…". The audit completes and the report is wrong.
#   * A CRASH, when a byte lands on one of the five cp1252 has no mapping for (0x81, 0x8d, 0x8f,
#     0x90, 0x9d). `subprocess`'s reader thread raises, the exception is swallowed, and `stdout`
#     is left as None — which is how a Hindi page produced `TypeError: object of type 'NoneType'
#     has no len()` and took the whole audit down.
#
# `errors="replace"` because this is the boundary holding page-derived bytes: a malformed sequence
# must degrade one character, never kill the run.
CHILD_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}

ANALYZER_TIMEOUT_S = 60
ADVISOR_TIMEOUT_S = 30
MAX_ANALYZER_STDOUT = 8 * 1024 * 1024
EMPTY_ADVICE = {"corrective": [], "recommendations": []}

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


def forward_child_stderr(skill: str, raw) -> None:
    """Relay a child's diagnostics, tagged, capped, and unable to kill the run.

    Two hazards, both reachable on a non-English site. The stream may be None, because a decode
    failure in `subprocess`'s reader thread is swallowed and leaves the attribute unset. And the
    text may be unprintable on the parent's OWN stderr: a cp1252 console raises UnicodeEncodeError
    on Devanagari, so merely forwarding a child's message could take the audit down. The child is
    the component holding page-derived text, so neither may be fatal.
    """
    if not raw:
        return
    text = raw if isinstance(raw, str) else str(raw)
    for line in text[:MAX_FORWARDED_STDERR].splitlines():
        if line.strip():
            _write_safely(sys.stderr, f"[{skill}] {line}\n")
    if len(text) > MAX_FORWARDED_STDERR:
        _write_safely(sys.stderr, f"[{skill}] ... stderr truncated at {MAX_FORWARDED_STDERR} chars\n")


def _write_safely(stream, text: str) -> None:
    """Write text the stream may not be able to encode, without ever raising."""
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    except Exception:
        pass


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
            capture_output=True, timeout=timeout, cwd=str(REPO_ROOT),
            env=_analyzer_env(), **CHILD_TEXT,
        )
    except subprocess.TimeoutExpired:
        errors.append({"stage": "analyze", "type": "timeout", "message": f"{skill}: exceeded {timeout}s"})
        return []
    except Exception as exc:
        errors.append({"stage": "analyze", "type": "spawn_failed",
                       "message": f"{skill}: {type(exc).__name__}"})
        return []

    # `capture_output=True` pipes the child's stderr as well as its stdout, so an analyzer's logs
    # were being swallowed entirely rather than reaching the operator. Forward them, tagged and
    # capped: they are the only diagnostic a failing analyzer produces, but the child is also the
    # component holding page-derived text, so it does not get an unbounded channel.
    forward_child_stderr(skill, proc.stderr)

    if proc.returncode != 0:
        errors.append({"stage": "analyze", "type": "exit_code",
                       "message": f"{skill}: exited {proc.returncode}"})
    stdout = proc.stdout or ""
    if len(stdout) > MAX_ANALYZER_STDOUT:
        errors.append({"stage": "analyze", "type": "oversized_output", "message": skill})
        return []

    try:
        rows = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        errors.append({"stage": "analyze", "type": "bad_json",
                       "message": f"{skill}: stdout was not valid JSON"})
        return []
    return rows if isinstance(rows, list) else []


def run_advisor(workdir: Path, artifact_path: Path, findings: list, resolved: dict,
                errors: list, deadline: float | None) -> dict:
    """Ask remediation-advisor for snippets and proactive suggestions.

    Enrichment, not analysis. Every failure here costs the reader their copy-paste snippets and
    nothing else: findings keep the registry's prose remediation, the score is untouched, and the
    report stays schema-valid. That is why it returns empty advice rather than raising.

    It receives the resolved check states as well as the findings, because a proactive suggestion
    has to know what PASSED. Findings only carry what failed, and a suggestion built from that
    alone would repeat a defect the reader has already been told about.
    """
    skill, script = ADVISOR
    path = SKILLS_DIR / skill / "scripts" / script
    if not path.exists():
        errors.append({"stage": "advise", "type": "missing", "message": f"{skill}: script not found"})
        return dict(EMPTY_ADVICE)

    timeout = ADVISOR_TIMEOUT_S
    if deadline is not None:
        timeout = max(1, min(timeout, int(deadline - time.monotonic())))

    try:
        findings_path = workdir / "findings.json"
        states_path = workdir / "check-states.json"
        findings_path.write_text(json.dumps(findings), encoding="utf-8")
        states_path.write_text(
            json.dumps({cid: r.state for cid, r in resolved.items()}), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(path), "--artifact", str(artifact_path),
             "--findings", str(findings_path), "--check-states", str(states_path),
             "--config", str(CHECKS_PATH)],
            capture_output=True, timeout=timeout, cwd=str(REPO_ROOT),
            env=_analyzer_env(), **CHILD_TEXT,
        )
    except subprocess.TimeoutExpired:
        errors.append({"stage": "advise", "type": "timeout", "message": f"{skill}: exceeded {timeout}s"})
        return dict(EMPTY_ADVICE)
    except Exception as exc:
        errors.append({"stage": "advise", "type": "spawn_failed",
                       "message": f"{skill}: {type(exc).__name__}"})
        return dict(EMPTY_ADVICE)

    forward_child_stderr(skill, proc.stderr)

    if proc.returncode != 0:
        errors.append({"stage": "advise", "type": "exit_code",
                       "message": f"{skill}: exited {proc.returncode}"})
    stdout = proc.stdout or ""
    if len(stdout) > MAX_ANALYZER_STDOUT:
        errors.append({"stage": "advise", "type": "oversized_output", "message": skill})
        return dict(EMPTY_ADVICE)

    try:
        advice = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        errors.append({"stage": "advise", "type": "bad_json",
                       "message": f"{skill}: stdout was not valid JSON"})
        return dict(EMPTY_ADVICE)
    if not isinstance(advice, dict):
        return dict(EMPTY_ADVICE)
    return {"corrective": advice.get("corrective") if isinstance(advice.get("corrective"), list) else [],
            "recommendations": advice.get("recommendations")
            if isinstance(advice.get("recommendations"), list) else [],
            # Carried through so an EMPTY recommendations list is explainable rather than
            # mysterious: it says which detectors were suppressed to avoid repeating a finding and
            # which stayed silent on language grounds. Silence with no account of itself is
            # indistinguishable from a broken stage.
            "diagnostics": advice.get("diagnostics") if isinstance(advice.get("diagnostics"), dict) else {}}


def apply_advice(findings: list, advice: dict) -> None:
    """Merge corrective advice into the findings, in place, by finding id.

    Keyed by finding id and not by check id: the same check can fail on more than one page, and a
    reader following F-004 must not be handed F-002's address. A finding with no entry keeps the
    registry's prose remediation, which is why the merge only ever ADDS fields.
    """
    by_id = {entry.get("finding_id"): entry for entry in advice.get("corrective") or []
             if isinstance(entry, dict) and entry.get("finding_id")}
    for finding in findings:
        entry = by_id.get(finding.get("id"))
        if not entry:
            continue
        action = finding.setdefault("suggested_action", {})
        for field in ("target", "snippet", "validation", "placeholders_remaining"):
            value = entry.get(field)
            if value:
                action[field] = value


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

    Each finding carries a complete reasoning chain, so a reader can retrace the verdict:
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

        severity = severity_for_state(check.severity, state.state)

        findings.append({
            "title": finding_title,
            "severity": severity,
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
                "priority": severity,
            },
        })
    return findings


# Strict 3-tier, no `low` (locked). A partial is graded one tier down from the check's declared
# severity, and `medium` is the floor.
SEVERITY_ORDER = ("critical", "high", "medium")


def severity_for_state(declared: str, state: str) -> str:
    """A finding is reported at the strength of what was actually measured.

    Severity used to come straight from the registry, so a PARTIAL was announced at the same
    strength as a total failure. dev.to was told twice, in `critical`, that its identity was
    undeclared: once for having no JSON-LD at all, and once for having only Open Graph — which is
    a partial that had already been awarded half credit. Two criticals for one root cause, and one
    of them describing something the site does have. Likewise a 13-character title, two characters
    under the floor, was reported at `high` alongside genuinely blocking problems.

    Demoting partials keeps the severity histogram honest without touching the score, and without
    suppressing the finding: the gap is still reported, at a strength that matches it.
    """
    if state != scoring.PARTIAL or declared not in SEVERITY_ORDER:
        return declared
    return SEVERITY_ORDER[min(SEVERITY_ORDER.index(declared) + 1, len(SEVERITY_ORDER) - 1)]


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
          force_tier: str | None = None) -> dict:
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
            "user_agent": fetch_mod.user_agent(config),
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

    # --- analyze, score, advise ------------------------------------------------------------------
    # The temp directory spans all three stages because the advisor needs the same artifact the
    # analyzers read, and the findings do not exist until scoring has run. The `finally` still
    # guarantees removal: the directory is 0700 and holds page-derived HTML, so a scoring exception
    # must not leave it on disk.
    import shutil
    import tempfile

    rows: list = []
    advice = dict(EMPTY_ADVICE)
    tmpdir = None
    try:
        artifact_path = None
        try:
            tmpdir = tempfile.mkdtemp(prefix="citely-")
            os.chmod(tmpdir, 0o700)
            artifact_path = Path(tmpdir) / "artifact.json"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            for skill, script in ANALYZERS:
                rows.extend(run_analyzer(skill, script, artifact_path, errors, deadline))
        except Exception as exc:
            errors.append({"stage": "analyze", "type": "setup_failed",
                           "message": type(exc).__name__})

        # --- score -------------------------------------------------------------------------
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

        # --- the non-expert output layer ---------------------------------------------------
        # Computed here, into the REPORT, not in a separate renderer, so a machine consumer
        # piping the JSON gets the same plain reading a
        # person gets. Presentation only — it reads the scores, it never changes them.
        registry_categories = load_json(CHECKS_PATH).get("categories") or {}
        reason_text = registry_categories.get("reasons") or {}
        cat_coverage = summary.get("category_coverage") or {}
        verdict, headline_reliable, caveat = narrative.overall_verdict(
            overall, summary.get("coverage"), registry_categories)
        summary["verdict"] = verdict
        summary["headline_reliable"] = headline_reliable
        summary["headline_caveat"] = caveat
        summary["category_verdicts"] = narrative.category_verdicts(
            cat_scores, cat_coverage, registry_categories)

        # --- advise ------------------------------------------------------------------------
        # Runs after scoring and cannot influence it: the summary above is already final, and the
        # advisor only ever adds fields to findings that exist. Proactive items land in their own
        # top-level list, outside the counts the schema's invariant governs.
        if artifact_path is not None:
            advice = run_advisor(Path(tmpdir), artifact_path, findings, resolved, errors, deadline)
            apply_advice(findings, advice)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

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
            # Whether a browser was FOUND, not whether the render succeeded. Those are
            # different facts, and merging them reported "no browser" on a machine that had
            # just run one — sending a reader to reinstall something that was never missing.
            "playwright_available": bool(rendered.get("browser_available")),
            "render_mode": rendered.get("mode") or render_mod.TIER_B,
            # How the render went, not merely whether it happened. "salvaged" means the navigation
            # milestone timed out and the DOM was harvested anyway, which is a Tier-A result a
            # reader deserves to be able to tell apart from a clean one.
            "render_nav_state": rendered.get("nav_state"),
            "render_wait_strategy": rendered.get("wait_strategy"),
            "pages_checked": [p.get("url") for p in pages],
            "pages": [{"url": p.get("url"), "status": p.get("status", "error"),
                       "http_status": (p.get("raw") or {}).get("status"),
                       "reason": p.get("blocked_kind") or p.get("skip_reason")} for p in pages],
            "language_detected": (artifact.get("language") or {}).get("detected"),
            "language_supported": language_supported,
            "user_agent": artifact.get("user_agent", ""),
            "robots_checked": bool((artifact.get("robots") or {}).get("checked")),
            # Always false: no third-party corroboration lookup is implemented, so this discloses
            # that every piece of evidence in the report came from the audited pages themselves.
            # It is NOT a setting — there is no flag that can make it true.
            "external_lookup": False,
            "advisor": advice.get("diagnostics") or {},
            "checks_evaluated": sum(1 for r in resolved.values() if r.state != scoring.UNKNOWN),
            "checks_unknown": sum(1 for r in resolved.values() if r.state == scoring.UNKNOWN),
            "errors": errors,
        },
        "partial": partial,
        "partial_reason": reason,
        "findings": findings,
        # The same findings ordered by what fixing them is worth. findings[] is ordered by severity
        # so F-001… stay stable across runs, which is why it cannot also carry the ROI ranking.
        "next_actions": narrative.next_actions(findings),
        "not_checked": narrative.what_could_not_be_checked(
            resolved, registry, cat_coverage, raw_registry, reason_text),
        # Proactive suggestions from remediation-advisor. Deliberately a separate list: the
        # mandated schema floor requires total_findings == critical + high + medium, so anything
        # that is not a defect must stay outside findings[] or it breaks that invariant — and it
        # would also let beyond-problem advice move a score it has no business touching.
        "recommendations": advice.get("recommendations") or [],
    }


def write_report(report: dict, stream) -> None:
    """Emit the report as UTF-8, whatever the console happens to be set to.

    RFC 8259: JSON for interchange SHALL be encoded in UTF-8, so emitting UTF-8 is correct rather
    than merely convenient. A default Windows console is cp1252 and raises UnicodeEncodeError on
    any script it has no mapping for, which would mean an audit that ran perfectly and then died
    while printing its own result.

    Reconfiguring the stream is preferred, because it produces real UTF-8 bytes that a consumer
    redirecting to a file receives losslessly. Where the stream cannot be reconfigured — a pytest
    capture object, a plain StringIO — every non-ASCII character is escaped instead. That output is
    still valid JSON and still lossless; it is simply less pleasant to read.
    """
    escaped = False
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        declared = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        escaped = declared not in ("utf8", "")

    json.dump(report, stream, indent=2, ensure_ascii=escaped)
    stream.write("\n")
    stream.flush()


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
    # There is deliberately NO --allow-external flag. It existed for nine phases and did nothing:
    # it reached `diagnostics.external_lookup` and stopped there, while `external_corroboration`
    # was hardcoded to None and no Wikidata request was ever issued. A flag that advertises a
    # capability the code does not have is worse than an absent one, because a reviewer who passes
    # it is told the lookup ran. If one is ever built, the crawl artifact already declares the
    # `external_corroboration` slot for its result.
    parser.add_argument("--ci", action="store_true",
                        help="Exit non-zero when any critical finding is present.")
    _assert_no_ssrf_bypass_via_cli(parser)
    args = parser.parse_args(argv)

    configure_logging()

    try:
        config = load_json(Path(args.config))
    except Exception as exc:
        log.error("could not read config %s: %s", args.config, exc)
        return 2

    if config.get("fetch", {}).get("allow_private_hosts"):
        log.warning("fetch.allow_private_hosts is ENABLED — the SSRF guard is disabled. "
                    "This must only ever be true in tests.")

    # Everything from here to the report runs with stdout held shut, so no stage, dependency or
    # stray `print()` can put a byte in front of the JSON.
    with stdout_reserved_for_report() as report_stream:
        try:
            report = audit(args.url, args.html_file, config)
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

    write_report(report, report_stream)

    if args.ci and report.get("summary", {}).get("critical", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
