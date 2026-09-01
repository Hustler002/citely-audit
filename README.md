# Brand AI-Readiness Audit

An Agent Skill Marketplace (`agentskills.io`-compliant) that audits an arbitrary website and
diagnoses two things: **off-site AI discoverability** (why AI assistants fail to crawl, extract,
corroborate, or cite the brand's facts) and **on-site engagement** (why AI-referred visitors bounce).
It runs read-only and emits a single evidence-backed JSON report.

> **Status: architecture scaffold.** The directory structure, manifests, contracts (schemas), config,
> and SKILL.md files are in place. The Python check/fetch/orchestration logic is present as
> clearly-marked `TODO` skeletons and is not yet implemented.

## Layout

| Path | Purpose |
|------|---------|
| `marketplace.json` | Bespoke manifest declaring all skills; exactly one `entrypoint: true`. |
| `config/scoring-config.json` | Single source of truth for severity thresholds and score weights. |
| `skills/audit-orchestrator/` | **Entrypoint.** Fetch+render once, run analysis skills, merge/rank, validate, emit report. |
| `skills/crawl-render-extraction-audit/` | Mechanic 1 — access, render/readability, fact extraction. |
| `skills/quotability-density-audit/` | Mechanics 2 + 3 — RAG quotability and information density. |
| `skills/entity-corroboration-audit/` | Mechanic 4 — structured-data entity graph + optional Wikidata. |
| `skills/engagement-orientation-audit/` | Mechanic 5 — first-viewport orientation heuristics. |
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

## Run (once implemented)

```bash
# Online: audit a live homepage
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com

# Offline: audit a local HTML file with zero network access
python skills/audit-orchestrator/scripts/run_audit.py --html-file tests/fixtures/healthy_page.html
```

The report is written to **stdout only**; all logs go to stderr.

## Security posture

- Read-only; no auth, no writes; respects `robots.txt` (stops if the audit UA is disallowed on root).
- SSRF guard: `http`/`https` only; rejects private/loopback/link-local/reserved/metadata IPs;
  re-validates on every redirect; caps redirects; per-request timeout and max body size.
- One identifying User-Agent per request.

## Performance budget

Full audit targets **< 5 minutes** for a typical site; a global deadline (default 270s) skips
lowest-priority checks under pressure and marks the report `partial: true` with a reason.

## Rendering strategy (tiered)

- **Tier A** — Playwright render diff when Chromium is available.
- **Tier B** — browserless SPA heuristic (framework markers, empty-shell mount node, script-to-text
  ratio) when it is not; findings labeled `heuristic` and `diagnostics.render_mode = "heuristic"`.

## Validation

```bash
skills-ref validate ./skills/audit-orchestrator   # repeat per skill folder
pytest
```
