---
name: audit-orchestrator
description: Audits a website for AI discoverability and on-site engagement. Use when the user gives a URL and wants to know why AI assistants fail to find or cite the brand or why visitors bounce. Performs one safe crawl and render, runs the four analysis skills and the remediation advisor, scores the results, and emits a single JSON audit report.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12) and network access to the target site. Tier-A rendering needs Chromium, provisioned once through Playwright as a setup step; without it the audit falls back to a browserless heuristic.
metadata:
  role: entrypoint
  project: citely-audit
---

# Audit Orchestrator (entrypoint)

Runs a read-only AI-readiness audit of one website and produces the final report. This is the only
skill in the marketplace that uses the network.

## Usage

```bash
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com
python skills/audit-orchestrator/scripts/run_audit.py --html-file page.html
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com --ci
```

| Option | Meaning |
|---|---|
| `--url URL` | Homepage to audit. Exactly one of `--url` and `--html-file` is required. |
| `--html-file PATH` | Audit one local HTML file with no network access: no `robots.txt` check and no browser render. |
| `--config PATH` | Scoring and operational configuration. Default: `config/scoring-config.json`. |
| `--ci` | Exit with status 1 when the report contains any critical finding. |

Exit codes: `0` report printed; `1` with `--ci` and at least one critical finding; `2` invalid
arguments or an unreadable `--config` file.

## Output

A single JSON report on **stdout only**; all logs go to stderr. The report is defined by
[`references/report-schema.json`](references/report-schema.json).
[`references/sample-report.json`](references/sample-report.json) is the report for the
`tests/fixtures/broken_page.html` fixture, a page whose content depends on JavaScript.

## Procedure

1. **Robots gate.** Read `robots.txt`. If the audit user agent (`CitelyAuditBot`) is disallowed, or
   `robots.txt` cannot be retrieved (a network failure or a 5xx response), fetch nothing: every check
   resolves to `unknown` and the report is marked `partial` with reason `blocked`. A 4xx response
   counts as no restrictions. Which AI crawlers `robots.txt` allows is recorded
   separately for the scored check `access.ai_crawlers_allowed`.
2. **Fetch and select pages.** Fetch the homepage through `scripts/_safe_fetch.py` (SSRF guard,
   redirect, size and timeout limits, HTML content types only). Choose up to four more same-site pages
   with `scripts/_page_select.py`: navigation links first, the sitemap as a fallback, at most two path
   segments deep. Honour `Crawl-delay` between pages, up to 5 seconds.
3. **Render.** `scripts/_render.py` renders each fetched page in Chromium at 1280×800 (Tier A) or,
   when no browser is available, records browserless client-rendering signals (Tier B).
4. **Build the crawl artifact** with `scripts/_artifact.py`, as defined by
   [`references/crawl-artifact-schema.json`](references/crawl-artifact-schema.json). Pages that are
   consent walls, bot challenges, CAPTCHAs or login walls are marked `blocked`. The artifact is
   written to a temporary directory that is removed when the run ends.
5. **Analyze.** Run the four analysis skills as subprocesses, in this order, each with
   `--artifact <artifact.json> --config config/checks.json`: `crawl-render-extraction-audit`,
   `entity-corroboration-audit`, `quotability-density-audit`, `engagement-orientation-audit`. Each
   prints check states as defined by
   [`references/check-result-schema.json`](references/check-result-schema.json). A crash, a timeout
   (60 seconds, or less if the deadline is closer) or invalid output turns that skill's checks
   `unknown` and is recorded in `diagnostics.errors`.
6. **Score** with `scripts/_scoring.py`: apply the language gate and dependency suppression, compute
   category scores, the overall score, `coverage` and `score_confidence`, and turn every check that
   ended `fail` or `partial` into a finding. Finding ids (`F-001`, …) follow severity, then category,
   check, page and an evidence hash.
7. **Explain** with `scripts/_narrative.py`: plain-language verdicts, the `next_actions` checklist
   and `not_checked`.
8. **Advise.** Run `remediation-advisor` with `--artifact`, `--findings`, `--check-states` and
   `--config config/checks.json`, then merge its target, snippet, validation and remaining
   placeholders into each finding's `suggested_action` and add its `recommendations`. This happens
   after scoring and cannot change any score or count.
9. **Validate** the report against the schema and the rule `total_findings == critical + high +
   medium`, log any problem to stderr, and print the report.

## Guardrails

- Read-only: `GET` requests only, no authentication, no form submission, no writes to the target.
  Every request carries one identifying user agent, `CitelyAuditBot/0.1`, plus `fetch.contact_url`
  when one is configured.
- A global deadline (`budgets.global_deadline_s`, default 270 seconds) keeps the run under five
  minutes; pages skipped for time mark the report `partial` with reason `budget`.
- The audit does not crash on bad input: stage failures are recorded in `diagnostics.errors` and a
  report is still printed.
- `fetch.allow_private_hosts` disables the SSRF guard for the test suite only. It is deliberately not
  a command-line option.

See [`references/severity-rubric.md`](references/severity-rubric.md) for severity, confidence and
scoring.
