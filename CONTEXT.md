# CONTEXT.md — Agent Skill Marketplace: AI Discoverability & Engagement Audit

> ⚠️ **HISTORICAL DOCUMENT — preserved verbatim. Do not implement from this file.**
> This is the original **problem statement** and the **first draft design**, kept unedited as the record of
> what was originally asked. Its architecture, decisions, and the `brand-ai-readiness-audit/` tree in §2.1
> have all been **superseded by [`PLAN.md`](PLAN.md)** (MASTER PLAN v3), which is the authoritative
> implementation plan. Current progress lives in [`CLAUDE.md`](CLAUDE.md). The project is now named
> **Citely** (`citely-audit`).

> Handoff doc. Read this top to bottom before doing any implementation work.
> Status tags used throughout: **[CONFIRMED]** = user explicitly decided this · **[RECOMMENDED]** = assistant's proposal, not yet confirmed · **[OPEN]** = genuinely undecided.

---

## 1. Problem Statement (from the original hackathon brief)

**Role/goal:** Build a self-contained **Agent Skill Marketplace**, adhering to the `agentskills.io` standard, that audits any arbitrary, unseen website and diagnoses:
1. **Off-site AI discoverability** — why AI assistants (ChatGPT, Perplexity, Gemini, SearchGPT, etc.) fail to crawl, extract, corroborate, or cite the brand's key facts.
2. **On-site engagement** — why human visitors arriving from AI search snippets bounce or fail to orient themselves.

Output: a structured, evidence-backed report with prioritized, mechanism-sound recommendations.

### 1.1 Core failure mechanics the audit must be built around
1. **Three-Stage Crawl & Ingestion Funnel** — Access (robots.txt, bot headers, HTTP status) → Render & Readability (SSR vs CSR/SPA — facts needing JS hydration get missed by simple crawlers) → Fact Extraction (facts trapped in images/SVG/canvas without semantic HTML fail to parse into embeddings/context).
2. **Real-Time Source Selection & Quotability (RAG behavior)** — self-contained, quotable factual statements get cited; vague marketing prose gets discarded.
3. **Information Density & Summarizer Survival** — key facts surrounded by low-value filler get dropped when AI summarizers condense long pages.
4. **Entity Disambiguation & Cross-Web Corroboration** — missing `schema.org`/JSON-LD entity graphs (`sameAs`, Wikidata/social mappings) → hallucination or mistaken-identity risk.
5. **First-Viewport Orientation & Visitor Retention** — no clear value proposition above the fold → visitors bounce even when the AI citation was correct.

### 1.2 Mandatory constraints
- **Marketplace manifest:** root `marketplace.json` declares all skills; **exactly one** skill marked `entrypoint: true`.
- **Skill compliance:** every skill folder = `agentskills.io`-spec compliant — `SKILL.md` with YAML frontmatter, deterministic procedure, inputs, outputs, optional `scripts/` and/or `references/`.
- **Read-only / non-destructive:** runs entirely read-only in a sandbox — no site modifications, no authenticated actions, no aggressive rate-limiting, must respect `robots.txt`.
- **Performance budget:** full audit run < 5 minutes for a typical site.
- **Deliverable:** the entrypoint skill emits a single JSON report matching the schema floor below.

### 1.3 Minimum output schema (mandatory — extra fields allowed, none of these may be omitted)
```json
{
  "site": "example.com",
  "audited_at": "2026-09-20T14:32:00Z",
  "summary": { "total_findings": 0, "critical": 0, "high": 0, "medium": 0 },
  "findings": [
    {
      "id": "F-001",
      "title": "<Concise issue title>",
      "severity": "<critical | high | medium>",
      "evidence": "<Concrete, reproducible observed proof>",
      "suggested_action": {
        "summary": "<Mechanism-sound, actionable fix>",
        "priority": "<critical | high | medium>"
      }
    }
  ]
}
```

### 1.4 Note on the standard (verified, not assumed)
`agentskills.io` is a real, adopted open standard (originally created by Anthropic; now used across Claude, Codex, Gemini CLI, Copilot, Cursor, VS Code, etc.). The *base* spec only requires a folder + `SKILL.md` with at minimum `name` + `description`; body has no required format but a ~5,000-token ceiling is recommended (whole body loads into context on activation). **The base standard does not define `marketplace.json`** — that's a platform-level convention. This project's own brief defines its own `marketplace.json` schema (§1.2), which is treated as authoritative here — not any other platform's format.

The example skill layout/names in the original brief (`audit-orchestrator`, `crawl-render-audit`, `freshness-corroboration`, `engagement-audit`) are **illustrative only** — an independent decomposition was explicitly requested.

---

## 2. Architecture / Plan

### 2.1 Directory structure
```
brand-ai-readiness-audit/
├── marketplace.json
├── README.md
└── skills/
    ├── audit-orchestrator/              <-- entrypoint: true
    │   ├── SKILL.md
    │   ├── scripts/run_audit.py
    │   └── references/
    │       ├── report-schema.json
    │       ├── severity-rubric.md
    │       └── sample-report.json
    ├── crawl-render-extraction-audit/
    │   ├── SKILL.md
    │   └── scripts/crawl_render_extract.py
    ├── quotability-density-audit/
    │   ├── SKILL.md
    │   └── scripts/quotability_density.py
    ├── entity-corroboration-audit/
    │   ├── SKILL.md
    │   └── scripts/entity_corroboration.py
    └── engagement-orientation-audit/
        ├── SKILL.md
        └── scripts/engagement_orientation.py
```

### 2.2 Skill decomposition rationale (orchestrator + 4 sub-skills, not the example's 3)
Mapped onto the 5 mechanics (§1.1), not copied from the example:
- **`crawl-render-extraction-audit`** — bundles mechanic 1's three stages (Access + Render/Readability + Fact Extraction) into one skill because they share a single crawl session (raw fetch → rendered fetch → DOM diff). Bundling avoids redundant requests to the target site (budget + politeness).
- **`quotability-density-audit`** — bundles mechanics 2 + 3. Both are pure text/structure analysis over content already harvested by the skill above; no new network calls needed, so bundling avoids re-crawling.
- **`entity-corroboration-audit`** — mechanic 4. Kept separate: distinct data pattern (structured data + optional external corroboration) vs. prose analysis.
- **`engagement-orientation-audit`** — mechanic 5. Kept separate: UX/visual reasoning over the rendered DOM, not textual AI-readiness.
- **`audit-orchestrator`** (entrypoint) — sequences the above, merges findings, assigns IDs, computes summary, validates against schema, emits final report.

### 2.3 Shared contracts
- **Single crawl session:** only `crawl-render-extraction-audit` touches the network for page content; it caches raw HTML, rendered HTML, and timing/availability metadata for the other three skills to consume.
- **Per-finding contract (extends §1.3, non-breaking):**
  - `category` — which of the 5 mechanics the finding belongs to
  - `confidence` — `"verified"` vs `"heuristic"` (epistemic honesty — most checks are inference, not ground truth)
  - `suggested_action.snippet` (optional) — a concrete, copy-pasteable fix (e.g. actual JSON-LD), not just prose
- **Shared severity rubric** (`references/severity-rubric.md`) — defines critical/high/medium per mechanic so the four independently-written skills judge severity consistently.
- **Dual-mode scripts** — every script is both an importable function (for the orchestrator) and independently CLI-runnable (`python quotability_density.py --html-file page.html`) — satisfies "deterministic procedure, inputs, outputs," and doubles as the local test harness.

### 2.4 Orchestrator responsibilities
- Fetch robots.txt + raw/rendered HTML once. If the audit's own UA is disallowed on the root path, **stop** — that becomes the top finding, no silent proceeding.
- Run the other 3 skills against shared artifacts.
- Merge findings, assign `F-001…`, compute `summary` counts, validate the assembled JSON against `references/report-schema.json`.
- Track elapsed time against the 5-minute budget; if at risk, skip remaining lower-priority checks and mark the report `"partial": true` with a reason (additive field).

### 2.5 Guardrails baked in throughout
One page fetched per audit (homepage only, v1) · polite/identifying User-Agent on every request · fully read-only, no auth, no writes.

---

## 3. Decisions Log

| Decision | Status | Detail |
|---|---|---|
| Skill decomposition (§2.2) | **[RECOMMENDED]** | Orchestrator + 4 sub-skills, mapped to the 5 mechanics |
| Language/runtime | **[RECOMMENDED]** | Python 3.11+ (`requests`, `urllib.robotparser`, `BeautifulSoup4`/`lxml`, `playwright`) — user explicitly deferred this choice ("you decide") |
| Render/readability check | **[CONFIRMED]** | User explicitly chose a real headless browser (Playwright) for an accurate JS-render diff, over a lightweight heuristic-only approach |
| Entity-corroboration depth | **[RECOMMENDED]** | User deferred ("you decide"). Assistant's call: on-page JSON-LD/Open-Graph extraction as the always-on reliable core, plus an optional soft-fail live Wikidata lookup that never blocks/crashes the audit and is labeled unverified if it fails |
| Graceful Playwright fallback if browser install is missing/fails | **[OPEN]** | Recommended by assistant; user has not yet said whether this is in scope or whether it should fail loudly instead |
| Multi-page audits (beyond homepage) | **[OPEN]** | Not discussed yet; v1 assumes homepage-only |
| Implementation start | **[OPEN — explicitly paused]** | User has twice instructed: plan only, no implementation yet |

---

## 4. Recommendations (open, pending confirmation)
1. **Composite 0–100 "Discoverability Score"** in `summary` — non-breaking addition; stronger single-number headline than a bare findings list for a demo.
2. **`diagnostics` block** — crawl duration, `playwright_available: true/false`, pages checked, UA used — transparency so numbers are auditable, not just asserted.
3. **`references/sample-report.json`** — a pre-generated example report checked into the repo, so reviewers see real output without running anything.
4. **`--html-file` / offline mode on every script** — enables local testing with zero network access; also protects against restricted-egress execution environments generally.
5. **Shared severity rubric doc** (already folded into §2.3) — keeps four independently-written skills consistent.
6. **`suggested_action.snippet`** (already folded into §2.3) — copy-pasteable fixes, not just diagnosis.
7. **Graceful Playwright fallback** (see Decisions Log — this is the one recommendation still awaiting an explicit yes/no).

---

## 5. Known Practical Constraint (for whichever AI/environment builds this)
Headless-browser rendering (Playwright) requires downloading browser binaries from a CDN, and outbound `requests` calls need to reach the *arbitrary target site* — both need real internet egress. Sandboxed coding environments often restrict egress to package registries only. If that's the case here too: the render-diff and live-fetch logic should still be written correctly, but validated locally via a fixture HTTP server (a "healthy" test page + a "broken" test page served on `localhost`) rather than assumed to have been run against a live external site.

---

## 6. Explicit Open Questions for the User
1. Should the Playwright fallback (item 7 above) be built in, or should a missing/failed browser install fail the audit loudly instead?
2. Is homepage-only auditing acceptable for v1, or should multi-page (e.g. homepage + `/about`) be in scope from the start?
3. Do items 1–6 in §4 get built as designed, or should any be dropped/changed?
