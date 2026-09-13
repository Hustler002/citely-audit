# Severity and Scoring (shared)

The human-readable source of truth for how the four analysis skills judge severity, so that skills
written to be independently runnable still agree. Machine-readable thresholds live in
[`config/checks.json`](../../../config/checks.json) (per check) and
[`config/scoring-config.json`](../../../config/scoring-config.json) (the model itself). The
orchestrator passes `config/checks.json` to each analysis skill via `--config`.

## Severity tiers (strict 3-tier — there is no `low`)

| Severity   | Meaning                                                                           |
|------------|-----------------------------------------------------------------------------------|
| `critical` | Blocks AI ingestion or citation outright, or a visitor cannot orient at all.       |
| `high`     | Materially degrades discoverability or retention; a large fraction of value is lost.|
| `medium`   | Meaningful but partial degradation; worth fixing after critical and high.           |

Each check declares its severity in the registry. A check that resolves to `partial` is reported
**one tier below** its declared severity, with `medium` as the floor. A half-credit result and a
total failure are not the same finding, and announcing both at the same strength makes the severity
histogram meaningless. The demotion changes wording only; it never changes the score.

Invariant, enforced in code because JSON Schema cannot express it:
`total_findings == critical + high + medium`.

## Confidence

- `verified` — a directly observed fact (an HTTP status, a robots directive, a missing JSON-LD block).
- `heuristic` — an inference (a SPA detected without a browser, quotability judged from text signals).

Confidence is reported, never used to silently shrink a score. `summary.score_confidence` is the
share of scored weight contributed by `verified` checks.

## The score is a capability model, not a penalty model

Each check resolves to one of five states. `pass` earns full credit, `partial` half, `fail` none.
`unknown` and `not_applicable` earn nothing **and leave the denominator**, so a check that could not
be measured lowers coverage rather than the score.

```
category score = weighted mean of credit over that category's APPLICABLE checks
overall score  = weighted mean of the category scores that could be computed
```

A category with nothing measurable scores `null`, never `0.0`. "We could not measure this" and "this
scored zero" are different claims, and a penalty model of the form `100 - Σ penalty` conflates them.
It is also unbounded and double-counts correlated findings, which is why it is not used here.

Two figures qualify every score and must be read with it:

- `summary.coverage` — the share of total check weight that was actually scored. Suppression can
  leave a handful of checks standing, and a high score on a thin slice is not a verdict on the site.
- `summary.category_coverage` — the same, per category, because one surviving check can read 100.0.

Below a coverage floor of 0.5 the report sets `headline_reliable: false` and says in plain words
that the site could not be measured. The numbers stay in place; declining to headline a measurement
is not the same as hiding it.

## Dependency suppression

When a check fails, the checks that depend on it resolve to `unknown` rather than `fail`, so one
root cause does not produce a dozen downstream findings. Suppression requires a **`verified`** failed
prerequisite: a merely heuristic failure may not silence its dependents, which stops suppression
being used to duck a hard call.

## Remediation ranking

`points_recoverable` is the number of overall-score points regained by bringing a check to `pass`,
computed against the current denominator. `next_actions[]` ranks by that value, with severity only
as a tie-break, so the ordering reflects what a fix is worth rather than how alarming it sounds.

## Per-mechanic severity guidance

### crawl-ingestion (mechanic 1)
- `critical`: `robots.txt` disallows an AI assistant's crawler; the homepage returns non-2xx; a
  client-rendered shell with no server-rendered facts.
- If `robots.txt` blocks the audit's own user agent, nothing is fetched and no finding is emitted:
  every check resolves to `unknown` and the report is marked `partial` with reason `blocked`.
- `high`: key facts only in images or canvas with no text or alt equivalent.
- `medium`: partial hydration; semantic-HTML gaps.

### quotability and information density (mechanics 2 + 3)
- `critical`: no self-contained quotable factual statements at all.
- `high`: facts buried under filler; factual-to-filler ratio below threshold on a long page.
- `medium`: quotable facts exist but are sparse or diluted.

### entity corroboration (mechanic 4)
- `critical`: no entity structured data at all.
- `high`: an entity is declared but carries no `sameAs` or cross-web mapping.
- `medium`: a partial or incomplete entity graph.

### engagement and orientation (mechanic 5)
- `critical`: no value proposition and no heading in the first viewport.
- `high`: a heading is present but there is no clear value proposition or call to action above the fold.
- `medium`: orientation is present but weak, for example legibility below threshold.
