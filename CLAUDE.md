# CLAUDE.md — Citely · Project State & Progress

> **Purpose: the running record of progress.** A new chat session reads this to learn where the work
> actually stands. Update "Current status", the task checklists, and the "Progress log" as work advances.

## Document roles (read in this order)

| File | Role | Authority |
|---|---|---|
| [`CONTEXT.md`](CONTEXT.md) | Original **problem statement** + the very first draft design | **Historical only.** Preserved verbatim; superseded — do not implement from it |
| [`PLAN.md`](PLAN.md) | **MASTER PLAN v3** — the final, rubric-aligned implementation plan | **Authoritative.** All design decisions live here |
| `CLAUDE.md` (this file) | **Progress** — what is done, what is next, current repo state | Authoritative for *status*, never for *design* |
| [`README.md`](README.md) | Usage, setup, security posture | User-facing |

If this file and `PLAN.md` ever disagree on design, **`PLAN.md` wins** — and fix this file.

## What this project is

**Citely — Brand AI-Readiness Audit**: an Agent Skill Marketplace (`agentskills.io`-compliant) that audits
a website and diagnoses (1) **off-site AI discoverability** (why AI assistants fail to crawl / extract /
corroborate / cite the brand's facts) and (2) **on-site engagement** (why AI-referred visitors bounce).
Read-only, non-destructive, **< 5-minute** budget, emits one JSON report (+ optional HTML).

Built on the 5 failure mechanics: (1) crawl/render/extraction funnel, (2) RAG quotability,
(3) information density / summarizer survival, (4) entity disambiguation & cross-web corroboration,
(5) first-viewport orientation & retention.

## Repository state (verified)

- **Path / git root:** `C:\Users\PRIYANSHU PAL\Desktop\ADOBE\citely-audit`
- **Branch:** `main` · **Commits:** 3, latest `f2aa690 Add tests for SPA detection and security measures`
  (41 files tracked, as of 2026-09-06). Phase 4 work is uncommitted.
- `.gitignore` present; `PLAN.md` is tracked.
- Project renamed `brand-ai-readiness-audit` → **`citely-audit`**. Identity fields rebranded;
  **skill folder names deliberately unchanged** (descriptive + agentskills.io-valid).

## Locked decisions (current — per PLAN.md v3; do not re-litigate)

- **Recommend-only.** No skill modifies any site. No apply-fix, no re-audit loop, no writes to the target.
  Suggestions may be **proactive** (improvements where no defect was found).
- **Orchestration** = subprocess + JSON file contract. The orchestrator owns **ALL** network I/O; every
  analyzer is a pure, network-free function of the shared crawl artifact — **zero exceptions**.
- **Wikidata lookup** sits **in the orchestrator's fetch stage** (left of the artifact boundary), stored as
  `external_corroboration`. Off by default (`--allow-external`), soft-fail → dependent checks `unknown`.
- **Rendering** = tiered. Tier A = Playwright render diff; Tier B = browserless SPA heuristic. Chromium is
  provisioned as a SETUP step, never inside the timed run. Disclosed via `diagnostics.render_mode`.
- **Page scope** = homepage + **sitemap-aware sample** (default 5), degrading silently to homepage-only.
  Page count is a budget-derived *maximum*; skipped pages' checks are `unknown`, never `fail`.
- **Scoring** = capability model. Checks resolve to `pass`/`partial`/`fail`/`not_applicable`/`unknown`;
  category score = weighted mean over *applicable* checks only. `unknown`/`not_applicable` leave the
  denominator. **Dependency suppression** collapses correlated findings; **anti-dodge rule** = suppression
  requires a `verified` failed prerequisite.
- **Severity** = strict 3-tier (`critical`/`high`/`medium`), no `low`. Invariant: `total_findings ==
  critical + high + medium`. Proactive items are **not findings** — separate `recommendations[]`.
- **Confidence** = `verified` vs `heuristic`. `summary.score_confidence` is a ratio, not a third value.
- **`summary.coverage`** (added in Phase 1) = share of total check weight actually scored. **Independent of
  confidence** and both are required: a CSR shell suppresses ~19/24 checks and scores 85 at confidence 1.0,
  but coverage 0.23 exposes that the headline rests on a thin slice. Never show a score without coverage.
- **robots.txt = two separate concerns** (decided 2026-09-05, PLAN.md §6.3.1). (1) *Operational gate* —
  may **CitelyAuditBot** fetch? If not: don't fetch, one critical finding, `partial_reason: "blocked"`,
  `blocked_kind: "robots_disallowed"`, all checks `unknown` with reason `blocked_before_fetch`. **Never
  scored.** (2) *Scored signal* — check `access.ai_crawlers_allowed` asks whether **GPTBot / ClaudeBot /
  PerplexityBot / Google-Extended / CCBot** etc. are allowed. A site can allow us and still be invisible
  to ChatGPT; scoring our own access would pass on every site and miss that entirely. This check has
  **no dependents by design** — an AI-crawler block doesn't stop *us* reading the page.
- **`summary.discoverability_score`** retained as the **overall** score; category scores added alongside.
- **No LLM anywhere** in the fetch, analysis, or scoring path — the basis of determinism, auditability,
  and prompt-injection resistance.
- **i18n gating:** language-dependent checks return `unknown` (never `fail`) when page language is
  undetected/unsupported. Prevents mass false positives on non-English sites.
- Each skill is a self-contained folder (agentskills.io) → helpers and default thresholds are
  **duplicated across skills by design**, never shared by import. Drift is caught by explicit tests
  in `tests/test_analyzers.py` (`test_spa_markers_match_config`, `test_embedded_thresholds_match_registry`,
  `test_sanitize_behaves_identically_across_skills`, …), NOT merely asserted.
- **Analyzers emit check states, not findings** (PLAN §6.1). Report wording lives once in
  `config/checks.json` and is applied by the orchestrator, so it cannot drift between analyzers.
  Contract: `references/check-result-schema.json`.

## Verified spec facts (agentskills.io)

- Skill = folder + `SKILL.md`; required frontmatter is only `name` + `description`. Optional: `license`,
  `compatibility` (≤500 chars), `metadata` (string→string), `allowed-tools` (experimental).
- `name`: ≤64 chars, lowercase alnum + single hyphens, no leading/trailing/consecutive hyphens,
  MUST match the folder name.
- Body recommended < 5000 tokens / < 500 lines; push detail to `references/`.
- Base spec has **no** `marketplace.json` / `entrypoint` concept — bespoke to this brief.
- Official validator: `skills-ref validate ./<skill>` (github.com/agentskills/agentskills).

## Current status: PHASE 5 COMPLETE — all 24 checks implemented, all four categories score

**349 tests, 13 skipped.** All **24 of 24 checks are implemented** across four analyzers, and the
full four-category score is produced end-to-end.

Verified on a live Tier-A render against the fixture server:
| Page | Overall | Coverage | States |
|---|---|---|---|
| healthy (SSR) | 98.8 | 0.975 | 22 pass / 1 partial |
| SPA shell | 85.3 | **0.23** | 4 pass / 1 fail / 19 unknown |

The SPA row is the model working as designed: one root cause suppresses 19 checks, so the headline
85.3 is honest only because coverage 0.23 sits beside it.

**Chromium IS provisioned**; Tier-A rendering verified end-to-end.

Still missing: the other two analyzers (Phase 5) and orchestrator wiring (Phase 6). The entrypoint
`run_audit.py` still returns `{"_status": "not_implemented"}`, so **there is no runnable report yet**.
The 13 skips are the two unimplemented analyzers' stub files.

**Environment:** `.venv/` created, all pinned deps installed (`pytest`, `jsonschema`, `lxml`,
`beautifulsoup4`, `playwright`, `requests`) **and Chromium provisioned**.
Run tests with `./.venv/Scripts/python.exe -m pytest`.
**Disk note:** ~1.6 GB free after the Chromium install.

### Implementation phases
| Phase | Scope | Status |
|---|---|---|
| 1 | Scoring spine & schema contracts | ✅ **DONE** |
| 2 | Safe acquisition (`_safe_fetch.py`, SSRF, robots) + security corpus | ✅ **DONE** |
| 3 | Artifact pipeline (`_page_select.py`, `_render.py` Tier A/B) | ✅ **DONE** |
| 4 | Structural analyzers (crawl/render/extraction, entity) | ✅ **DONE** |
| 5 | Parity analyzers + i18n (engagement, quotability) | ✅ **DONE** |
| 6 | Orchestration & report assembly | next |
| 7 | `remediation-advisor` (6th skill) + proactive suggestions | |
| 8 | Non-expert output layer + `render_html.py` | |
| 9 | `labeled_corpus.json` + precision/recall + archetype fixtures | |
| 10 | Compliance & sign-off (`skills-ref validate`, README) | |

### Done
- [x] Full scaffold: manifest, 2 JSON Schemas, config, 5 `SKILL.md`, script skeletons, fixtures, tests.
- [x] `pyproject.toml` (pinned deps, Py 3.11+); `.gitignore`; initial commit on `main`.
- [x] `PLAN.md` (MASTER PLAN v3) added to the repo as the authoritative plan.
- [x] **Rename to `citely-audit`** — `marketplace.json`, `pyproject.toml`, both schema `$id`s,
      `report-schema.json` title, `audit-orchestrator/SKILL.md` `metadata.project`, `README.md` H1.
- [x] `CONTEXT.md` marked historical (text preserved verbatim); `CLAUDE.md` re-synced to v3.

### Phase 1 delivered
- [x] `config/checks.json` — 24 checks, **6 per category (exact parity)**, weights sum to 100 per category.
- [x] `config/scoring-config.json` — rewritten for the capability model (v1's penalty model removed).
- [x] `_scoring.py` — states/credits, category + overall scores, dependency suppression, **anti-dodge rule**,
      i18n gating, `score_confidence`, **`coverage`**, `points_recoverable`, deterministic finding ids,
      summary invariant enforcement, fail-fast registry validation (cycles, unknown deps, weight sums).
- [x] Both schemas extended to v3 (`category_scores`, `score_confidence`, `coverage`, `recommendations[]`,
      finding reasoning-chain fields, `partial_reason` enum, artifact `pages[]` + `external_corroboration`).
- [x] `sample-report.json` rebuilt as the CSR-shell case; **every number verified against the engine**.
- [x] `tests/test_scoring.py` (39) + `tests/test_schemas.py` (30) — **69 passing, 13 skipped** (skips are
      Phase 2+ stub files).

## ⚠ Open issues (as of 2026-09-04, end of Phase 1)

| # | Issue | Severity | Status |
|---|---|---|---|
| 1 | Robots check had zero dependents / wrong subject | High | ✅ **RESOLVED 2026-09-05** — reframed, see Locked decisions |
| 2 | Robots-block behaviour unspecified in PLAN.md v3 | High | ✅ **RESOLVED 2026-09-05** — decided and written into PLAN.md §6.3.1 |
| 3 | `blocked_kind` enum had no robots value | Medium | ✅ **RESOLVED** — `robots_disallowed` added |
| 4 | **Nothing is committed.** Phases 1-2 uncommitted. | Medium | **User commits manually** (their choice) |
| 5 | **Disk headroom is thin** — ~738 MB free. Phase 3 needs ~150 MB for Chromium; the drive already hit 100% once during setup. | Medium | Monitor |
| 6 | Chromium not provisioned | Low | ✅ **RESOLVED** — installed, Tier A verified |
| 9 | **Content-type was unenforced** (PLAN §10), **timeouts duplicated** across `budgets`/`fetch`/`render`, **global deadline unwired**, size cap gap, undeclared artifact fields, 3 dead config keys, dead code, stale role enum. | High→Low | ✅ **ALL 8 RESOLVED 2026-09-05** |
| 10 | **A category can score 100 from a single measurable check.** On the SPA fixture, Human Orientation reads 100.0 because 5 of its 6 checks were suppressed and only `viewport_meta` remained. Overall `coverage` (0.23) exposes this, but a per-CATEGORY coverage figure would stop a category headline being read as a clean bill of health. Relevant to the rubric's output-design criterion. | Medium | Decide in Phase 6/8 |
| 8 | **Browser relaunched per page.** `render_page` launches Chromium for every page (~3-4 s of the ~5 s per-page cost). At 5 pages that is ~25 s of the 270 s budget — acceptable now, but reusing one browser across pages is the obvious win if the budget ever tightens. | Low | Optimize if needed |

| 7 | **`allow_private_hosts` is a live SSRF bypass switch.** Default false and test-only, but if it were ever set true in a real run the guard is fully disabled. Phase 3 must pass it only from test fixtures, never from CLI input. | Medium | Guard in Phase 3 |

**No known defects in the code** — scoring, registry, schemas and the fetch layer are green
(157 tests) and internally consistent. Items 4-7 are environment/process//forward-looking, not bugs.

### Phase 2 delivered
- [x] `_safe_fetch.py` — scheme/port allowlists, URL-credential rejection, **all** A/AAAA records
      validated, **IP pinning** (closes the DNS-rebinding TOCTOU window), per-hop redirect
      re-validation with loop detection + hop cap, body & decompression-bomb caps, connect/read
      timeouts, TLS failures recorded never downgraded, credential/header redaction.
- [x] `check_robots()` implementing the two-concern policy: operational gate for our UA
      (4xx ⇒ allow-all, 5xx/unreachable ⇒ conservative refuse) **and** AI-crawler evaluation for the
      scored check, plus `Crawl-delay` and `Sitemap:` extraction.
- [x] `config`: added `connect_timeout_s`, `read_timeout_s`, and `allow_private_hosts`
      (**test-only escape hatch**, default false — required so Phase 3 E2E can reach the localhost
      fixture server, which the SSRF guard correctly blocks).
- [x] `tests/test_security.py` — 83 tests. Guards **mutation-verified**: deliberately breaking
      per-hop revalidation and the 6to4 unwrap each fail the suite.

### Phase 3 delivered
- [x] `_page_select.py` — **structural, language-neutral** selection (nav membership + URL depth).
      Replaces the English keyword list, which violated PLAN.md §8 and would have degraded every
      non-English site to homepage-only. Sitemap is a fallback and degrades silently (gzip, index,
      malformed, oversized all handled).
- [x] `_render.py` — Tier A real Chromium render (1280x800) + Tier B browserless SPA heuristic
      (empty mount node, framework markers, script-to-text ratio). `force_tier` makes both paths
      deterministically testable.
- [x] `_artifact.py` — assembles the crawl artifact: robots gate, language detection (declared
      `html lang` only, never guessed), **blocked-page detection** (consent wall / bot challenge /
      captcha / login wall), per-page fetch+render, budget skipping. Never raises.
- [x] `tests/fixture_server.py` — threaded fixture server with robots variants, sitemaps (index,
      gzip, malformed), redirect chain/loop/no-location/internal, compression bomb, interstitials.
- [x] `tests/fixtures/spa_hydrating.html` — a **genuinely hydrating** SPA. The old `broken_page.html`
      is a static shell whose bundles 404, so it could never demonstrate a render diff.
- [x] `tests/test_artifact.py` — 70 tests. Verified end-to-end: hydrating SPA raw 55 -> rendered 510
      chars (+455); server-rendered control shows +0 and is correctly not flagged.

### Phase 4 delivered
- [x] **Analyzer wire format decided**: analyzers emit **check states**, not findings (PLAN §6.1
      "Checks, not findings, are the unit"). The old skeleton docstrings said "partial findings" —
      stale, now corrected. Report wording stays in `checks.json` as one source of truth.
- [x] `references/check-result-schema.json` — the third cross-process contract, now pinned.
- [x] `crawl-render-extraction-audit` — all 6 AI Discoverability checks.
- [x] `entity-corroboration-audit` — all 6 Entity Trust checks. Accepts entity evidence from ANY
      page (markup legitimately lives on /about), prefers the rendered DOM (tag managers inject
      JSON-LD), handles `@graph`, and treats structured data as claims with size/depth caps.
- [x] Both keep `--html-file` offline mode, so each skill is independently runnable.
- [x] `tests/test_analyzers.py` — 57 tests incl. subprocess contract, adversarial injection,
      malformed HTML, and integration into the scoring engine.

### Phase 5 delivered
- [x] `quotability-density-audit` — 6 AI Comprehension checks (title, meta description, heading
      hierarchy, scannable blocks, quotability, density).
- [x] `engagement-orientation-audit` — 6 Human Orientation checks, with a **two-tier measurement**
      strategy mirroring the render tiers: real geometry (pixel offsets, computed font size, overlay
      coverage) captured during a Tier-A render, falling back to a DOM-order proxy in Tier B and
      saying so in `reason`. Legibility resolves to `unknown` without a browser rather than guessing.
- [x] `_render.py` now captures `geometry` (headings/interactive tops, body font px, overlays,
      above-fold text); declared in the artifact schema.
- [x] **i18n gating built in from the start**: 3 of 12 new checks are language-dependent and resolve
      to `unknown` on an unsupported/undetected language. `--html-file` mode detects `<html lang>`
      locally so standalone runs still work.
- [x] `tests/fixtures/non_english_page.html` — a structurally excellent German page. Tests assert it
      passes every language-independent check and never *fails* a language-gated one.
- [x] `tests/test_analyzers_phase5.py` — 50 tests.

### Not yet done — implementation order (PLAN.md §14)
- [x] ~~**2.** `_safe_fetch.py` + security corpus.~~ **DONE**
- [ ] **3.** Artifact assembly, `_page_select.py` (sitemap → homepage fallback), `_render.py` (Tier A/B);
      fixture server routes (robots, sitemap, redirect, bomb).
- [x] ~~**4.** structural analyzers~~ **DONE**
- [x] ~~**5.** parity analyzers + i18n~~ **DONE**
- [ ] **6.** Orchestrator wiring → schema-valid JSON report.
- [ ] **7.** `remediation-advisor` skill (**new, 6th**) incl. non-obvious proactive suggestions.
- [ ] **8.** Non-expert output layer + `render_html.py` (folded in — `report-renderer` was cut as padding).
- [ ] **9.** `labeled_corpus.json` + precision/recall harness; non-English, consent-wall, adversarial fixtures.
- [ ] **10.** `skills-ref validate` all 6 skills; README refresh; determinism + read-only sign-off.

### Structural deltas from the current scaffold (v3 requires)
- **Add** `config/checks.json` (check registry) — does not yet exist.
- **Add** 6th skill `remediation-advisor/`; **do not** add `report-renderer` (cut as padding).
- **Split** `run_audit.py` into `_render.py`, `_page_select.py`, `_scoring.py`, `render_html.py`.
- **Add** fixture archetypes: static, spa, ecommerce, corporate, image-heavy, unstructured,
  **non-english**, **consent-wall**, **adversarial** (+ `labeled_corpus.json`).
- **Extend** schemas: `summary.category_scores`, `summary.score_confidence`, top-level `recommendations[]`,
  `findings[].{page_url,check_id,measurement,threshold,selector,impact,plain_summary}`,
  `suggested_action.validation`, `partial_reason` enum; artifact gains `pages[]` + `external_corroboration`.

## How to run / verify (once implemented)

From the repo root (`citely-audit`):
```bash
pip install -e .
python -m playwright install chromium            # setup step, optional (Tier A)
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com
python skills/audit-orchestrator/scripts/run_audit.py --html-file tests/fixtures/healthy_page.html
pytest
for s in skills/*/; do skills-ref validate "$s"; done
```
Report goes to **stdout only**; logs to **stderr**; HTML only via `--html-out`.

## Conventions & guardrails

- Read-only; respect robots.txt (+ `meta robots` / `X-Robots-Tag`). See **robots policy** in Locked
  decisions and PLAN.md §6.3.1.
- Never crash: every stage records failures into `diagnostics.errors[]` and still emits a schema-valid report.
- Global monotonic deadline (default 270s); on pressure set `partial: true` + `partial_reason`
  (`budget` | `blocked` | `render_failed` | `analyzer_failed`).
- Treat every fetched byte as hostile; page-derived text reaches the report only sanitized, truncated, and
  delimited as untrusted data — never as instructions.
- User-Agent: pin an identifying string with a contact URL (**contact URL still TODO**).
- Partial finding shape (analyzer output, no `id`): `{title, severity, category, confidence, evidence,
  suggested_action:{summary, priority, snippet?}}`. The orchestrator assigns `F-001…`.

## Progress log

- 2026-09-01 — Reviewed original plan, found 30 mistakes/gaps, wrote corrected production plan (approved).
- 2026-09-01 — Scaffolded full directory structure + architecture (contracts, config, SKILL.md, skeletons, tests).
- 2026-09-04 — Adobe/competitor research; red-teamed plan → **v2** (capability scoring, security hardening).
- 2026-09-04 — **Judging rubric received** → rewrote as **MASTER PLAN v3**: cut `report-renderer` as padding,
  raised engagement to parity, promoted proactive suggestions to must-build, added i18n gating
  (fixes an English-only false-positive defect), added precision/recall harness.
- 2026-09-04 — Renamed project to **Citely** (`citely-audit`); `git init` → `main`, initial commit `ab10281`;
  `.gitignore` added; `PLAN.md` copied into repo; identity fields rebranded; `CONTEXT.md` marked historical;
  this file re-synced to v3.
- 2026-09-04 — **Phase 1 complete.** Built the scoring spine: 24-check registry, capability scoring with
  dependency suppression + anti-dodge rule, i18n gating, deterministic finding ids; extended both schemas
  to v3; rebuilt and numerically verified `sample-report.json`. 69 tests green.
  Two fixes found by the tests themselves: (a) a missing check with a failed prerequisite now attributes
  `suppressed_by_failed_prerequisite` rather than `not_measured`; (b) the anti-dodge rule was untestable
  against the real registry (nothing depends on a heuristic check), so it is now covered by a synthetic
  registry instead of being skipped. Also added **`coverage`** after the sample report revealed that
  suppression can produce a high score from very few checks.
  Env fix: `pyproject.toml` gained `[tool.setuptools] packages = []` — the project ships no importable
  package (skills are subprocess-invoked), which was breaking `pip install -e .`.
- 2026-09-04 — Pre-Phase-2 review. Corrected the test split recorded above (39+30, not 43+26) and logged
  six open issues, two of which (robots dependency + robots-block behaviour) must be settled before
  Phase 2 implements `_safe_fetch.py`.
- 2026-09-05 — **Robots reframe (issues 1–3 closed).** Discovered that `access.robots_allows` measured
  whether *our own* bot was permitted — which passes on essentially every site, since nobody blocklists an
  unknown auditor UA, making the check near-worthless and hiding the single most direct AI-discoverability
  failure. Renamed to **`access.ai_crawlers_allowed`** and repointed at the real AI crawler UAs (GPTBot,
  ClaudeBot, PerplexityBot, Google-Extended, CCBot, …), with the token list in `scoring-config.json` so it
  can be updated without code changes. Our own fetch permission became a non-scored operational gate.
  Added `REASON_BLOCKED` + `blocked_before_fetch` to `_scoring.py`, `robots_disallowed` to `blocked_kind`,
  and 5 tests (74 passing). Sample-report numbers re-verified unchanged; 24 checks and 6/6/6/6 parity held.
- 2026-09-05 — **Phase 2 complete.** Implemented `_safe_fetch.py` (SSRF guard with IP pinning, per-hop
  redirect revalidation, size/decompression caps, timeouts, redaction) and `check_robots()` with the
  two-concern policy. 83 security tests; full suite 157 passing.
  **Mutation-tested the guards rather than trusting a green run**, which paid off twice: (a) removing
  per-hop revalidation correctly failed, but (b) removing the IPv4-mapped unwrap failed NOTHING —
  Python 3.11's `is_private` already covers `::ffff:` , so that branch is belt-and-braces and the tests
  were passing for a reason other than our code. Investigating that showed **6to4 (`2002::/16`) is caught
  by *nothing* in Python's predicates** — our `sixtofour` unwrap is the only thing blocking it, and it
  had zero tests. Added 10 IPv6-transition tests (6to4 / NAT64 / IPv4-compatible / Teredo) plus an
  over-blocking guard, and annotated the load-bearing branch so it is not "simplified" away later.
  Corrected an earlier wrong claim of mine: NAT64 and IPv4-compatible are *not* gaps — `is_reserved`
  covers them.
- 2026-09-05 — **Phase 3 complete**, then an 8-issue cleanup audit before Phase 4.
  Built `_page_select.py` (structural/language-neutral — replaced an English keyword list that
  violated PLAN §8), `_render.py` (Tier A Chromium 1280x800 + Tier B heuristic), `_artifact.py`,
  and a threaded fixture server. Added `spa_hydrating.html` because the old SPA fixture's bundles
  404, so raw==rendered and the render diff was never actually exercised; the new one proves it
  (55 -> 510 chars) with a server-rendered control at +0.
  Bugs found by testing rather than assumption: single-threaded fixture server deadlocked on
  redirects (needed ThreadingHTTPServer); `deadline_s: None` violated the schema.
  **Cleanup audit found 8 real issues**, notably that PLAN §10's `text/html`-only rule was never
  implemented, timeouts had two sources of truth (the exact drift hazard PLAN mistake #22 warns of),
  and `budgets.global_deadline_s` was read by nothing. All 8 fixed with regression tests.
  **My first content-type fix was inert and the full 211-test suite stayed green** — only an explicit
  end-to-end probe caught it. Behaviour is now asserted, not assumed.
- 2026-09-05 — **Phase 4 complete.** Implemented the two structural analyzers (12 of 24 checks).
  Resolved a contract contradiction first: PLAN §6.1 says checks are the unit, but the scaffold's
  docstrings said "partial findings" — settled on **check states**, so report wording never drifts.
  **Three false positives found and fixed during development**, all of the same family — the
  render check conflating "needs JavaScript" with "page is short":
    1. The healthy fixture (461 chars, fully server-rendered) was flagged `partial`.
    2. After a first fix, an 80-char but genuine local-business page still failed, because the
       `empty_floor_chars` floor was 100. Lowered to 50 — thin content is the density check's job.
    3. The analyzer could only see SPA markers via `rendered.heuristic_signals`, so in `--html-file`
       mode it was **blind to SPA shells entirely**. It now detects them from raw HTML itself,
       which is also correct for self-contained skills.
  Also rebalanced an adversarial test that was accidentally measuring page length rather than
  injection resistance.
- 2026-09-06 — Pre-Phase-5 audit: synced this file (commit/tracking state was stale) and fixed 4 issues.
  **SPA markers were duplicated** between `scoring-config.json` and hardcoded constants in the crawl
  analyzer — the third instance of the same drift-hazard family in this project. Markers are now read
  from config, with the embedded fallback pinned by a test. Added the drift tests that make this
  file's "drift is caught by tests" claim actually true, registered `check-result-schema.json` in
  PLAN §12, and removed two dead `CATEGORY` constants.
  **Found and repaired real source corruption**: an earlier heredoc edit interpreted `` and ``
  as escape sequences and wrote raw 0x08/0x01 control bytes into a regex, silently disabling
  empty-mount detection (masked because the framework-marker path still matched). Scanned all 28
  source files — only that one was affected. Added `test_no_control_characters_in_source` so it
  cannot recur, plus a test proving the `` backreference still distinguishes an empty mount node
  from a populated one.
- 2026-09-06 — **Phase 5 complete.** Both parity analyzers built; all 24 checks now implemented.
  **Two real defects found by running the analyzers, both in the value-proposition check:**
    1. FALSE POSITIVE — the SPA shell scored a signal because "enable JavaScript to run this app"
       matched the offering-noun "app". Added a minimum-text floor.
    2. **GAMEABILITY** — the injection test proved that simply appending a paragraph flipped the
       verdict fail -> pass, because the check scored keywords anywhere above the fold. That
       violates PLAN §6.2 ("adding filler text changes nothing"). Rewritten to score only the
       PROMINENT area (topmost heading + the paragraph after it); a page with no headline now fails
       regardless of how much text is added.
  Also corrected the healthy fixture, which was missing `meta viewport` and `meta description` and
  so was not actually healthy.
  **Two test-design errors of my own**: the injection tests compared pages whose content genuinely
  differed, so they measured content change rather than injection resistance. Reformulated to assert
  the property that matters — injected instructions must never *improve* a verdict.
  A heredoc again wrote literal null bytes into a source file (second occurrence); caught by the
  Phase 4 guard test. Heredocs are no longer used for content containing escapes.
