---
name: remediation-advisor
description: Turns audit findings into targeted, copy-pasteable fixes and adds non-obvious proactive suggestions. Use after the analysis skills have produced findings and check states, to attach a target, a snippet and a validation procedure to every finding and to surface improvements where no defect was found. Never invents business facts.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12). Prescription only — zero network access and no site modification, exactly like the analysis skills.
metadata:
  role: prescription
  project: citely-audit
---

# Remediation Advisor

**Prescription, not detection.** Every other skill in this marketplace measures the page. This one
emits no check states and cannot influence the score: it answers the question the findings leave
open, *what exactly do I change, and how will I know it worked?*

## Inputs

- `--artifact <crawl_artifact.json>`: read-only evidence. Snippets are filled **only** from values
  observed on the homepage, and the proactive detectors read the homepage through it.
- `--findings <findings.json>`: the JSON array of findings the orchestrator has already assembled and
  assigned `F-001…` ids to. Optional; defaults to none.
- `--check-states <states.json>`: a JSON object mapping each `check_id` to its resolved state.
  Optional. Findings carry only the checks that failed; the states also tell the advisor what passed.
- `--config <checks.json>`: accepted so the invocation matches the analysis skills; currently unused.
- Offline mode: `--html-file <page.html>` instead of `--artifact` runs the proactive detectors alone
  against a local file, with no findings and no network. The language gate then passes only when the
  file declares English in `<html lang>`.

## Output

A single JSON object on **stdout only** (logs to stderr), defined by
[`references/advice-schema.json`](references/advice-schema.json):

```json
{
  "corrective": [
    { "finding_id": "F-001", "check_id": "…", "target": "…", "snippet": "…",
      "validation": "…", "placeholders_remaining": ["{{LOGO_URL}}"] }
  ],
  "recommendations": [
    { "id": "R-001", "type": "proactive", "detector": "R:undated_claims", "category": "…",
      "title": "…", "summary": "…", "rationale": "…" }
  ],
  "diagnostics": { "suppressed": ["…"], "language_gated": ["…"] }
}
```

`diagnostics` is present only when there is something to report. The orchestrator merges `target`,
`snippet`, `validation` and `placeholders_remaining` into each finding's `suggested_action` by
`finding_id`, places `recommendations` at the top level of the report, and reports `diagnostics` as
`diagnostics.advisor`.

## Procedure

1. **Corrective.** For each finding, look up its check in
   [`references/remediation-templates/corrective.json`](references/remediation-templates/corrective.json),
   which covers all 24 checks with a target and a validation procedure. Fill the snippet from observed
   values. Three checks have no snippet, because pasting markup cannot fix them:
   `access.http_ok`, `render.content_without_js` and `density.factual_ratio`.
2. **Proactive.** Run the four detectors in
   [`references/remediation-templates/proactive.json`](references/remediation-templates/proactive.json)
   against the homepage, honouring the language gate and suppression. Emit only those that fire.
3. **Assign** `R-001…` in order, so repeat runs produce the same ids.

Ranking is not done here: each finding already carries `points_recoverable`, and the orchestrator
orders `next_actions` by it.

## The three rules that keep this honest

- **Never invent a business fact.** A snippet value is either observed on the page or stays a literal
  `{{PLACEHOLDER}}`. Remaining placeholders are listed in `placeholders_remaining`, so the reader is
  told what they must supply instead of being handed a plausible-looking fabrication. No language
  model is involved anywhere in this skill.
- **Never report one root cause twice.** A proactive item is suppressed when its related check
  produced a finding (or, offline, when a supplied check state is `fail` or `partial`). Suppression
  is checked after the detector runs, so `suppressed` means "had something to report and withheld
  it".
- **Proactive items are not findings.** They live in `recommendations[]`, are excluded from `summary`
  counts and from scoring, and can therefore neither inflate nor deflate the score.

## Proactive detectors

| Id | Fires when | Language-gated | Suppressed by |
|---|---|---|---|
| `R:faq_schema` | Question-shaped headings with answers, and no `FAQPage` in JSON-LD | No | `entity.structured_data_present` finding |
| `R:facts_in_prose` | Specification-shaped facts stranded in paragraphs | Yes | `content.scannable_blocks` finding |
| `R:undated_claims` | Numeric claims with no date and no attribution anywhere on the page | Yes | — |
| `R:image_facts_with_alt` | Numbers that exist only in image `alt` text, never in the body | No | `extraction.facts_not_image_only` finding |

A language-gated detector stays silent on a page whose language is undetected or unsupported, for the
same reason the language-dependent checks resolve to `unknown`.

A fifth candidate was considered and deliberately **not** built: an `Organization` carrying `sameAs`
but no third-party anchor. That is already the scored check `entity.sameas_authority`, so a detector
for it would report one root cause twice.
