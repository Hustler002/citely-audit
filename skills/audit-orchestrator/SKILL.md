---
name: audit-orchestrator
description: Audits a website for AI discoverability and on-site engagement. Use when the user gives a URL and wants to know why AI assistants fail to cite the brand or why visitors bounce. Performs one safe crawl + render, runs the four analysis skills, merges and ranks findings, and emits a single JSON audit report.
license: Apache-2.0
compatibility: Requires Python 3.11+ and network access to the target site. Optional Playwright/Chromium for Tier-A render diff (audit degrades to a browserless heuristic without it). Provision Chromium once as a setup step, not during a run.
metadata:
  role: entrypoint
  project: citely-audit
---

# Audit Orchestrator (entrypoint)

Coordinates a read-only, non-destructive AI-readiness audit of a single homepage URL and produces the
final report. This is the only skill that touches the network for page content.

## Inputs
- `url` (required) — homepage to audit.
- Offline mode: `--html-file <page.html>` to audit a local file with zero network access.

## Output
- A single JSON report on **stdout only** (all logs go to stderr), valid against
  [`references/report-schema.json`](references/report-schema.json).

## Procedure
1. **Safe fetch** robots.txt + raw HTML via `scripts/_safe_fetch.py` (SSRF guard, redirect cap,
   content-type/size/timeout limits). If the audit User-Agent is disallowed on the root path, **stop**
   and emit that single finding — never proceed silently.
2. **Render once** (Tier A Playwright if available, else Tier B browserless heuristic — see the crawl
   skill). Build the shared crawl artifact (see [`references/crawl-artifact-schema.json`](references/crawl-artifact-schema.json)).
3. **Run the three consumer sub-skills as subprocesses**, each against the artifact, passing
   `--config config/scoring-config.json`: `quotability-density-audit`, `entity-corroboration-audit`,
   `engagement-orientation-audit`. (`crawl-render-extraction-audit` runs as part of step 2's analysis.)
4. **Merge** partial findings, assign stable IDs `F-001…` by the deterministic sort
   (severity → category → skill order → evidence hash).
5. **Compute** `summary` counts + `discoverability_score` (see [`references/severity-rubric.md`](references/severity-rubric.md)),
   attach `diagnostics`, set `partial`/`partial_reason` if the global deadline forced skips.
6. **Validate** the assembled report against the schema, then print it.

## Guardrails
- Read-only; no auth, no writes, respects robots.txt; one identifying User-Agent per request.
- Global monotonic deadline (default 270s) keeps the run under the 5-minute budget.
- The audit never crashes: every stage records failures into `diagnostics.errors[]`.

See [`references/severity-rubric.md`](references/severity-rubric.md) for severity, confidence, and scoring.
