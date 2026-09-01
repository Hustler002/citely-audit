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
- `--allow-external` (optional) — enable the Wikidata corroboration lookup. **Off by default.**

## Output
- JSON array of partial findings on stdout (no `id`). Logs to stderr.

## Checks
- Extract JSON-LD, Open Graph, and microdata; require an `Organization`/`Person`-class entity.
- Check for `sameAs` and Wikidata/social mappings (cross-web corroboration).
- **Optional external corroboration** (`--allow-external` only): a Wikidata lookup that is
  **soft-fail** — it never blocks or crashes the audit, respects the SSRF/timeout guards, and any
  finding derived from it is labeled `confidence: heuristic`. This is the single sanctioned exception
  to the "orchestrator owns all network I/O" rule.

Thresholds from `config/scoring-config.json` → `entity_corroboration`.
