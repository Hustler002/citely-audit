# MASTER PLAN v3 — Citely · Brand AI-Readiness Audit (rubric-aligned)

## Context

v1 fixed correctness/spec/security gaps and scaffolded the repo (no logic yet). v2 red-teamed it and added
a capability scoring model plus security hardening. **v3 exists because the actual judging rubric arrived**,
and it invalidates part of v2's investment.

The rubric scores six things: **detection accuracy, suggested-action quality, output design, skill-format
hygiene, marketplace composition, generalization.** It contains **no criterion for demo polish, Adobe
relevance, innovation, or differentiation.** v2 spent heavily on exactly those. v3 re-weights.

**Three corrections the rubric forces:**
1. **Cut skill padding.** "*Not padding; a single well-built skill scores fully here too*" means adding
   skills to look like a marketplace is actively penalized. v2 added two skills partly for narrative → one
   is cut (§5.2).
2. **Engagement is 50% of detection accuracy.** The rubric says "across **both** discoverability and
   engagement." v2 treated engagement as the last, lightest analyzer. Now at parity (§6.4).
3. **Generalization is tested by construction on unseen sites.** This exposed a real defect in v2: the
   engagement heuristics were English-only and would false-positive on every non-English site (§8).

**Locked decisions retained:** recommend-only (no writes, no re-audit) · JSON report (self-contained HTML
deferred as unscored — see §9 and §14.8) ·
homepage + sitemap-aware sample · subprocess+JSON contract · tiered rendering · strict 3-tier severity ·
`verified`/`heuristic` confidence · stdout-JSON-only · never-crash · agentskills.io compliance.

### Repository (verified current state)

- **Path:** `C:\Users\DEVANSH\OneDrive\Desktop\ADOBE_HACK\citely-audit` — this directory is itself the
  **git root**. Development moved here from `C:\Users\PRIYANSHU PAL\Desktop\ADOBE\citely-audit` on
  2026-09-10; that path is historical. **Paths in this document are not portable** — check any absolute
  path against the machine you are on before using it. Live environment setup is recorded in
  `CLAUDE.md` under *Local environment*, which is authoritative for it.
- **Branch `feature/devansh-singh`**, at `origin/main` (`437104b`) after the phase 1-6 merge; `origin` on
  GitHub. Local `main` is deliberately behind and unused. `.gitignore` present.
- **Project renamed to “Citely.”** Scope locked with the user: rebrand the *identity* fields; **keep the six
  skill folder names unchanged** (`audit-orchestrator`, `crawl-render-extraction-audit`, …) because they are
  descriptive and agentskills.io-valid, and the rubric rewards clear separation of concerns over branding.
  Human-readable title: **“Citely — Brand AI-Readiness Audit”**; schema title “Citely Audit Report”.

### Document roles (locked)

| File | Role | Authority |
|---|---|---|
| `CONTEXT.md` | Original problem statement + first draft design | **Historical only**, preserved verbatim; superseded |
| `PLAN.md` (this file) | MASTER PLAN v3 — final implementation plan | **Authoritative for all design** |
| `CLAUDE.md` | Running progress record | Authoritative for *status*, never for *design* |
| `README.md` | Usage, setup, security posture | User-facing |

`CONTEXT.md` keeps `brand-ai-readiness-audit/` in its §2.1 tree **by design** — it is the record of what was
originally asked. It carries a header marking it historical and pointing at `PLAN.md`.

---

## 1. Rubric → design mapping (the spine of this plan)

| Rubric criterion | What it demands | Where this plan delivers it | Risk if ignored |
|---|---|---|---|
| **Detection accuracy** | Real, evidence-backed problems across **both** discoverability **and** engagement; **few misses AND few false positives** | §6 precision/recall strategy; dependency suppression; engagement at parity | The single highest-weight failure mode |
| **Suggested-action quality** | Correctly targeted, mechanism-sound, prioritized; **beyond-problem suggestions relevant and non-obvious** | §7 remediation model; proactive recommendations promoted to MUST BUILD | v2 had proactive suggestions as *stretch* — would have forfeited half this criterion |
| **Output design** | Structured, actionable report **a non-expert could act on**, from the entrypoint skill | §9 non-expert output contract (plain language, no jargon) | v2's report was engineer-facing (`text_to_node_ratio`, `credit`) |
| **Skill-format & engineering hygiene** | agentskills.io compliant; well-formed manifest, exactly one entrypoint; deterministic; safe | §4 compliance gates; §10 security; already strong from v1 | Cheap to pass, fatal to fail |
| **Marketplace composition** | Genuine separation of concerns, clean composition; **not padding** | §5.2 — 7 skills cut to 6, each justified or removed | v2's `report-renderer` skill was padding |
| **Generalization** | Works on **unseen** sites; no examples given | §8 generalization hardening (i18n, CMS variety, conservative thresholds) | Overfitting to my own fixtures |

**Not in the rubric — deliberately demoted:** demo theatrics, Adobe positioning, competitive
differentiation, judge Q&A. Kept only where free (a README paragraph, §13). No engineering time.

---

## 2. Red-team of v2 against the real rubric

**P0 — Skill padding is now a scored liability.** v2 added `report-renderer` and `remediation-advisor`
partly to strengthen a "marketplace story" the rubric doesn't reward. `report-renderer` is presentation —
the rubric already says the *entrypoint* emits the report. **Cut it** into the orchestrator.

**P0 — Engagement under-invested.** v2's implementation order put engagement 8th, with the thinnest checks.
The rubric weights it equally with discoverability inside detection accuracy. **Rebalance.**

**P0 — Proactive suggestions were "stretch."** The rubric explicitly rewards "beyond-problem suggestions…
relevant and non-obvious." They move to MUST BUILD, with a quality bar: *non-obvious* means "add JSON-LD"
does not count.

**P0 — English-only heuristics break generalization.** `value_prop_signal_verbs: ["build","create",…]`,
`vague_marketing_tokens`, and sentence-splitting are all English-assuming. On a German or Japanese site
every engagement and quotability check would fail → mass false positives on exactly the "unseen sites" the
rubric tests. **This is the most likely real-world failure of the current design.**

**P1 — Report is engineer-facing.** Fields like `credit`, `weight`, `text_to_node_ratio` fail "a non-expert
could act on." Needs a plain-language layer that keeps the technical detail available but not primary.

**P1 — Threshold overfitting.** Tuning thresholds against my own 8 fixtures then being judged on unseen
sites is textbook overfitting. Mitigation: prefer *structural* checks (does a JSON-LD `Organization` exist?)
over *statistical* ones (is the text/node ratio > 1.0?), and make statistical thresholds deliberately
conservative — biased toward `unknown` over a confident wrong answer.

**P1 — `unknown` can become a "miss."** Dependency suppression cuts false positives, but the rubric also
penalizes misses. Suppression must apply only to *genuinely* unmeasurable checks, never as a way to dodge a
hard call. Rule: suppression requires a *verified* failed prerequisite (§6.3).

**P2 — Multi-page raises generalization risk.** Sitemap parsing on unseen sites hits index files, huge
sitemaps, gzipped sitemaps, and 404s. Must degrade to homepage-only silently rather than error.

---

## 3. Architecture (unchanged core, corrected composition)

```
Input → Safe Fetch → Render → Normalized Artifact → Analysis → Findings → Scoring → Recommendations → Report
        (orchestrator: the ONLY network I/O)      │   (pure functions of the artifact)              │
                                                  └── single cross-boundary contract ───────────────┘
```

**Invariant:** everything left of *Normalized Artifact* is the only code touching the network; everything
right is a pure function of it — which is what makes runs replayable, offline-testable, and deterministic.

> **Resolves v1's network contradiction.** v1 let `entity-corroboration-audit` call Wikidata, breaking this
> invariant. The optional lookup **moves left of the boundary** into the orchestrator's fetch stage and is
> stored in the artifact as `external_corroboration`. Analyzers now have **zero** network access, no
> exceptions. If disabled or failed, the field is absent and dependent checks resolve to `unknown`.

---

## 4. Skill-format & hygiene gates (rubric criterion 4)

Cheap to satisfy, so treat as non-negotiable pre-submission gates:
- Every folder passes `skills-ref validate`; `name` matches its directory, lowercase alnum + single hyphens.
- `description` states **what + when** (the discovery signal), ≤1024 chars.
- `compatibility` declared where env matters (Playwright, network).
- Body < 500 lines / < 5000 tokens; detail pushed to `references/`.
- `marketplace.json` well-formed, **exactly one** `entrypoint: true`, with a README note that
  `marketplace.json`/`entrypoint` are this brief's bespoke convention (not base agentskills.io).
- Determinism: identical artifact ⇒ byte-identical report (asserted in tests).
- Safety: read-only, robots-respecting, no auth, no writes (asserted in E2E).

---

## 5. Marketplace composition (rubric criterion 5)

### 5.1 The padding test
Each skill must pass all three: **(a)** distinct concern and distinct method, **(b)** independently
runnable and useful, **(c)** its removal would force unrelated logic into another skill. Anything failing
these is padding and gets folded in.

### 5.2 Final composition — 6 skills

| Skill | Distinct concern | Distinct method | Verdict |
|---|---|---|---|
| `audit-orchestrator` **(entrypoint)** | Acquisition, composition, scoring, report emission | Network I/O + subprocess fan-out + pure scoring | **Keep** (required) |
| `crawl-render-extraction-audit` | Mechanic 1 — can a machine access and parse it? | Raw-vs-rendered DOM diff | **Keep** |
| `quotability-density-audit` | Mechanics 2+3 — is content citable and survivable? | Text/sentence analysis | **Keep** |
| `entity-corroboration-audit` | Mechanic 4 — is identity unambiguous? | Structured-data graph analysis | **Keep** |
| `engagement-orientation-audit` | Mechanic 5 — can a human orient? | Viewport/DOM-position analysis | **Keep** |
| `remediation-advisor` | **Prescription**, not detection — consumes *verdicts that already exist* | Template synthesis + ROI ranking | **Keep** — passes all three tests; the rubric scores suggested-action quality independently |
| ~~`report-renderer`~~ | Presentation of a report the entrypoint already owns | HTML templating | **CUT — padding.** Folded into `audit-orchestrator/scripts/render_html.py` |

The four analyzers map 1:1 onto the brief's five named failure mechanics (2+3 share a text-analysis method,
so they share a skill) — a decomposition justified by the *problem domain*, not by wanting more folders.

> **Correction (2026-09-11, Phase 7).** This row previously read "consumes *findings*, not the artifact",
> and §7 makes that impossible: snippets must be filled from values **observed on the page**, and every
> proactive detector inspects the page. The advisor therefore reads the crawl artifact as read-only
> evidence, alongside the findings and the resolved check states. The distinction that justifies the
> skill is **concern**, not inputs — it emits no check state, runs after scoring, and cannot move the
> score. That last property is asserted end-to-end by
> `test_remediation_advisor.py::test_the_advisor_cannot_change_the_score`, rather than asserted here.

---

## 6. Detection accuracy (rubric criterion 1 — highest weight)

### 6.1 Checks, not findings, are the unit

Each check declared in `config/checks.json`: `id`, `category`, `weight`, `severity`, `confidence_class`,
`depends_on`, `threshold`, `requires_language`. Each resolves to one state:

| State | Meaning | Credit | In denominator? |
|---|---|---|---|
| `pass` | Signal present, above threshold | 1.0 | yes |
| `partial` | Present but below threshold | 0.5 | yes |
| `fail` | Measured, absent/failing | 0.0 | yes |
| `not_applicable` | Legitimately doesn't apply | — | **no** |
| `unknown` | Could not be measured | — | **no** |

### 6.2 Scoring

```
category_score = 100 × Σ(weight × credit) / Σ(weight)      over applicable checks only
overall_score  = Σ(category_weight × category_score) / Σ(category_weight)
```

Categories: **AI Discoverability** · **AI Comprehension** · **Entity Trust** · **Human Orientation**.

Why this replaces v1's `100 − Σ penalty`: bounded and comparable (a weighted mean can't run away);
no double counting (§6.3); missing ≠ negative (unmeasurable checks leave the denominator instead of being
punished); confidence surfaced honestly via `score_confidence` rather than silently shrinking numbers;
hard to game because states depend on structural facts, so adding filler text changes nothing.

> **Field naming (no breaking change).** v1 locked `summary.discoverability_score` and it exists in the
> schema — **retained as the overall score**. Category scores are added as `summary.category_scores{…}`,
> plus `summary.score_confidence`.

> **`summary.coverage` — added during Phase 1 implementation.** Building the sample report exposed a
> weakness in the model as specified: on a client-rendered shell, suppression silences ~19 of 24 checks, so
> the 5 survivors score **85 with confidence 1.0** on a site that is effectively invisible to AI.
> `score_confidence` cannot reveal this — it measures the *verified share of what was scored*, and the
> survivors were all verified. **`coverage`** (share of total check weight actually scored) is the missing
> signal: it reads **0.23** for that same scan. Both are required and they are independent — confidence
> answers "how trustworthy are the scored checks?", coverage answers "how much of the audit ran at all?".
> Any headline score must be presented alongside coverage (§9 layer 1).

### 6.3 Precision/recall strategy — the core of this criterion

The rubric penalizes **both** misses and false positives, which pull in opposite directions. The resolution
is to make each check's *failure mode* explicit rather than applying one global bias:

- **Structural checks** (does a JSON-LD `Organization` exist? is there an `<h1>`? does robots block us?) —
  binary, language-independent, near-zero FP risk. **Bias toward calling them.** These carry most of the weight.
- **Statistical checks** (text density, quotable-sentence count) — threshold-dependent and the main FP
  source. **Bias toward `partial`/`unknown`** near the boundary; only call `fail` well past the threshold.
- **Dependency suppression** (the double-count and FP fix): if a prerequisite fails, dependents become
  `unknown`, not `fail`. *Example:* `content.reachable` fails (consent wall / bot challenge / CSR shell) →
  every quotability, entity, and orientation check becomes `unknown`. Result: **one** critical finding about
  the root cause instead of ~12 downstream false positives.
- **Anti-dodge rule** (prevents suppression becoming a *miss*): suppression only applies when the failed
  prerequisite is itself `confidence: verified`. A merely-heuristic prerequisite failure may not silence
  dependents. This keeps recall honest.
- **Blocked-page detection is mandatory**, not optional: consent walls, bot challenges, and CAPTCHAs are
  detected explicitly and set `partial: true` with `partial_reason: "blocked"`. Reporting "your content is
  bad" for a consent wall is the worst false positive available and it *will* happen on unseen sites.

### 6.3.1 robots.txt — two distinct concerns (decided 2026-09-05)

v3 originally said only "robots-respecting", which was too thin to implement. Robots involves **two
separate questions that must not be conflated**:

1. **Operational gate — may *we* fetch?** Evaluated for `CitelyAuditBot`. If disallowed we **do not fetch
   at all**: emit one critical finding, set `partial: true` + `partial_reason: "blocked"` and page
   `blocked_kind: "robots_disallowed"`, and resolve every check to `unknown` with reason
   `blocked_before_fetch` so the whole scan attributes to one root cause. **Never scored** — nobody
   blocklists an unknown auditor UA, so scoring it would pass on every site and measure nothing.
2. **Scored signal — may the *AI assistants* fetch?** Check `access.ai_crawlers_allowed` evaluates
   robots for `GPTBot`, `OAI-SearchBot`, `ChatGPT-User`, `ClaudeBot`, `Claude-Web`, `PerplexityBot`,
   `Google-Extended`, `CCBot`, `Applebot-Extended` (tokens in `scoring-config.json → robots.ai_crawlers`,
   updatable without code changes).

**Why this matters more than it appears.** A site can allow us and be fully crawlable by Google while
blocking `GPTBot` — invisible to ChatGPT, yet scoring "robots OK" under the naive reading. That is a false
negative on the most direct AI-discoverability signal there is, and the rubric penalises misses. Blocking
AI crawlers is common and frequently a CDN or platform default the owner never consciously chose.

This check has **no dependents by design**: an AI-crawler block does not stop *us* reading the page, so it
must not suppress content checks.

### 6.4 Engagement at parity (rubric: "**both** discoverability and engagement")

Engagement gets equal design attention and a comparable check count, using **language-independent
structural signals** wherever possible:
- Heading present in first viewport; heading depth/order sanity.
- Primary CTA above the fold — detected structurally (`<a>`/`<button>` in the first viewport, prominence
  by DOM position), not by matching English words.
- Text-to-chrome ratio above the fold; content pushed below fold by interstitials.
- Legibility proxies: computed font size, line length, contrast where styles are available.
- Interstitial/overlay obstruction of first-paint content.
- `meta viewport` presence / mobile-readiness signals.

Language-dependent value-proposition wording is checked **only** when language is confidently detected and
supported (§8); otherwise `unknown`, never `fail`.

### 6.5 Every finding carries its reasoning chain

`signal` → `measurement` (observed value) → `threshold` (config value + comparison) → `evidence` (verbatim,
sanitized, truncated, with `selector` + `page_url`) → `impact` → `remediation`. This is what makes a finding
"evidence-backed" per the rubric, and it makes any wrong call auditable rather than mysterious.

---

## 7. Suggested-action quality (rubric criterion 2)

`remediation-advisor` consumes findings and emits two classes, both deterministic and template-driven.

**Corrective** — bound to a failing check. Each carries: the exact target (`selector` / file location
hint), a **copy-paste snippet**, a **validation procedure** ("paste into Rich Results Test; expect
Organization detected"), and the mechanism explanation.

**Proactive** (rubric: "beyond-problem suggestions… relevant and **non-obvious**") — **MUST BUILD, not
stretch.** Quality bar: a suggestion that any generic SEO checklist would give is *obvious* and doesn't
count. Non-obvious examples grounded in this project's mechanics:
- Page has an FAQ in prose but no `FAQPage` schema → the content is already there and is one block from
  being directly quotable by an answer engine.
- ~~`Organization` exists with `sameAs`, but no `sameAs` points at an *authority* node~~ — **NOT BUILT,
  deliberately (2026-09-11).** Between this plan and Phase 7 that became the scored check
  `entity.sameas_authority`, which already fails or partials exactly this shape and emits a finding with
  its own remediation. Building it here as well would report one root cause twice, which is the
  double-jeopardy defect fixed on 2026-09-10. A version pitched *above* the check — "you have a
  directory anchor, now get an encyclopedic one" — was considered and rejected: the project already
  locked the finding that a local business will never have a Wikidata entry, so it would hand bad
  advice to precisely the small businesses that scored best in the entity corpus. Pinned by
  `test_the_authority_anchor_example_was_not_built_as_a_detector` so a future reader working from this
  list does not helpfully re-add it..
- Key differentiating facts appear only in prose, not in a definition list/table → summarizers drop them
  first when condensing.
- Facts stated without dates/attribution → RAG systems discount unsourced claims even when extractable.
- Numeric claims rendered in images with correct `alt` → passes accessibility, still invisible as *facts*.

**Prioritization = points recoverable.** `points_recoverable = category_weight × (weight/Σweights) ×
(1 − credit)`, so each action reads "+18.4 → Entity Trust." Ranks fixes by ROI rather than opinion.

**Hard rule — never invent business facts.** Snippets are filled *only* from values observed on the page;
anything unobserved stays an explicit placeholder (`{{LEGAL_ENTITY_NAME}}`) with a note. No LLM generates
factual claims.

> **Schema-invariant resolution.** Proactive items are **not** findings and never enter `findings[]` — the
> mandated floor requires `total_findings == critical + high + medium`. They live in a separate additive
> top-level `recommendations[]`, excluded from counts and from the score.

---

## 8. Generalization (rubric criterion 6) — "tested by construction"

Judges will run this on sites nobody has seen. Design rules:

**Internationalization (the v2 defect).** Detect language from `<html lang>`, then content heuristics.
Every language-dependent check declares `requires_language`. If the page language is undetected or
unsupported, those checks return **`unknown`, not `fail`**, and the report states that language-dependent
analysis was skipped. Language-independent structural checks still run and still score. *Without this, every
non-English site produces a wall of false positives.*

**No site-specific assumptions.** No hardcoded domains, CMS names, or framework allowlists beyond the
documented SPA marker list (used only as positive evidence, never as a requirement). No assumption of a
sitemap, of `/about`, or of any URL convention — all are opportunistic.

**Conservative thresholds over fitted ones.** Structural checks carry the weight; statistical thresholds are
set at the *clearly wrong* boundary, not the median of my fixtures. Prefer `unknown` to a confident wrong call.

> **The word-list rule, restated after a third breach (2026-09-11).** "No hardcoded allowlists …
> used only as positive evidence, never as a requirement" was written for SPA markers, breached by
> the entity type allowlist (fixed 2026-09-10), and breached again by
> `orientation.value_proposition`, which REQUIRED a word from an 18-noun list. It failed "Emergency
> lock repair across Leeds" at high severity while passing example.com, which offers nothing.
> The general form: where a list must exist, put it on the **subtracting** side. A phrase missing
> from a vague-marker list can never cause a failure; a category missing from a required list
> causes one on every unseen trade.

**Real-world messiness.** Non-HTML responses, mixed/incorrect encodings, huge pages, gzipped/index/404
sitemaps (degrade silently to homepage-only), infinite redirects, slow origins, geo/consent interstitials,
JS-heavy and legacy-HTML sites alike.

**Benchmark archetypes** double as the generalization suite: static/SSR · SPA shell · e-commerce PDP ·
corporate/about-heavy · image-heavy · unstructured div-soup · **non-English** · consent-wall/bot-challenge ·
adversarial. Expected score *ranges* asserted per archetype — proving sensible behavior, not memorized numbers.

---

## 9. Output design (rubric criterion 3) — "a non-expert could act on"

The entrypoint emits one report readable at three depths, so a non-expert isn't forced through engineer
detail. **All three live in the emitted JSON** (decided 2026-09-11 from the brief, which grades the
marketplace "not any single report it happens to produce" and calls its schema "a floor, not a
ceiling"), so there is one wording rather than two that can disagree:

1. **Plain-language verdict** — one sentence per category, no jargon. *"AI assistants can reach your site
   but can't read your product facts, because the page builds itself in the browser."*
2. **Prioritized actions** — ranked by points recoverable; each states what to do, where, the copy-paste
   snippet, and how to confirm it worked.
3. **Technical evidence** — measurement, threshold, selector, confidence. Present but never the headline.

Rules: no undefined jargon in layers 1–2 (`text_to_node_ratio` belongs in layer 3); every finding names a
concrete next step; severity in words plus color; the HTML report (`--html-out`, never stdout) presents the
same three layers, is fully self-contained, escapes all page-derived text, and works offline.

### 9.1 Low coverage must suppress the headline, not just sit beside it (added 2026-09-10)

**The problem, measured.** `tests/fixtures/spa_hydrating.html` scores **80/100 at `coverage` 0.175**,
with Human Orientation at **100.0 off `category_coverage` 0.1** — one surviving check. Scoring is
correct: a page that renders nothing without JavaScript leaves 82.5% of the check weight unmeasurable,
and the capability model deliberately keeps `unknown` out of the denominator rather than guessing. The
output is what fails. A page no AI assistant can read leads with an 80, and the number that disproves it
is rendered as a peer. That the deader `broken_page.html` scores 30 on the identical coverage is the
same point from the other side: at this coverage the headline is arbitrary.

**Why the fix does not go in `_scoring.py`.** Damping the score for low coverage would move a presentation
concern into the scoring spine, which is the one component whose determinism, auditability and
injection-resistance the entire design rests on (§6, §10). The score is a faithful statement about what
was measurable; the renderer is what must stop it being read as a statement about the site.

**Requirement on the Phase 8 output layer.** Below a coverage floor, layers 1 and 2 lead with what could
not be measured and why, and no score — overall or per category — is permitted to render unqualified.
The existing rule *never show a score without coverage* becomes something the renderer **enforces**
rather than something the reader is trusted to honour.

**Acceptance criterion.** The rendered output for `spa_hydrating.html` must lead with the fact that the
page could not be read, and neither `80` nor `100.0` may stand as a bare headline anywhere in it.
Asserted as a test, in the same spirit as §8's archetype ranges: a behaviour, not a memorised number.

This supersedes the residual half of open issue #10 in `CLAUDE.md`. The first half — a category scoring
100 off one check being *invisible* — was closed in Phase 6 by `summary.category_coverage`.

> **✅ Delivered 2026-09-11, and the location changed.** §9.1 is implemented as **report fields**, not
> as renderer behaviour: `summary.headline_reliable`, `summary.headline_caveat`,
> `summary.category_verdicts` and `not_checked[]`. The floor is `_narrative.COVERAGE_FLOOR = 0.5`.
> Putting it in the data means a CI job piping the JSON is protected exactly as a reader is, and the
> rule is testable without parsing markup. `spa_hydrating.html` now reports `headline_reliable:
> false` with Human Orientation saying it could not be assessed despite scoring 100.0 — while both
> numbers remain in `summary`, because declining to headline a measurement is not hiding it.

---

## 10. Security architecture

**Threat model: every fetched byte is hostile, and the report is read by an LLM agent.**

**Fetch/SSRF** — scheme allowlist; reject URL userinfo; resolve host and validate **all** A/AAAA records
against private/loopback/link-local/reserved/multicast/ULA/IPv4-mapped-IPv6 and `169.254.169.254`; **pin the
validated IP for the connection** (defeats DNS rebinding/TOCTOU) with SNI+Host preserved; re-validate at
**every** redirect hop; redirect cap + loop detection; port allowlist; connect/read timeouts; TLS failures
recorded, never downgraded.

**Resource exhaustion** — max response bytes; incremental **decompression-bomb** guard; DOM node/depth caps;
render timeout with hard browser kill; global monotonic deadline.

**Parser abuse** — `lxml` with entity resolution off, no network entity fetch, XXE/XInclude disabled
(blocks billion-laughs/XXE); JSON-LD size/depth capped before traversal.

**ReDoS** — no regex ever built from page content; static, anchored patterns; inputs length-capped before
matching; string/DOM ops preferred. Adversarial fixtures assert bounded runtime.

**Prompt injection** — decisive property is architectural: **no LLM in the fetch, analysis, or scoring
path**, so injected text cannot alter a score. Page-derived strings reach the report only sanitized,
truncated, and delimited as untrusted; each `SKILL.md` tells the agent `evidence` is data, never
instructions. HTML report escapes everything and never `innerHTML`s crawled content.

**Malicious structured data** — JSON-LD treated as claims, not commands; `sameAs` never auto-fetched unless
`--allow-external`, then only via a Wikidata domain allowlist through the same SSRF guard.

**Subprocess isolation** — `argv` lists only, never `shell=True`; minimal env allowlist (no inherited
secrets); fixed `cwd`; per-child timeout+kill; stdout size cap; failure or malformed JSON degrades that
analyzer's checks to `unknown`, never crashes the run.

**Temp files & secrets** — `mkdtemp` 0700, unpredictable names, removed in `finally`; URL credentials and
`Authorization`/`Cookie` headers redacted from logs and report; no bodies in logs by default.

**Dependencies** — pinned with hashes; `pip-audit` in CI; Playwright pinned, provisioned as a setup step.

---

## 11. Testing strategy

| Layer | Asserts |
|---|---|
| **Detection quality** | **Labeled corpus**: each archetype fixture has expected findings; suite reports **precision/recall per check** and fails on regression. This is the rubric's top criterion — measure it, don't assume it. |
| **Generalization** | Non-English fixture produces zero language-dependent `fail`s; malformed sitemap degrades to homepage-only; consent wall yields one blocked finding, not a wall of false positives. |
| **Unit** | Per-check state transitions at, above, below each threshold. |
| **Security** | SSRF corpus (private v4/v6, IPv4-mapped, metadata IP, rebinding, redirect-to-internal, userinfo, bad ports); decompression bomb; billion-laughs/XXE; ReDoS runtime bounds; subprocess timeout/crash/garbage-stdout; secret redaction. |
| **Adversarial** | Prompt-injection fixtures — report byte-identical to clean run except sanitized evidence; scoring untouched. |
| **Scoring** | Determinism (same artifact ⇒ byte-identical report); suppression collapses correlated findings; `unknown` leaves the denominator; bounds; monotonicity. |
| **Schema** | Report + artifact validate; mandated floor never violated; `total == critical+high+medium`. |
| **E2E / perf** | Full run vs. fixture server, online and Tier-B paths; budget headroom; deadline sets `partial`; read-only assertion (no writes to target). |

---

## 12. Repository structure

```
citely-audit/                     # git root — branch feature/devansh-singh, origin on GitHub
├── marketplace.json              # name: "citely-audit"; 6 skills, exactly one entrypoint:true
├── README.md · CLAUDE.md · CONTEXT.md · pyproject.toml   # pyproject name: "citely-audit"
├── config/
│   ├── checks.json               # NEW: registry — id, category, weight, severity,
│   │                             #      confidence_class, depends_on, threshold, requires_language
│   └── scoring-config.json       # category weights, credits, budgets, UA, limits
├── skills/
│   ├── audit-orchestrator/       # entrypoint
│   │   ├── scripts/{run_audit,_safe_fetch,_render,_page_select,_scoring,render_html}.py
│   │   └── references/{report-schema,crawl-artifact-schema,check-result-schema,checks-reference,severity-rubric,sample-report}.*
│   │       # check-result-schema.json = the THIRD cross-process contract (advice-schema.json is the fourth, Phase 7): what analyzers print
│   │       # (check states, not findings — §6.1). Added in Phase 4.
│   ├── crawl-render-extraction-audit/   ├── quotability-density-audit/
│   ├── entity-corroboration-audit/      ├── engagement-orientation-audit/
│   └── remediation-advisor/      # scripts/advise.py + references/advice-schema.json
│                                 #   + references/remediation-templates/{corrective,proactive}.json
└── tests/
    ├── fixtures/{static,spa,ecommerce,corporate,image-heavy,unstructured,non-english,consent-wall,adversarial}/
    ├── fixture_server.py         # + robots.txt, sitemap.xml, redirect, bomb routes
    ├── labeled_corpus.json       # NEW: expected findings per fixture → precision/recall
    └── test_{detection,generalization,security,scoring,<skill>,orchestrator_e2e}.py
```

Changes from v2: `report-renderer` skill removed (folded to `render_html.py`); `non-english` fixture added;
`labeled_corpus.json` added; root renamed `brand-ai-readiness-audit/` → `citely-audit/` with identity fields
rebranded (skill folder names deliberately unchanged).

---

## 13. Positioning (unscored — README only, zero engineering time)

Adobe already ships **LLM Optimizer / Brand Visibility** (GEO for marketers, off-site mention measurement).
One README paragraph frames this as the developer-side complement: *they measure whether the brand is
mentioned; this diagnoses whether the page is machine-readable and hands over the fix.* Adobe's CX
Enterprise emphasis on "reliable and **auditable** agentic workflows" aligns with our no-LLM-in-scoring
design. **No engineering effort is spent here** — it is not in the rubric.

---

## 14. Implementation order (rubric-weighted)

0. ~~**Rename cleanup.**~~ ✅ **DONE (2026-09-04).** `marketplace.json.name` and `pyproject.toml` `name` →
   `citely-audit`; both schema `$id`s → `https://citely-audit/…`; `report-schema.json` title → "Citely Audit
   Report"; `SKILL.md` `metadata.project` → `citely-audit`; `README.md` H1 → "Citely — Brand AI-Readiness
   Audit"; `CONTEXT.md` marked historical (text preserved verbatim); `CLAUDE.md` re-synced to v3 with stale
   paths fixed. Skill folder names unchanged, as locked.
1. ~~`config/checks.json` + `_scoring.py` (pure, no I/O) + determinism/suppression tests — the spine.~~
   ✅ **PHASE 1 DONE (2026-09-04).** 24 checks (6 per category, exact parity); capability scoring with
   dependency suppression + anti-dodge rule; i18n gating; `coverage` added; both schemas extended to v3;
   sample report regenerated and numerically verified. 69 tests passing.
2. `_safe_fetch.py` + security test corpus.
3. Artifact assembly, `_page_select.py` (sitemap, degrade-to-homepage), `_render.py` (Tier A/B); fixture server.
4. `crawl-render-extraction-audit` + `entity-corroboration-audit` (highest-confidence structural checks).
5. **`engagement-orientation-audit` + `quotability-density-audit` at parity** — with i18n gating built in
   from the start, not retrofitted.
6. Orchestrator wiring → schema-valid JSON report.
7. ~~**`remediation-advisor`** incl. proactive suggestions (rubric criterion 2 — not deferrable).~~
   ✅ **PHASE 7 DONE (2026-09-11).** Sixth skill built: corrective snippets filled only from observed
   values, a validation procedure on every one of the 24 checks, and four proactive detectors. The
   fifth §7 example was not built — it had become a scored check; see §7. Advisor is score-neutral
   by construction and its failure costs snippets, never the report.
8. ~~**Non-expert output layer** + `render_html.py` (rubric criterion 3), **including §9.1**.~~
   ✅ **PHASE 8 DONE (2026-09-11)** — the output layer shipped **inside the report**, not as a
   renderer. `render_html.py` is **deferred as unscored**: the brief grades "the marketplace itself …
   not any single report it happens to produce", requires "a single audit report (fixed schema)", and
   never mentions HTML. The §9.1 acceptance criterion is met and asserted.
9. ~~`labeled_corpus.json` + precision/recall harness; archetype fixtures.~~
   ✅ **PHASE 9 DONE (2026-09-11)** — 10 labelled fixtures, recall and precision both 100% with
   16 must-find and 98 must-not-find labels, all 10 scores in band. Labels are written from each
   fixture's construction, never recorded from a run. Consent-wall stays in the fixture server:
   blocked-page detection happens during acquisition, and the corpus audits local files.
   It earned its keep immediately by exposing the `orientation.value_proposition` word-list
   gate, which failed real value propositions while passing example.com — see §8.
10. `skills-ref validate` all 6; README/CLAUDE.md; final determinism + read-only sign-off.

---

## 15. Consistency pass — contradictions resolved

| # | Contradiction | Resolution |
|---|---|---|
| 1 | "Analyzers are network-free" vs. entity skill's Wikidata call (v1) | Lookup moved left of the artifact boundary into the orchestrator; stored as `external_corroboration`. Zero exceptions. (§3) |
| 2 | Proactive suggestions vs. `total_findings == critical+high+medium` | Proactive items are not findings; separate additive `recommendations[]`, excluded from counts and score. (§7) |
| 3 | New category scores vs. v1-locked `summary.discoverability_score` | Retained as the overall score; category scores added alongside. No breaking change. (§6.2) |
| 4 | 5-page scope + `Crawl-delay` vs. 270s deadline | Page count is a budget-derived **maximum**, not a guarantee. Skipped pages' checks are `unknown` (excluded), so a short scan lowers confidence, never the score. `partial_reason: "budget"`. |
| 5 | Two sources of "partial" (deadline vs. blocked) | Unified: `partial` true if any stage was skipped/unmeasurable; `partial_reason` is an enum (`budget`, `blocked`, `render_failed`, `analyzer_failed`). |
| 6 | Per-finding `confidence` vs. per-check `confidence_class` | Same enum, two scopes: check declares `confidence_class`; emitted finding inherits it as `confidence`. `score_confidence` is a ratio, not a third enum value. |
| 7 | **New in v3:** FP-reduction (`unknown`) vs. rubric penalty for misses | Anti-dodge rule — suppression requires a **verified** failed prerequisite (§6.3). |
| 8 | **New in v3:** marketplace-composition narrative vs. anti-padding criterion | `report-renderer` cut; every remaining skill passes the three-part padding test (§5.1). |

**Schema changes** (all additive; mandated floor and existing `required` lists unchanged):
`summary.category_scores`, `summary.score_confidence`; top-level `recommendations[]`; `findings[].page_url`,
`.check_id`, `.measurement`, `.threshold`, `.selector`, `.impact`, `.plain_summary`;
`suggested_action.validation`; `partial_reason` → enum; `diagnostics.pages[]`, `.language_detected`.
`crawl-artifact-schema.json` gains `pages[]` and `external_corroboration`. `checks.json` is new.

**Confirmed non-contradictions:** recommend-only holds throughout (no writes, no re-audit) · tiered
rendering disclosed via `diagnostics.render_mode` · stdout-JSON-only preserved (HTML via `--html-out`) ·
analyzer failure → `unknown` consistent with subprocess isolation · strict 3-tier severity intact.

---

## MUST BUILD — directly scored by the rubric
Capability scoring + dependency suppression + anti-dodge rule (§6) · **engagement checks at parity with
discoverability** (§6.4) · **i18n gating so non-English sites don't false-positive** (§8) · blocked-page
detection · structural-first check set · `remediation-advisor` **including non-obvious proactive
suggestions** (§7) · points-recoverable prioritization · **non-expert output layer** (§9) · schema-valid
JSON report + agentskills.io compliance for all 6 skills · SSRF/resource/parser guards (§10) ·
`labeled_corpus.json` precision/recall harness · non-English + consent-wall + adversarial fixtures.

## HIGH ROI
Sitemap-aware multi-page with silent degradation · `meta robots`/`X-Robots-Tag` detection · self-contained
HTML report · full 9-archetype benchmark · `checks-reference.md` documenting every check · CI exit code.

## NICE TO HAVE
`--allow-external` Wikidata corroboration · per-page HTML drill-down · shadow-DOM/iframe traversal ·
`projected_score` · sample GitHub Action.

## DO NOT BUILD — unscored or actively penalized
Extra skills to look like a bigger marketplace (**explicitly penalized as padding**) · an LLM anywhere in
the scoring path (destroys determinism, auditability, injection resistance) · applying fixes or any
re-audit loop (user-locked recommend-only) · hosted dashboard/SaaS/accounts · live ChatGPT/Perplexity
citation checks (non-deterministic, unscored) · elaborate demo theatrics, Adobe-relevance engineering, or
competitive-differentiation work (**no rubric criterion**) · multi-hundred-rule catalogs · real Adobe
API/MCP integration · databases/queues · screenshot-diffing or CV layout analysis.

---

## Verification

All commands run from the repo root (currently `C:\Users\DEVANSH\OneDrive\Desktop\ADOBE_HACK\citely-audit`
— see *Repository*), with the virtual environment activated. **Python 3.12 specifically**: the pinned
`lxml` and `greenlet` publish no wheels for 3.14, and the pins are what make recorded scores
reproducible. Setup details in `CLAUDE.md` → *Local environment*.

```bash
py -3.12 -m venv .venv && .venv/Scripts/activate               # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]" && python -m playwright install chromium   # setup (Tier A optional)
pytest                                                       # detection, generalization, security, scoring, E2E
python tests/run_precision_recall.py                         # per-check precision/recall vs labeled corpus
python skills/audit-orchestrator/scripts/run_audit.py --url http://127.0.0.1:8099/spa --html-out report.html
python skills/audit-orchestrator/scripts/run_audit.py --url http://127.0.0.1:8099/non-english   # i18n gating
for s in skills/*/; do skills-ref validate "$s"; done
```

Sign-off gates: all 6 skills pass `skills-ref validate` · report validates against schema **and** the
mandated floor · identical artifact ⇒ byte-identical report · non-English fixture produces zero
language-dependent `fail`s · consent-wall fixture produces one blocked finding, not a cascade · adversarial
fixture leaves scoring unchanged · run under the 5-minute budget · no writes to any target ·
no code/config/schema file contains `brand-ai-readiness-audit` — verify with
`grep -rn "brand-ai-readiness-audit" . --exclude-dir=.git`, whose only permitted hits are the historical tree
in `CONTEXT.md` plus prose in `CONTEXT.md`/`CLAUDE.md`/`PLAN.md` that *describes* the rename.
