---
name: engagement-orientation-audit
description: Diagnoses first-viewport orientation and visitor retention (mechanic 5). Use as part of the brand AI-readiness audit, over the rendered DOM from the shared crawl artifact. Detects a missing above-the-fold value proposition, heading, or CTA that makes AI-referred visitors bounce.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12). No network access.
metadata:
  mechanic: "5"
  category: engagement-orientation
---

# Engagement & Orientation Audit (mechanic 5)

Deterministic, reproducible heuristics over the rendered DOM — not subjective/visual judgment.

## Inputs
- `--artifact <crawl_artifact.json>` (uses rendered HTML) or `--html-file <page.html>`.
- `--config <scoring-config.json>`.

## Output
- JSON array of partial findings on stdout (no `id`). Logs to stderr.

## Checks (all threshold-driven, deterministic)
- **Heading present** in the first viewport (`<h1>`/hero).
- **Value-proposition signal** in above-the-fold text (verb + offering, per the configured verb list).
- **Primary CTA** present above the fold.
- **Legibility proxies** — body font size / basic contrast from inline/computed styles when available.

Thresholds from `config/scoring-config.json` → `engagement_orientation`.
Determinism note: this replaces the original plan's "UX/visual reasoning" with measurable signals so
runs are reproducible (plan §1 mistake #2).
