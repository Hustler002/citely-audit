---
name: entity-corroboration-audit
description: Diagnoses entity disambiguation and cross-web corroboration failures (mechanic 4). Use as part of the brand AI-readiness audit, over the shared crawl artifact. Detects missing schema.org/JSON-LD entity graphs and sameAs/Wikidata mappings that cause AI hallucination or mistaken-identity.
license: Apache-2.0
compatibility: Requires Python 3.11+. On-page analysis needs no network. Optional --allow-external enables a soft-fail Wikidata lookup requiring outbound HTTPS.
metadata:
  mechanic: "4"
  category: entity-corroboration
---

# Entity Corroboration Audit (mechanic 4)

Extracts and evaluates the page's structured-data entity graph.

## Inputs
- `--artifact <crawl_artifact.json>` or `--html-file <page.html>`.
- `--config <scoring-config.json>`.
- The optional Wikidata corroboration lookup runs in the **orchestrator's fetch stage**, not here.
  It arrives pre-fetched as `external_corroboration` in the artifact. This analyzer has **zero**
  network access, with no exceptions.

## Output
- JSON array of **check states** on stdout (see `references/check-result-schema.json`): one
  `{check_id, state, measurement, evidence, selector, page_url}` per check. **Not findings** —
  report wording lives once in `config/checks.json` and is applied by the orchestrator. Logs to stderr.

## Checks
- Extract JSON-LD, Open Graph, and microdata; require an `Organization`/`Person`-class entity.
- Check for `sameAs` and Wikidata/social mappings (cross-web corroboration).
- **Optional external corroboration** (`--allow-external` only): a Wikidata lookup that is
  **soft-fail** — it never blocks or crashes the audit, respects the SSRF/timeout guards, and any
  finding derived from it is labeled `confidence: heuristic`. This is the single sanctioned exception
  to the "orchestrator owns all network I/O" rule.

Thresholds from `config/scoring-config.json` → `entity_corroboration`.
