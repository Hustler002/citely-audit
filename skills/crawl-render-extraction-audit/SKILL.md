---
name: crawl-render-extraction-audit
description: Diagnoses AI crawl-and-ingestion failures (mechanic 1) — bot access, JS-render/readability gaps, and facts trapped in images/canvas. Use as part of the brand AI-readiness audit, over the shared crawl artifact. Detects SSR-vs-CSR content loss and non-semantic facts that fail to parse into AI context.
license: Apache-2.0
compatibility: Requires Python 3.11+. Optional Playwright/Chromium for the Tier-A raw-vs-rendered diff; without it, uses a browserless SPA heuristic (Tier B).
metadata:
  mechanic: "1"
  category: crawl-ingestion
---

# Crawl / Render / Extraction Audit (mechanic 1)

Analyzes the shared crawl artifact for the three stages of the ingestion funnel. Does **not** fetch.

## Inputs
- `--artifact <crawl_artifact.json>` (orchestrated) or `--html-file <page.html>` (offline).
- `--config <scoring-config.json>` (thresholds; safe defaults embedded).

## Output
- JSON array of **check states** on stdout (see `references/check-result-schema.json`): one
  `{check_id, state, measurement, evidence, selector, page_url}` per check. **Not findings** —
  report wording lives once in `config/checks.json` and is applied by the orchestrator, so it
  cannot drift between analyzers. Logs go to stderr.

## Checks
1. **Access** — HTTP status, robots directive, bot-hostile response headers.
2. **Render / Readability** — Tier A: raw-vs-rendered DOM text delta (Playwright). Tier B (no browser):
   SPA signals in raw HTML — empty-shell mount node, framework markers (`__NEXT_DATA__`, `__NUXT__`,
   `ng-version`), high script-to-text ratio, `<noscript>`. Tier B findings are `confidence: heuristic`.
3. **Fact Extraction** — facts trapped in `<img>`/SVG/`<canvas>` without text/`alt`/semantic HTML
   (text-to-node ratio, missing `alt`, numeric facts only in image filenames).

Thresholds and marker lists come from `config/scoring-config.json` → `crawl_render_extraction`.
See the orchestrator's `references/severity-rubric.md` for severity/confidence.
