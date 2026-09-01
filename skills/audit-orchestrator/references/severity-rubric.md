# Severity Rubric & Scoring (shared)

Single human-readable source of truth for how the four analysis skills judge severity, so
independently-written skills stay consistent. Machine-readable thresholds live in
[`config/scoring-config.json`](../../../config/scoring-config.json) (the orchestrator passes it to each
sub-skill via `--config`).

> Status: **placeholder defaults** — thresholds to be tuned against the test fixtures (plan §10).

## Severity tiers (strict 3-tier — there is no `low`)

| Severity   | Meaning                                                                                          |
|------------|--------------------------------------------------------------------------------------------------|
| `critical` | Blocks AI ingestion/citation outright, or visitors cannot orient at all. Fix first.              |
| `high`     | Materially degrades discoverability/retention; a large fraction of value is lost.                |
| `medium`   | Meaningful but partial degradation; worth fixing after critical/high.                            |

`summary` invariant (enforced in code): `total_findings == critical + high + medium`.

## Confidence

- `verified` — directly observed fact (HTTP status, a robots directive present, a JSON-LD block missing).
- `heuristic` — inference (SPA detected without a browser, quotability judged from text signals). Default.

## Discoverability score

```
score = clamp(100 - Σ penalty(severity), 0, 100)
```

Default penalties (`config/scoring-config.json` → `score_weights`): critical −20, high −10, medium −4.
Recorded as `summary.discoverability_score`.

## Per-mechanic severity guidance (to be finalized during implementation)

### crawl-ingestion (mechanic 1)
- `critical`: robots blocks the audit UA on root; root returns non-2xx; SPA shell with no server-rendered facts.
- `high`: key facts only in images/canvas with no text/alt; significant raw-vs-rendered text delta.
- `medium`: partial hydration; some semantic-HTML gaps.

### quotability / information-density (mechanics 2 + 3)
- `critical`: no self-contained quotable factual statements at all.
- `high`: facts buried under filler; factual-to-filler ratio below threshold on a long page.
- `medium`: quotable facts exist but are sparse or diluted.

### entity-corroboration (mechanic 4)
- `critical`: no entity structured data at all (no JSON-LD/OG Organization/Person).
- `high`: entity present but missing `sameAs` / cross-web mappings.
- `medium`: partial/incomplete entity graph.

### engagement-orientation (mechanic 5)
- `critical`: no value proposition or heading in the first viewport.
- `high`: heading present but no clear value-prop signal or CTA above the fold.
- `medium`: orientation present but weak (e.g., legibility below threshold).
