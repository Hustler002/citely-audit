---
name: engagement-orientation-audit
description: Diagnoses first-viewport orientation and visitor retention (mechanic 5). Use as part of the brand AI-readiness audit, over the rendered DOM from the shared crawl artifact. Detects a missing above-the-fold heading, call to action or specific value proposition, overlays covering the first screen, a missing mobile viewport and small body text, which make AI-referred visitors bounce.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12). No network access.
metadata:
  mechanic: "5"
  category: engagement-orientation
---

# Engagement & Orientation Audit (mechanic 5)

Can someone arriving from an AI answer tell, on the first screen, that they are in the right place?
Deterministic measurements over the crawl artifact, with no network access and no visual judgement.

## Inputs

- `--artifact <crawl_artifact.json>` (as run by the orchestrator) or `--html-file <page.html>`
  (offline).
- `--config <checks.json>`: the check registry. Each check's `threshold` is read from it, with
  built-in defaults for any check it does not define. Default: `config/checks.json`.

## Output

A JSON array of **check states** on stdout, one per check:
`{check_id, state, measurement, evidence, selector, page_url, reason}`, as defined by the
orchestrator's `references/check-result-schema.json`. These are not findings: report wording lives in
`config/checks.json` and is applied by the orchestrator. Logs go to stderr.

## Scope and measurement tiers

The homepage only. If it could not be read, all six checks are `unknown` with the reason.

- **With a browser render (Tier A)**, checks use geometry captured during the render: element
  offsets against the viewport height, fixed-overlay coverage and the computed body font size.
- **Without one (Tier B, and always offline)**, a document-order proxy stands in: elements within the
  first `dom_proxy_text_chars` characters of visible text count as above the fold, with text inside
  navigation and headers discounted (`chrome_text_weight`, capped by `chrome_text_cap`). Those
  results carry the reason "measured by DOM-order proxy (no browser render available)". Overlay
  coverage and legibility need a render, so they resolve to `unknown`.

## Checks

Threshold names refer to the check's `threshold` block in `config/checks.json`.

| Check | Result |
|---|---|
| `orientation.heading_first_viewport` | pass: a heading with text above the fold (in Tier B, an `h1` to `h3` within the proxy window); fail: none |
| `orientation.primary_cta` | pass: a link or button above the fold, detected structurally rather than by wording; fail: none |
| `orientation.content_not_obstructed` | fail: the page was detected as an interstitial (consent wall, bot challenge, CAPTCHA or login wall), or a fixed overlay covers more than `max_overlay_coverage` of the first screen; pass: otherwise; `unknown` without a render |
| `orientation.viewport_meta` | pass: `<meta name="viewport">` with content; fail: missing |
| `orientation.value_proposition` | Language-dependent; see below |
| `orientation.legibility` | pass: computed body font size at least `min_body_font_px`; fail: smaller; `unknown` without a render |

**Value proposition.** Only the headline area is judged: the topmost heading above the fold plus the
text that follows it, so text added elsewhere on the page does not change the result. In Tier B,
navigation and floating dialogs cannot supply the headline.

- fail: no headline, or fewer than `min_text_chars` characters in the headline area.
- The headline area is **specific** when it contains a figure with a unit, currency or percentage, or
  a proper noun, or both an `action_verbs` word and an `offering_nouns` word. A missing word can
  never cause a failure on its own.
- When `max_vague_markers` or more `vague_markers` phrases appear, the result is capped: partial if
  specific, fail if not.
- Otherwise: pass if specific, partial if not.

This check uses English vocabulary. When the artifact's `language.supported` is false it resolves to
`unknown` with reason `language_unsupported_or_undetected`, never `fail`.

Every signal here is a measurement (a pixel offset, a computed font size, an overlay area, a position
in the document), never a judgement about how a page looks, so a second run can reproduce it.

See the orchestrator's `references/severity-rubric.md` for severity and confidence.
