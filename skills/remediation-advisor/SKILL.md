---
name: remediation-advisor
description: Turns audit findings into targeted, copy-pasteable fixes and adds non-obvious proactive suggestions. Use after the analysis skills have produced findings and check states, to attach a snippet and a validation procedure to every finding and to surface improvements where no defect was found. Never invents business facts.
license: Apache-2.0
compatibility: Requires Python 3.11+. Prescription only — zero network access and no site modification, exactly like the analysis skills.
metadata:
  role: prescription
  project: citely-audit
---

# Remediation Advisor

**Prescription, not detection.** Every other skill in this marketplace measures the page. This one
measures nothing: it consumes verdicts that already exist and answers the question they leave open —
*what exactly do I change, and how will I know it worked?*

## Inputs

- `--findings <findings.json>` — the JSON array of findings the orchestrator has already assembled
  and assigned `F-001…` ids to.
- `--check-states <states.json>` — a JSON object mapping every `check_id` to its resolved state.
  Findings only carry the checks that **failed**; proactive suggestions need to know what **passed**,
  which is the difference between "beyond-problem" advice and repeating a finding.
- `--artifact <crawl_artifact.json>` — read-only evidence. Snippets are filled **only** from values
  observed here, and the proactive detectors read the page through it.
- `--config <checks.json>` — the check registry, for category and severity.
- Offline mode: `--html-file <page.html>` runs the proactive detectors alone against a local file,
  with no findings and no network. This is what makes the skill independently useful.

> **Why it reads the artifact.** `PLAN.md` §5.2 originally justified this skill as consuming
> "findings, not the artifact". That distinction is about **concern**, not inputs, and §7 makes the
> artifact unavoidable: snippets must be filled from observed values, and every proactive detector
> inspects the page. The skill still performs no detection of its own — it never emits a check state
> and never influences the score.

## Output

A single JSON object on **stdout only** (logs to stderr), valid against
[`references/advice-schema.json`](references/advice-schema.json):

```json
{
  "corrective":      [ { "finding_id": "F-001", "snippet": "…", "validation": "…" } ],
  "recommendations": [ { "id": "R-001", "type": "proactive", "title": "…", "summary": "…" } ]
}
```

The orchestrator merges `corrective` into each finding's `suggested_action` by `finding_id`, and
places `recommendations` at the top level of the report.

## Procedure

1. **Corrective.** For each finding, look up its check in
   [`references/remediation-templates/corrective.json`](references/remediation-templates/corrective.json),
   fill the snippet from observed values, and attach the validation procedure.
2. **Proactive.** Run the four detectors in
   [`references/remediation-templates/proactive.json`](references/remediation-templates/proactive.json)
   against the artifact. Emit only those that fire.
3. **Rank.** Corrective actions inherit the finding's `points_recoverable`, so the report's
   prioritisation stays ROI-ordered rather than opinion-ordered. Proactive items are unranked and
   unscored by construction.
4. **Assign** `R-001…` deterministically, so repeat runs are byte-identical.

## The three rules that keep this honest

- **Never invent a business fact.** A snippet value is either observed on the page or stays a
  literal `{{PLACEHOLDER}}`. Remaining placeholders are listed in `placeholders_remaining` so the
  reader is told what they must supply, rather than being handed a plausible-looking fabrication.
  No LLM is involved anywhere in this skill.
- **Never report one root cause twice.** A proactive item is suppressed when its related check
  produced a finding. Without this the advisor would tell a site to add `FAQPage` markup directly
  beneath a critical finding saying it has no structured data at all.
- **Proactive items are not findings.** They live in `recommendations[]`, are excluded from
  `summary` counts and from scoring, and can therefore neither inflate nor deflate the score.

## Proactive detectors

| Id | Fires when | Suppressed by |
|---|---|---|
| `R:faq_schema` | Question-shaped headings with answers, and no `FAQPage` in JSON-LD | `entity.structured_data_present` finding |
| `R:facts_in_prose` | Specification-shaped facts stranded in paragraphs | `content.scannable_blocks` finding |
| `R:undated_claims` | Numeric claims with no date and no attribution anywhere on the page | — |
| `R:image_facts_with_alt` | Numbers that exist only in image `alt` text, never in the body | `extraction.facts_not_image_only` finding |

`PLAN.md` §7 listed a fifth example — an `Organization` with `sameAs` but no authority anchor. That
became the scored check `entity.sameas_authority`, so building it here would report one root cause
twice. It is deliberately **not** a detector; see the note in `PLAN.md` §7.

Detectors that rely on word-level heuristics are **language-gated**: on a page whose language is
undetected or unsupported they stay silent rather than guessing, for the same reason the
language-dependent checks resolve to `unknown`.
