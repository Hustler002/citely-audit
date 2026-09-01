---
name: quotability-density-audit
description: Diagnoses RAG quotability (mechanic 2) and information-density / summarizer-survival (mechanic 3). Use as part of the brand AI-readiness audit, over rendered HTML from the shared crawl artifact. Flags vague marketing prose that AI won't cite and key facts diluted by filler that summarizers drop.
license: Apache-2.0
compatibility: Requires Python 3.11+. No network access.
metadata:
  mechanic: "2+3"
  category: quotability
---

# Quotability & Density Audit (mechanics 2 + 3)

Pure text/structure analysis over already-harvested rendered HTML. No new network calls.

## Inputs
- `--artifact <crawl_artifact.json>` (uses rendered HTML) or `--html-file <page.html>`.
- `--config <scoring-config.json>`.

## Output
- JSON array of partial findings on stdout (no `id`). Logs to stderr.

## Checks
- **Quotability (mechanic 2)** — count self-contained factual sentences (subject + concrete claim);
  flag vague marketing tokens from the configured list; low quotable-sentence count → finding.
- **Information density (mechanic 3)** — factual-to-filler ratio; on long pages (word count above
  threshold) low-density key facts risk being dropped by summarizers.

Categories emitted: `quotability` and `information-density`. Thresholds from
`config/scoring-config.json` → `quotability_density`.
