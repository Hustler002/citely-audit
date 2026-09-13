---
name: crawl-render-extraction-audit
description: Diagnoses AI crawl-and-ingestion failures (mechanic 1) — whether the page returns successfully, whether robots.txt admits the AI assistants' crawlers, whether it is indexable, whether its content exists without JavaScript, and whether it uses semantic landmarks and keeps facts out of images. Use as part of the brand AI-readiness audit, over the shared crawl artifact.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12). No network access. Uses the rendered DOM as supporting evidence when the orchestrator rendered the page in Chromium.
metadata:
  mechanic: "1"
  category: crawl-ingestion
---

# Crawl / Render / Extraction Audit (mechanic 1)

Can a machine reach, render and parse this page? Reads the shared crawl artifact and never fetches
anything.

## Inputs

- `--artifact <crawl_artifact.json>` (as run by the orchestrator) or `--html-file <page.html>`
  (offline).
- `--config <checks.json>`: the check registry. Each check's `threshold` is read from it, with
  built-in defaults for any check it does not define. Default: `config/checks.json`.
- Client-rendering markers (`render.spa_mount_selectors` and `render.spa_framework_markers`) are read
  from `config/scoring-config.json`, with an embedded fallback copy.

## Output

A JSON array of **check states** on stdout, one per check:
`{check_id, state, measurement, evidence, selector, page_url, reason}`, as defined by the
orchestrator's `references/check-result-schema.json`. These are not findings: report wording lives in
`config/checks.json` and is applied by the orchestrator. Logs go to stderr.

## Scope

The homepage is authoritative. If it could not be read, all six checks are `unknown` with the reason;
another page's results are never reported in its place.

## Checks

Threshold names refer to the check's `threshold` block in `config/checks.json`.

| Check | pass | partial | fail |
|---|---|---|---|
| `access.http_ok` | 2xx status | — | Any other status |
| `access.ai_crawlers_allowed` | `robots.txt` permits every crawler in `robots.ai_crawlers` | — | At least one is disallowed. `unknown` when `robots.txt` could not be evaluated, which includes offline mode. |
| `access.indexable` | No `noindex` | — | `noindex` in `<meta name="robots">` or the `X-Robots-Tag` header |
| `render.content_without_js` | At least `min_visible_chars_raw` characters of visible text in the raw HTML, or fewer with no sign that JavaScript is required | — | Little raw text plus evidence of client-side rendering (an empty mount node, framework markers, or a render that adds far more text), or fewer than `empty_floor_chars` characters |
| `extraction.semantic_html` | A `main` or `article` landmark | Only `header` or `nav` | No landmarks |
| `extraction.facts_not_image_only` | No suspect images | Some suspect images, up to `max_image_only_fact_ratio` of those considered | More than `max_image_only_fact_ratio` |

`render.content_without_js` is judged from the raw HTML, so it is `verified` whether or not a browser
ran; the render adds supporting evidence only.

For `extraction.facts_not_image_only`, an image is suspect when its filename looks like data (a short
number beside real words, excluding content hashes, UUIDs and encoded paths) and it has fewer than 10
characters of `alt` text. Images declared smaller than `min_fact_image_px` are not considered, and
inline `data:` or `blob:` images have no filename, so they are never suspect. The check is
`not_applicable` when fewer than `min_images_to_evaluate` images remain.

See the orchestrator's `references/severity-rubric.md` for severity and confidence.
