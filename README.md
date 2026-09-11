# Citely — Brand AI-Readiness Audit

An Agent Skill Marketplace (`agentskills.io`-compliant) that audits an arbitrary website and
diagnoses two things: **off-site AI discoverability** (why AI assistants fail to crawl, extract,
corroborate, or cite the brand's facts) and **on-site engagement** (why AI-referred visitors bounce).
It runs read-only and emits a single evidence-backed JSON report.

> **Status: working.** The audit runs end-to-end and emits a schema-valid JSON report: safe fetch,
> page selection, tiered rendering, 24 checks across four analyzer skills, capability scoring, and a
> copy-paste fix plus a validation procedure on every finding.
> The report also reads in plain language: a one-sentence verdict per category, actions ranked by the
> score they recover, and an explicit statement of what could not be measured.
> Still to come: a precision/recall harness (Phase 9) and compliance sign-off (Phase 10).

## Layout

| Path | Purpose |
|------|---------|
| `marketplace.json` | Bespoke manifest declaring all skills; exactly one `entrypoint: true`. |
| `config/checks.json` | The check registry — 24 checks, six per category, with weights, severities and report wording. |
| `config/scoring-config.json` | Single source of truth for severity thresholds and score weights. |
| `skills/audit-orchestrator/` | **Entrypoint.** Fetch+render once, run analysis skills, merge/rank, validate, emit report. |
| `skills/crawl-render-extraction-audit/` | Mechanic 1 — access, render/readability, fact extraction. |
| `skills/quotability-density-audit/` | Mechanics 2 + 3 — RAG quotability and information density. |
| `skills/entity-corroboration-audit/` | Mechanic 4 — structured-data entity graph + optional Wikidata. |
| `skills/engagement-orientation-audit/` | Mechanic 5 — first-viewport orientation heuristics. |
| `skills/remediation-advisor/` | **Prescription, not detection.** Turns findings into copy-paste snippets with a validation procedure, and adds proactive suggestions where no defect was found. |
| `tests/` | Fixtures (healthy/broken pages), localhost fixture server, unit + E2E tests. |

## Bespoke convention (not base spec)

`marketplace.json` and the `entrypoint` flag are **this project's platform convention**. The base
`agentskills.io` spec defines only the per-skill folder + `SKILL.md` (requiring `name` + `description`);
it has no marketplace/entrypoint concept.

## Setup

```bash
pip install -e .
# Provision Chromium ONCE (not during a timed audit run). Optional — the audit degrades gracefully without it.
python -m playwright install chromium
```

## Run

```bash
# Online: audit a live homepage
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com

# Offline: audit a local HTML file with zero network access
python skills/audit-orchestrator/scripts/run_audit.py --html-file tests/fixtures/healthy_page.html
```

The report is written to **stdout only**; all logs go to stderr.

## Suggested actions

Every finding carries a `suggested_action` with a plain-language summary, a **validation procedure**
saying how to confirm the fix worked, and — where pasting markup can actually fix the problem — a
**copy-paste snippet**. Snippets are filled only from values **observed on the page**; anything the
page never stated stays a literal `{{PLACEHOLDER}}` and is listed in `placeholders_remaining`, so the
report never invents a business fact. No language model is involved anywhere in the audit, which is
what makes the output deterministic and auditable.

Beyond-problem suggestions live in a separate top-level `recommendations[]`. They are **not**
findings: they are excluded from the summary counts and from scoring, so they can neither inflate nor
deflate the score, and one is never emitted when the check it relates to already produced a finding.

## Reading the report

The report is written to be acted on by someone who is not an SEO engineer, at three depths:

1. `summary.verdict` and `summary.category_verdicts` — one plain sentence each, no jargon.
2. `next_actions[]` — the same findings ordered by the score each fix recovers, each saying what to
   do, where to do it, and how to confirm it worked.
3. `findings[]` — the measurement, threshold, selector and confidence behind every verdict. Present,
   but never the headline.

**A score built on too little evidence is not presented as a verdict.** When under half the check
weight could be measured — a page that assembles itself in the browser, say — `summary.headline_
reliable` is `false`, the verdict says the site could not be measured, and `not_checked[]` names
each gap and its cause in plain words. The numbers stay in `summary`, because declining to headline
a measurement is not the same as hiding it.

## Security posture

- Read-only; no auth, no writes; respects `robots.txt` (stops if the audit UA is disallowed on root).
- SSRF guard: `http`/`https` only; rejects private/loopback/link-local/reserved/metadata IPs;
  re-validates on every redirect; caps redirects; per-request timeout and max body size.
- One identifying User-Agent per request.

## Performance budget

Full audit targets **< 5 minutes** for a typical site; a global deadline (default 270s) skips
lowest-priority checks under pressure and marks the report `partial: true` with a reason.

## Rendering strategy (tiered)

- **Tier A** — Playwright render diff when Chromium is available. Navigation waits for `load`, never
  for `networkidle`: that state needs 500ms with almost nothing in flight, which analytics beacons,
  chat widgets and long-polling never allow, so it does not fire on most real sites. Quiescence is
  pursued afterwards under its own small budget, and a navigation timeout **salvages** the rendered
  DOM rather than discarding it. `diagnostics.render_nav_state` reports `ok`, `busy` or `salvaged`.
- **Tier B** — browserless SPA heuristic (framework markers, empty-shell mount node, script-to-text
  ratio) when it is not; findings labeled `heuristic` and `diagnostics.render_mode = "heuristic"`.
  Above-the-fold checks fall back to a DOM-order proxy budgeted in **visible text**, with
  non-rendering subtrees pruned and navigation chrome charged at a capped discount, so a mega-menu
  or an inline SVG sprite cannot push the real content out of the measured window.

## Validation

```bash
skills-ref validate ./skills/audit-orchestrator   # repeat per skill folder
pytest
```
