---
name: quotability-density-audit
description: Diagnoses whether AI assistants can understand and quote a page — RAG quotability (mechanic 2) and information density / summarizer survival (mechanic 3) — through its title, meta description, heading outline, facts in lists or tables, self-contained quotable sentences and factual density. Use as part of the brand AI-readiness audit, over the homepage HTML from the shared crawl artifact.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12). No network access.
metadata:
  mechanic: "2+3"
  category: quotability
---

# Quotability & Density Audit (mechanics 2 + 3)

Can an assistant lift a clear fact out of this page and quote it, and would the facts survive
summarizing? Pure text and structure analysis of the crawl artifact, with no network access.

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

## Scope

The homepage only, using its rendered DOM when a browser render produced one and its raw HTML
otherwise. If the homepage could not be read, all six checks are `unknown` with the reason.

## Checks

All six belong to the `ai_comprehension` category. Threshold names refer to the check's `threshold`
block in `config/checks.json`.

| Check | Language-dependent | Result |
|---|---|---|
| `content.title_descriptive` | No | pass: title length within `min_chars` to `max_chars`; partial: outside that range; fail: no `<title>` |
| `content.meta_description` | No | pass: length within `min_chars` to `max_chars`; partial: outside that range; fail: missing |
| `content.heading_hierarchy` | No | pass: exactly one `h1` and no level jump larger than `max_level_skip`; partial: otherwise; fail: no headings |
| `content.scannable_blocks` | No | pass: at least `min_blocks` lists, tables or definition lists with `min_items_per_block` items; partial: none, on a page shorter than `short_page_chars`; fail: none, on a longer page |
| `quotability.self_contained_facts` | Yes | pass: at least `min_quotable_sentences` quotable sentences; partial: fewer, but some; fail: none |
| `density.factual_ratio` | Yes | pass: quotable sentences make up at least `min_ratio` of all sentences; below that, fail on pages longer than `long_page_word_count` words and partial otherwise. Any `filler_tokens` found are named in the evidence. |

A sentence is **quotable** when it is `min_sentence_chars` to `max_sentence_chars` long, has at least
`min_words` words, does not open with one of the `dangling_openers` (words such as "It" that depend
on the previous sentence), and contains a number or a proper noun.

**Language gate.** The two language-dependent checks use English vocabulary. When the artifact's
`language.supported` is false, because the page declares no language or an unsupported one, they
resolve to `unknown` with reason `language_unsupported_or_undetected`, never `fail`. In offline mode
the language is read from the file's `<html lang>` and compared with `language.supported` in
`config/scoring-config.json`.

See the orchestrator's `references/severity-rubric.md` for severity and confidence.
