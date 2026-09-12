---
name: entity-corroboration-audit
description: Diagnoses entity disambiguation and cross-web corroboration failures (mechanic 4). Use as part of the brand AI-readiness audit, over the shared crawl artifact. Detects missing schema.org/JSON-LD entity graphs and sameAs/Wikidata mappings that cause AI hallucination or mistaken-identity.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12). Needs no network at all, because every verdict is read from the shared crawl artifact.
metadata:
  mechanic: "4"
  category: entity-corroboration
---

# Entity Corroboration Audit (mechanic 4)

Extracts and evaluates the page's structured-data entity graph.

## Inputs
- `--artifact <crawl_artifact.json>` or `--html-file <page.html>`.
- `--config <scoring-config.json>`.
- This analyzer has **zero** network access, with no exceptions. The artifact declares an
  `external_corroboration` slot for a third-party lookup performed in the orchestrator's fetch
  stage; no such lookup is implemented, so it is always null and nothing here depends on it.

## Output
- JSON array of **check states** on stdout (see `references/check-result-schema.json`): one
  `{check_id, state, measurement, evidence, selector, page_url}` per check. **Not findings** —
  report wording lives once in `config/checks.json` and is applied by the orchestrator. Logs to stderr.

## Checks
- Extract JSON-LD, Open Graph, and microdata; require an `Organization`/`Person`-class entity.
- Check for `sameAs` and Wikidata/social mappings (cross-web corroboration).
- Authority is judged from the links the page itself publishes. There is **no external lookup**:
  every verdict rests on evidence observed on the audited pages, which is what makes the same page
  produce the same verdict every time.

Thresholds from `config/scoring-config.json` → `entity_corroboration`.
