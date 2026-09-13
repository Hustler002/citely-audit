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

> **Development moved to a second machine on 2026-09-10.** Paths in this repo are **not portable** —
> never copy an absolute path out of a doc without checking it against the machine you are on.
> Machine-specific setup lives in [Local environment](#local-environment-current-machine) below.

- **Path / git root:** `C:\Users\DEVANSH\OneDrive\Desktop\ADOBE_HACK\citely-audit` (current machine).
  Previously `C:\Users\PRIYANSHU PAL\Desktop\ADOBE\citely-audit` — historical, do not use.
- **Branch:** `feature/devansh-singh`, sitting exactly at `origin/main` (`437104b`, the merge of the
  phase 1-6 work). Ongoing phase-7 work continues on this branch. Working tree clean, 51 files tracked.
- **Remote:** `origin` on GitHub. Local `main` is intentionally behind and is not used for development.
- `.gitignore` present; `PLAN.md` is tracked.
- Project renamed `brand-ai-readiness-audit` → **`citely-audit`**. Identity fields rebranded;
  **skill folder names deliberately unchanged** (descriptive + agentskills.io-valid).

## Local environment (current machine)

Verified working on 2026-09-10: **848 passed, 13 skipped**, and `python.org` reproduces its recorded
score of 90.

**Python 3.12 is required in practice, not just 3.11+.** The machine shipped only Python 3.14, on which
`pip install -e .` **cannot succeed as pinned**: neither `lxml==5.3.0` nor `greenlet==3.0.3` (pulled in by
`playwright==1.47.0`) publishes a cp314 wheel, and both would fall back to a source build. Python 3.12.10
was installed alongside 3.14 rather than relaxing the pins, because the pins are what make the recorded
scores reproducible. **Do not "fix" a fresh-machine install by bumping the pins** — install 3.12 instead.

```powershell
# one-time setup, run from the repo root
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium      # ~345 MB, provisions Tier A; setup only, never inside a run
```

**Playwright browser location.** Chromium normally lands in `%LOCALAPPDATA%\ms-playwright` and is found
with no configuration. It is only worth knowing that a **packaged/containerised shell** (the Claude
desktop app is one) has its `%LOCALAPPDATA%` writes redirected into the package's `LocalCache`, and the
Windows loader then cannot resolve Chromium's private side-by-side assembly — `chrome.exe` fails with
*"the side-by-side configuration is incorrect"* and the audit silently degrades to Tier B. The fix is to
provision the browsers at a path outside `AppData\Local` and point `PLAYWRIGHT_BROWSERS_PATH` at it. A
copy provisioned that way currently lives at `C:\Users\DEVANSH\ms-playwright`. **An ordinary VS Code or
PowerShell terminal needs none of this.**

## Locked decisions (current — per PLAN.md v3; do not re-litigate)

- **Recommend-only.** No skill modifies any site. No apply-fix, no re-audit loop, no writes to the target.
  Suggestions may be **proactive** (improvements where no defect was found).
- **Orchestration** = subprocess + JSON file contract. The orchestrator owns **ALL** network I/O; every
  analyzer is a pure, network-free function of the shared crawl artifact — **zero exceptions**.
- **There is NO external lookup, and no flag pretends otherwise** (corrected 2026-09-12). PLAN §14
  lists Wikidata corroboration under NICE TO HAVE and it was never built. This entry previously
  described it as shipped, and `--allow-external` existed for nine phases doing nothing: it reached
  `diagnostics.external_lookup` and stopped, while `external_corroboration` stayed hardcoded `None`
  and no request was ever issued. The flag is removed. If it is ever built it belongs in the fetch
  stage, left of the artifact boundary, and the artifact already declares the
  `external_corroboration` slot for its result; `diagnostics.external_lookup` is now a constant
  `false` disclosing that every piece of evidence came from the audited pages themselves.
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
- **Analyzer output contract (enforced, not hoped for):** every analyzer ALWAYS emits exactly its
  declared `CHECKS`, each with a valid registry id, regardless of input. `finalize()` drops anything
  unrecognised and fills gaps with `unknown`. This is what makes the orchestrator safe to build:
  previously an internal exception emitted the FUNCTION NAME as a check id, which `_scoring` rejects
  as registry drift — crashing the whole audit and silently losing the real check.
- **Homepage-authoritative analyzers are anchored to the homepage.** crawl / comprehension /
  orientation measure the page with `role == "homepage"` or nothing. They must never fall through to
  another page: a consent wall on the homepage previously scored 58.1 at 0.805 coverage because
  `/pricing` was graded in its place. Entity corroboration is deliberately exempt — Organization
  markup legitimately lives on `/about`.
- **`safe_get` never raises.** It is the network boundary; every failure becomes a `FetchResult`
  with an `error_kind`, including a final catch-all for unanticipated resolver/urllib3 errors.
- **Entity identity is graded by STRENGTH, not presence in JSON-LD** (added 2026-09-08).
  A typed schema.org entity is `pass`; Open Graph identity alone (`og:site_name`) is `partial`;
  nothing is `fail`. Treating Open Graph as equivalent to declaring nothing scored github.com at
  37.5 and — because a `verified` fail suppresses dependents — also wiped `sameas_present`,
  `sameas_authority` and `name_consistency` to `unknown` as collateral.
- **Finding wording is chosen by check STATE.** Registry `title`/`plain_summary` describe what GOOD
  looks like; `failure_*` describes a fail; `partial_*` describes a partial. Using the wrong one
  produced titles that contradicted their own evidence in BOTH directions — "Mobile viewport is
  declared" for a missing viewport, and "No organization or person entity declared" for a page whose
  evidence read "Identity declared only via Open Graph".
- **stdout carries the report and nothing else, ENFORCED not intended** (added 2026-09-10).
  Every skill writes JSON to stdout, so one stray byte in front of it breaks a CI consumer. Two
  mechanisms, because one is not enough. (1) `configure_logging()` passes **`force=True`** —
  `logging.basicConfig` is a *silent no-op* when the root logger already has a handler, so a
  dependency that configures logging at import time keeps its handler AND its stream. Probed and
  confirmed: with a library calling `basicConfig(stream=sys.stdout)` first, our records land on
  stdout and our format is discarded. `force=True` is the load-bearing argument; without it the
  function is decoration. (2) `stdout_reserved_for_report()` holds `sys.stdout` shut for the whole
  audit, because a `print()` in a lazily-imported dependency is not logging and carries no stream
  to correct. Diverted bytes are counted and warned about, never silently swallowed.
- **Analyzer subprocess stderr is forwarded, tagged and capped.** `capture_output=True` pipes the
  child's stderr, which was being discarded outright — the only diagnostic a failing analyzer
  produces. Capped, because the child is the component holding page-derived text.
- **The advisor prescribes; it never measures, and it cannot move the score** (added 2026-09-11).
  `remediation-advisor` runs AFTER scoring, emits no check state, and only ever ADDS fields to
  findings that already exist. Proactive items go in `recommendations[]`, outside `summary` counts,
  because the mandated floor requires `total_findings == critical + high + medium` — and because
  beyond-problem advice must not be able to move a number it has no business touching. Asserted
  end-to-end, not by convention: `test_the_advisor_cannot_change_the_score` runs the same audit with
  the advisor present and absent and compares score, coverage and finding ids.
- **A snippet is filled only from values OBSERVED on the page** (added 2026-09-11). Unobserved
  placeholders stay literal and are listed in `placeholders_remaining`, which is the deliverable
  rather than a failure: it tells the reader which facts they must supply instead of handing them a
  fabrication. No LLM touches the advice path, for the same reason none touches scoring.
- **A copy-paste snippet that does not parse is worse than none** (added 2026-09-11). Snippets
  declare a `snippet_json` shape and a test parses each one after substitution. This caught a real
  defect: the `sameas_authority` snippet joined observed profile links with no trailing comma and
  then appended another array element, so a site that already had one profile link was handed
  JSON-LD that looked complete and did not parse.
- **A proactive suggestion never repeats a finding, and suppression is checked AFTER the detector
  runs** (added 2026-09-11). Suppressing first was cheaper but recorded "already reported as a
  finding" for detectors that had nothing to say — on four of five real sites sampled — so the
  diagnostics asserted a redundancy that was never established. `suppressed` now means exactly one
  thing: this had something to report and it was withheld.
- **Never express a deadline in a unit the clock cannot represent** (added 2026-09-11).
  `time.monotonic()` on Windows is `GetTickCount64()` with a resolution of **15.625 ms**, so a test
  passing `deadline = monotonic() + 0.001` was asking whether a sub-tick interval had elapsed. It
  had no answer: the result depended on whether a tick boundary happened to fall during the crawl,
  which failed about one run in six. A deadline test drives the clock explicitly instead — the real
  one is installed only in `_artifact`, so `_safe_fetch` keeps full real budget for the fixture
  fetches while the page loop sees an expired deadline on every iteration.
- **The plain-language layer lives in the REPORT, not in a renderer** (added 2026-09-11).
  The brief grades the marketplace "not any single report it happens to produce", and asks whether
  the entrypoint *is built to emit* something a non-expert could act on. So the verdict, the ranked
  actions and the coverage caveat are fields in the emitted JSON. A machine consumer gets the same
  plain reading a person does, and there is one wording rather than two that can disagree.
- **A score built on a minority of the evidence is not a verdict on the site** (added 2026-09-11).
  Below `COVERAGE_FLOOR` (0.5) `summary.headline_reliable` is false, the headline sentence says the
  site could not be measured, and any category under the same floor says so too — even while its
  NUMBER stays in `category_scores`, because deleting a measurement hides it. Chosen as a principle,
  not fitted: under a half, most of the evidence is missing. It separates the two JavaScript shells
  (0.175) from every real page measured, the thinnest being the German fixture at 0.698. This closes
  the second half of open issue #10.
- **Ranking is by what the fix is worth, never by how alarming it sounds** (added 2026-09-11).
  `next_actions` sorts on `points_recoverable` first, severity only as a tie-break. The first test
  of this passed with the points key deleted, because on that fixture severity order and gain order
  coincided — the fourth false-pass this project has caught by mutation. It is now asserted on a
  case where the two orderings disagree.
- **A word list may be positive evidence; it may never be a requirement** (added 2026-09-11).
  Third instance of this rule, after the SPA markers and the entity type allowlist. A closed
  vocabulary only ever fits the sites it was written against, and the open web has every trade and
  product category in it. Where a list must exist, it belongs on the SUBTRACTING side: a phrase
  missing from `vague_markers` can never cause a failure, whereas a category missing from
  `offering_nouns` used to cause one on every unseen trade.
- **Corpus labels come from a fixture's construction, never from a run** (added 2026-09-11).
  `labeled_corpus.json` records what each page was BUILT to contain. Recording what the auditor
  happens to emit would produce a corpus that passes by definition and measures nothing.
- **The User-Agent never carries a contact URL we do not own** (added 2026-09-12).
  It shipped for nine phases as `CitelyAuditBot/0.1 (+https://example.com/citely-bot)`, beside a
  config note saying to replace it "before any run against a third-party site" — a guardrail already
  crossed on python.org, Wikipedia, Django, dev.to, eff.org and github.com. `example.com` is
  IANA-reserved for documentation, so an operator who investigated the bot reached a placeholder
  dressed as a real contact. No correct value was available: the repository is private, so its URL
  would 404 AND leak the repo path to every audited host, and inventing one would breach this
  project's own rule against presenting an unobserved value as a fact. So `fetch.contact_url` is now
  a separate field, **empty by default**, appended as ` (+URL)` only when set. The agent is composed
  in one place so the product token the robots parser matches on cannot drift from the string
  actually sent. Guarded by a test rejecting every reserved documentation domain.
- **A signal is graded by exactly one check** (added 2026-09-12). `extraction.semantic_html` used to
  grade h1 count alongside landmarks, while `content.heading_hierarchy` owns heading structure and
  already requires exactly one h1. A missing h1 was therefore charged in TWO categories, and no
  suppression edge could catch it: suppression follows a dependency chain, and these are unrelated
  checks in different categories that happened to read the same signal. The landmark check now
  grades landmarks and nothing else. Measured benefit beyond tidiness: eff.org publishes a real
  `article` landmark and two h1s, and was being marked down on LANDMARKS for a heading defect.
- **`nav` still earns partial credit, on evidence** (decided 2026-09-12). It looked wrong that a
  page marking up only its chrome escapes `fail`. A 32-site survey says leave it alone: nav-only
  occurs **once in 32** (the audited site itself), the "content stuffed in the nav" pathology occurs
  **zero** times, and the obvious rule — penalise a high share of text inside `nav` — is disproved,
  because the highest shares in the corpus belong to hubspot (62.8%), MDN (51.9%) and w3.org (32%),
  all of which publish a proper `main` or `article`. Tuning on a single example is the
  fit-to-fixtures failure this project keeps punishing. Revisit only with more nav-only sites.
- **A name is normalized by Unicode, never by the ASCII alphabet** (added 2026-09-12).
  `normalize_name` stripped `[^a-z0-9]`, so a brand written in its own script normalized to the
  EMPTY STRING — and two empty strings do not intersect. `_names_agree` therefore returned False
  for two byte-identical names in Japanese, Chinese, Korean, Greek, Cyrillic, Arabic and
  Devanagari. Measured on a government homepage whose `<title>` and `og:site_name` carry the same
  five characters: the report said the name "matches neither the page title nor the domain" while
  the same report's title measurement quoted those exact characters. Two findings contradicting
  each other on one observable fact. Now `[\W_]+` under `re.UNICODE`, which keeps letters and
  digits in every script and leaves Latin behaviour byte-identical. Discrimination is asserted
  alongside agreement, because a normalizer that returned a constant would satisfy the first half
  and destroy the conflict detection the check exists for.
- **An inline asset has no filename, and its payload is not evidence** (added 2026-09-12).
  `extraction.facts_not_image_only` derived a filename with `urlparse(src).path.rsplit("/")`. On a
  `data:` URL that returns a slice of base64, whose alphabet is exactly what the fact-filename rule
  looks for — letters for words, digits for a figure. Measured: **420 of 500 random inline PNGs**
  were reported as images carrying trapped facts, and the evidence string printed in the report was
  raw payload (`UEh4tn8UsNoAAAAASUVORK5CYII=`). Same family as the 2026-09-10 fingerprint defect one
  layer earlier: that fix made the NAME harder to satisfy, while these URLs have no name at all.
  `asset_filename()` now returns `""` for `data:` and `blob:`, so an inline image is counted as
  present and never as suspect — the treatment `hero.jpg` already had.
- **Every stream boundary is explicitly UTF-8, never the platform locale** (added 2026-09-12).
  `subprocess.run(text=True)` decodes with `locale.getpreferredencoding(False)` — cp1252 on a
  default Windows install — while we tell the child to WRITE utf-8 via `PYTHONIOENCODING`. That
  mismatch had two failure modes and the quiet one was worse: **silent corruption** of every
  non-ASCII evidence string ("café" arriving as "cafÃ©"), and a **crash** whenever a byte landed on
  one of the five cp1252 cannot map (0x81, 0x8d, 0x8f, 0x90, 0x9d). The crash is indirect: the
  reader thread's exception is swallowed, `stdout` is left as `None`, and `len(None)` kills the run
  after all the work is done. All child reads now go through `CHILD_TEXT`, and `errors="replace"`
  is load-bearing because this boundary carries page-derived bytes. The report write and the
  child-stderr forward are hardened the same way: an audit must not die while printing its own
  result, nor while relaying a child's message.
- **Analyzers emit check states, not findings** (PLAN §6.1). Report wording lives once in
  `config/checks.json` and is applied by the orchestrator, so it cannot drift between analyzers.
  Contract: `references/check-result-schema.json`.
- **A finding is reported at the strength of what was MEASURED** (added 2026-09-10). Severity came
  straight from the registry, so a `partial` was announced at the same strength as a total failure.
  dev.to was told twice, both `critical`, that its identity was undeclared: once for having no
  JSON-LD, once for having only Open Graph — a partial that had already been awarded half credit.
  A partial is now graded one tier below the declared severity, with `medium` as the floor because
  there is no `low`. This is the answer to "double jeopardy" that does NOT reintroduce suppression:
  measured, suppressing `organization_declared` on a `structured_data_present` failure costs 24
  points of coverage to gain 5 of score, and reinstates the 2026-09-08 collateral damage.
- **Above-the-fold text means text a visitor can read.** A TreeWalker over `SHOW_TEXT` returns the
  contents of `<script>` and `<style>`, and those elements report `top = 0`, so inline JavaScript
  read as the first thing on the page: 3,301 of dev.to's 6,214 reported characters were source
  code, and the value-proposition check scored `if (navigator.userAgent === 'ForemWebView/1')`
  instead of the site's hero copy. Non-rendering tags and nodes with no layout box are excluded.
- **A build fingerprint is not a fact.** `extraction.facts_not_image_only` treated any digit in a
  filename as evidence of a chart. Every modern bundler fingerprints assets, so that matched almost
  everything: 79 of 108 images on dev.to, 59 of them 18x18 reaction icons named
  `exploding-head-daceb38d....svg`. Content hashes, UUIDs, bare row ids and URL-encoded proxy paths
  are now excluded, a data filename must pair a short number with real words, and images the page
  itself declares smaller than `min_fact_image_px` are not considered at all.
- **Entity signals are graded by CARRIER STRENGTH and detected STRUCTURALLY** (added 2026-09-10).
  Three rules, each replacing a fixed list that only fitted the sites we had tested.
  (1) *Identity is a shape, not a type name.* schema.org has ~200 LocalBusiness subtypes, so a
  five-name allowlist is wrong for most of the web: a real dental practice publishing a correct
  `Dentist` node with sameAs scored 37.5 -> 39.3, worse than a site with no markup at all. A node
  with a `name` plus two real-world facts (address / telephone / sameAs / logo / geo / ...) is an
  identity claim whatever it calls itself, so subtypes invented after this file was written are
  caught without being listed. (2) *sameAs is a signal, not a property.* It is read from any
  JSON-LD node, microdata `itemprop`, `rel="me"`, and ordinary profile links recognised two ways
  — path handle resembling the brand, or a short-path link to a known platform sitting in site
  chrome. Neither route covers the web alone: EFF satisfies only the first, python.org only the
  second. (3) *Authority includes directories and registries in full.* A local business will never
  have a Wikidata entry; its Google Business, Yelp or companies-registry record is the third-party
  anchor it can actually obtain. Self-published profiles are `partial`, never `fail`.
- **`unknown` means unmeasurable, never "absent".** `sameas_present` and `name_consistency`
  returned `unknown` whenever no JSON-LD entity existed, which is a claim we could not tell — and
  it was false, because the evidence was on the page. With the intra-category dependency edges,
  that wiped 44 of 100 points to `unknown` as collateral and pinned Entity Trust to a **fixed
  point**: `(22*0.5 + 24*0.5 + 10)/56 = 58.9` at coverage `0.56`, returned identically by eff.org,
  github.com and a small dental practice. A constant carries no information about the site.
- **Entity checks no longer suppress each other.** Their `depends_on` edges encoded a detection
  limit, not a causal one. All six now depend only on `render.content_without_js`, which is the
  real common cause: if the page is a JS shell, nothing is measurable.
- **Open Graph is scored once, not three times.** It was rescuing `structured_data_present` to
  `partial` while also carrying `organization_declared` to `partial` and passing
  `opengraph_identity` — 46 points of critical weight resting on one signal. Open Graph is a
  social-preview format with no schema.org type, so it no longer stands in for structured data.
- **Precision rules that keep the above honest.** Share widgets are excluded (every CMS emits
  them; counting them would pass essentially every site). A profile path is at most two segments
  with the handle at the end, because matching the brand token anywhere in the path claimed
  `wiki.qt.io/Qt_for_Python` and a dated blog post as python.org's own profiles. Names are
  compared by SEGMENT, so a tagline is not a contradiction. Nodes credited via `author` are
  excluded, but `publisher` is NOT: every major CMS points an Article's publisher at the site's
  own Organization, and excluding it deleted exactly the node the category looks for.
- **Never wait on `networkidle`, and never discard a timed-out DOM** (added 2026-09-10).
  `networkidle` needs 500 ms with ≤2 connections in flight; analytics beacons, chat widgets and
  long-polling hold a modern page above that line permanently, so it does not fire. Measured on
  python.org: `load` 1.2 s, `domcontentloaded` 3.9 s, `networkidle` 20.0 s then timeout. We wait on
  `load`, pursue quiescence as a bounded bonus (`network_quiet_ms`), and **salvage** the DOM on a
  navigation timeout — a milestone failing to fire does not mean there is no page. Disclosed as
  `diagnostics.render_nav_state` (`ok` / `busy` / `salvaged`).
- **The Tier-B fold window is budgeted in VISIBLE TEXT, never in markup** (added 2026-09-10).
  Markup length is not layout: on python.org the first 16,151 characters of `<body>` are 11,414
  characters of tags and 1,345 of text, so a 2,500-character markup slice never left the masthead.
  Non-rendering subtrees (`<script>`, `<style>`, inline `<svg>`, `<template>`, hidden nodes) are
  pruned before counting, and chrome (`<nav>`, `<header>`, dialogs) is charged at
  `chrome_text_weight` up to `chrome_text_cap` **per chrome root**, because a collapsed mega-menu
  occupies one bar on screen however many links it holds. Past that allowance chrome costs full
  price, so the discount cannot be farmed. `<nav>` and floating dialogs are additionally barred
  from supplying the headline: a menu label is not a hero and a consent banner is not a value
  proposition. `<header>` is **not** barred — heroes legitimately live there, on python.org and in
  our own healthy fixture, and excluding it would have created a fresh false negative.
- **The two render tiers must agree on the same page.** An image-only heading
  (`<h1><img alt="Acme"></h1>`) has no `innerText` in Tier A and no `get_text()` in Tier B, so both
  now fall back to the accessible name. A tier-specific reading of the same DOM is a bug, not a
  tier difference.

## Verified spec facts (agentskills.io)

- Skill = folder + `SKILL.md`; required frontmatter is only `name` + `description`. Optional: `license`,
  `compatibility` (≤500 chars), `metadata` (string→string), `allowed-tools` (experimental).
- `name`: ≤64 chars, lowercase alnum + single hyphens, no leading/trailing/consecutive hyphens,
  MUST match the folder name.
- Body recommended < 5000 tokens / < 500 lines; push detail to `references/`.
- Base spec has **no** `marketplace.json` / `entrypoint` concept — bespoke to this brief.
- Official validator: `skills-ref validate ./<skill>` (github.com/agentskills/agentskills).

## Current status: ALL TEN PHASES COMPLETE — **compliance signed off mechanically**

**1142 tests, 0 skipped.** `run_audit.py` fetches, selects pages, renders, runs all four analyzers as
subprocesses, scores, and emits a schema-valid JSON report — and then `remediation-advisor` attaches a
copy-paste snippet and a validation procedure to every finding, plus proactive suggestions where no
defect was found. The marketplace is now **six skills**, one entrypoint.

Verified on real input. **Re-measured 2026-09-10 on the second machine**, after the entity-trust
generalization and the measurement-precision fixes recorded in the progress log. Fixture rows are
`--html-file` runs; live rows are `--url` runs with Tier A. The superseded column is kept because the
movement is the evidence that those two work items did what they claimed.

| Target | Score | Coverage | Findings | Was (pre-generalization) |
|---|---|---|---|---|
| `https://example.com` (live) | 66/100 | 0.975 | 12 — all genuine (no structured data, 14-char title, no meta description) | 62 / 0.805 / 8 |
| `https://www.python.org` (live) | **90/100** | 0.975 | 4 | 90 / 0.865 / 4; 77 / 0.795 / 5 before the render fixes |
| healthy fixture | 99/100 | 0.85 | 1 | unchanged |
| **German fixture** | **98/100** | 0.698 | 1 — generalization proven: the gap shows as *coverage*, not failures | unchanged |
| SPA shell (`broken_page.html`) | 30/100 | 0.175 | 2 | unchanged |
| hydrating SPA (`spa_hydrating.html`) | 80/100 | 0.175 | 1 | not previously tabulated — **see the SPA headline problem below** |
| deep-chrome fixture | 76/100 | 0.85 | 6 — the python.org shape, `<h1>` 41,752 chars into `<body>` | 75 / 0.68 / 2 |

Coverage rising toward 1.0 on live sites is the intended effect of the entity work: checks that used to
return `unknown` are now measurable. The extra findings on `example.com` and the deep-chrome fixture are
those newly-measured checks reporting, not new defects.

> **Correction (2026-09-11).** An earlier revision of this table reported the SPA shell as having
> drifted 30 -> 80. It had not. The row has always meant `broken_page.html`, which still returns
> 30 / 0.175 / 2 exactly, and is also the source of `sample-report.json`. The 80 belongs to a
> different fixture, `spa_hydrating.html`, which was never in this table. Both rows are now named
> by file, because "SPA shell" was ambiguous between two fixtures that legitimately score very
> differently — one is a dead shell whose bundles 404, the other genuinely hydrates.

#### ✅ The SPA headline problem — **FIXED in Phase 8** (2026-09-11)

`spa_hydrating.html` reports **80/100 at 0.175 coverage**. This is not a regression and never was:
only 17.5% of total check weight is measurable on a page that builds itself in the browser, and the
few checks that survive are ones it happens to satisfy. Human Orientation reads **100.0 off
`category_coverage` 0.1** — a single surviving check. The engine is behaving exactly as the
capability model specifies. `broken_page.html`, a deader shell, scores 30 at the same coverage, and
the gap between the two is the point: coverage this thin makes the headline arbitrary.

**The defect is in output design, not in scoring.** A page that no AI assistant can read presents an
80 as its headline number, and the coverage figure that disproves it sits beside it as an equal peer.
This is the successor to open issue #10, and the same reasoning applies: `category_coverage` makes the
problem *visible* but does nothing to stop the headline being *misread*.

**Fix belongs in Phase 8 (non-expert output layer, PLAN.md §9), not in `_scoring.py`.** Rationale:
- Suppressing or penalising the score would put a presentation concern inside the scoring spine, where
  the whole project's determinism and auditability guarantees live.
- PLAN.md §9 layer 1 is already specified as a plain-language verdict per category. A page scoring 80
  on 17.5% coverage must not be allowed to render a verdict sentence that reads like a pass.
- The standing rule in Locked decisions — *never show a score without coverage* — needs to become a
  property the renderer **enforces**, not a convention the reader is trusted to honour.

**Acceptance criterion, now met.** `spa_hydrating.html` reports `headline_reliable: false`, its
verdict reads "We could not measure enough of your site to give it a score", Human Orientation says
"We could not check what a visitor sees when they arrive" despite scoring 100.0, and `not_checked`
names the cause in plain words: *blocked by an earlier problem: content only appears after
javascript runs*. Neither 80 nor 100.0 stands as a bare headline anywhere. The numbers are still
there — `discoverability_score` is still 80 and `category_scores.human_orientation` still 100.0 —
because hiding a measurement is not the same as declining to headline it.

Run it:
```bash
./.venv/Scripts/python.exe skills/audit-orchestrator/scripts/run_audit.py --url https://example.com
./.venv/Scripts/python.exe skills/audit-orchestrator/scripts/run_audit.py --html-file page.html --ci
```
stdout is the report and nothing else; logs go to stderr. `--ci` exits 1 on any critical finding.

All ten phases are complete. The remaining optional, unscored items have been reviewed and explicitly declined: `render_html.py`, `checks-reference.md`, and browser reuse across pages.

### Implementation phases
| Phase | Scope | Status |
|---|---|---|
| 1 | Scoring spine & schema contracts | ✅ **DONE** |
| 2 | Safe acquisition (`_safe_fetch.py`, SSRF, robots) + security corpus | ✅ **DONE** |
| 3 | Artifact pipeline (`_page_select.py`, `_render.py` Tier A/B) | ✅ **DONE** |
| 4 | Structural analyzers (crawl/render/extraction, entity) | ✅ **DONE** |
| 5 | Parity analyzers + i18n (engagement, quotability) | ✅ **DONE** |
| 6 | Orchestration & report assembly | ✅ **DONE** |
| 7 | `remediation-advisor` (6th skill) + proactive suggestions | ✅ **DONE** |
| 8 | Non-expert output layer (JSON) — SPA headline problem fixed. `render_html.py` **deferred, unscored** | ✅ **DONE** |
| 9 | `labeled_corpus.json` + precision/recall + archetype fixtures | ✅ **DONE** |
| 10 | Compliance & sign-off (`agentskills validate`, README) | ✅ **DONE** |

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
| 10 | **A category can score 100 from a single measurable check.** On the SPA fixture, Human Orientation reads 100.0 because 5 of its 6 checks were suppressed and only `viewport_meta` remained. Overall `coverage` exposes this, but a per-CATEGORY coverage figure would stop a category headline being read as a clean bill of health. Relevant to the rubric's output-design criterion. | Medium | ✅ **RESOLVED 2026-09-11.** `summary.category_coverage` shipped in Phase 6 made it visible; Phase 8 made it un-misreadable. A category under 0.5 coverage now says it could not be assessed, while keeping its number. Asserted by `test_a_category_scoring_100_off_one_check_does_not_claim_to_be_fine`. |
| 8 | **Browser relaunched per page.** `render_page` launches Chromium for every page (~3-4 s of the ~5 s per-page cost). At 5 pages that is ~25 s of the 270 s budget — acceptable now, but reusing one browser across pages is the obvious win if the budget ever tightens. | Low | Optimize if needed |
| 11 | **Character-count bands are calibrated for alphabetic scripts and applied to logographic ones.** `content.title_descriptive` (15-70) and `content.meta_description` (50-160) are both `requires_language=false` and count raw characters. A CJK character carries roughly a morpheme, so the same organisation passes in English and fails in its own script. Proof from one real page: `jma.go.jp` titles itself `気象庁 Japan Meteorological Agency` (31 chars, **pass**); the Japanese name alone is 3 chars (**partial**). Measured on 9 CJK homepages: **6 of 9 report a false TITLE partial** (総務省 = 3 chars = "Ministry of Internal Affairs and Communications", 46 Latin chars; 文部科学省 = 5 chars = 61 Latin chars), and **2 of 9 also a false DESC partial**. `detect_faq_schema`'s 8-character question floor is a minor third instance. | **High** | **Open — deliberately NOT fixed.** The defect is proven; no remedy is. East Asian Width gives a factor of 2, measured insufficient (総務省 → 6, still under 15); a CJK-specific floor would be a constant fitted to a 9-site sample, the fit-to-fixtures failure this project has punished five times; `requires_language` gating is too blunt because `language.supported` is `["en"]` alone, so it would also gate German and French, where the band is valid. Needs a labelled multi-script corpus with human judgements of title adequacy before any constant is chosen. |
| 12 | **`authority_domains` contradicts its own check's definition.** `check_sameas_authority` documents authority as "a record somebody ELSE maintains" and grades self-published profiles `partial` because "the brand controls both ends". The list nevertheless contains `linkedin.com`, `github.com` and `gitlab.com`, whose organisation profiles are created and controlled by the organisation — while `facebook.com`, equally self-managed, is classified social. Observed effect: `digital.go.jp` scores a **`pass`** on a LinkedIn company page alone, i.e. full authority credit with no independent corroboration. | Medium | **Open — not changed.** Genuinely contested: LinkedIn requires domain verification and is widely used for entity reconciliation, so its inclusion is defensible. Reclassifying would move `healthy_page.html` and `archetype_adversarial.html`, both carrying labelled corpus score bands, so it needs a deliberate re-label rather than a unilateral edit. |
| 13 | **A truncated compressed body is accepted without error.** urllib3 exposes no public signal that a gzip or Brotli stream ended early, so a body cut short decodes to partial or empty HTML and is analysed as if complete. Measured: truncated gzip returned 34 bytes of partial HTML and truncated Brotli an empty body, both with no error. Pre-existing for gzip. | Low | **Open, deliberately not fixed (decided 2026-09-13).** Rare in practice. Detecting it would mean owning the decode loop instead of using urllib3, and a narrower rule that treats compressed bytes with no decoded output as malformed would miss partial truncation and wrongly refuse a genuinely empty compressed page. |

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

### Resilience hardening (2026-09-06, pre-Phase-6)

An adversarial probe (malformed artifacts, hostile HTML, XML attacks, hostile URLs, bad geometry)
found **five real defects**, all now fixed with regression tests in `tests/test_resilience.py` (306):

| # | Defect | Impact |
|---|---|---|
| 1 | Internal check error emitted the **function name** as `check_id` | **Orchestrator crash** — `_scoring` rejects unknown ids as registry drift, and the real check vanished |
| 2 | `pages` as a dict (not a list) raised `AttributeError` in **all four** analyzers | Crash on a replayed/hand-written artifact |
| 3 | Wrongly-typed fields (`raw.html` as int, `geometry.headings` as None, `top` as a string) | Silent **false negatives** — real checks became `unknown` |
| 4 | 5000-char hostname raised `UnicodeError` from the IDNA codec, uncaught | **Crash** — `resolve_host` only caught `gaierror` |
| 5 | Blocked homepage silently graded **a different page** | **False negative** — consent wall read 58.1 / 0.805 coverage; now 0.0 / 0.08 with `homepage blocked: consent_wall` |

Verified safe: XML billion-laughs and XXE degrade to empty (no file read), oversized sitemaps are
capped, 10k-image and deeply-nested HTML complete without blowup, and exception messages are never
leaked into report evidence (only the exception TYPE).

### Phase 6 delivered
- [x] `run_audit.py` — the real entrypoint: safe fetch → artifact → **subprocess fan-out** to all
      four analyzer skills → check states → scoring → schema-valid report on stdout.
- [x] **Remediation text added to all 24 checks** in `checks.json`. The mandated schema requires
      `suggested_action` on every finding, so Phase 6 could not emit a valid report without it;
      keeping it in the registry means report wording still has one source of truth.
- [x] **Failure phrasing** (`failure_title`, `failure_summary`) added to all 24 checks. Registry
      `title`/`plain_summary` describe what GOOD looks like, which read backwards in a finding
      ("Mobile viewport is declared" when it is missing).
- [x] `summary.category_coverage` — closes issue #10: a category can score 100.0 off one surviving
      check, and per-category coverage makes that self-evident.
- [x] Subprocess isolation: explicit env allowlist (no inherited tokens), per-child timeout, stdout
      size cap, 0700 temp dir removed in `finally`. A failed analyzer degrades its checks to
      `unknown` and is recorded — it never takes the audit down.
- [x] `--ci` exit code; `_assert_no_ssrf_bypass_via_cli()` asserts the SSRF kill-switch can never
      become a CLI flag.
- [x] `sample-report.json` **regenerated from real orchestrator output** (was a hand-written
      placeholder since Phase 1), with timestamps pinned so the committed file is stable.
- [x] `tests/test_orchestrator.py` — 32 tests.

### Acquisition & fallback resilience (2026-09-10, post-Phase-6)

Auditing `python.org` exposed **one defect in each rendering tier**, which compounded: Tier A threw
away a working render, and the Tier-B fallback it dropped into then misread the page. Human
Orientation scored **41.7** on a site with a heading, a CTA and a value proposition above the fold.

| # | Defect | Impact |
|---|---|---|
| 1 | `page.goto(..., wait_until="networkidle")` | The milestone needs 500 ms of near-silence and never fires on a page with beacons or long-polling. The full 20 s render budget was burned, then the **complete 68 KB DOM Chromium was holding was discarded** and the audit degraded to Tier B. |
| 2 | Tier-B fold window sliced **markup**, not layout | Mega-nav markup, an inline `<svg>` sprite and a critical-CSS block consumed the 2,500-character budget before it reached the content. Two false `fail`s: "No heading near the top of the document" on a page whose hero is a heading, and a value proposition marked missing because the headline was never seen. |

Both fixed, with `tests/test_render_resilience.py` (48) and `tests/fixtures/deep_chrome_page.html`.
Measured on python.org: overall **77 → 90**, Human Orientation **41.7 → 91.0**, category coverage
**0.72 → 1.0**, `render_mode` heuristic → playwright, `partial: true (render_failed)` → `false`,
wall clock 58.6 s → 40.8 s. Every pre-existing fixture score is byte-identical.

**Ten mutations were applied to confirm the tests fail without each fix**, and that paid off as it
did in Phase 2: `test_wrapping_the_page_in_nav_does_not_buy_free_space` **passed with the per-root
chrome cap removed**, because its `<nav>` was so large the discount alone exhausted the budget. It
was measuring the discount, not the cap. Resized to the band where only the cap can decide, and it
now fails correctly under mutation.

Related edge cases handled at the same time, each with a test: inline `<svg>` sprites and
critical-CSS blocks consuming the budget · consent dialogs displacing content in DOM order · nodes
that are `hidden` / `aria-hidden` / `display:none` both *costing* and *earning* nothing ·
image-only headings read via `alt` in **both** tiers · a bounded DOM walk · markup that a character
slice would have cut mid-tag · `timeout=0` reaching Playwright, which reads it as "wait forever".

Also extended `test_embedded_thresholds_match_registry` to the two Phase-5 analyzers, which it had
never covered despite this file claiming drift is caught by tests.

### Phase 7 delivered
- [x] **6th skill `remediation-advisor`** — the marketplace's only *prescription* skill. Consumes
      findings, resolved check states and the crawl artifact; emits corrective snippets and
      proactive suggestions. Declared in `marketplace.json`; still exactly one entrypoint.
- [x] `references/advice-schema.json` — the **fourth** cross-process contract, alongside the crawl
      artifact, the check-result and the report schemas.
- [x] `references/remediation-templates/{corrective,proactive}.json` — snippet and validation text.
      All **24 checks** carry a validation procedure; snippets are `null` only where pasting markup
      cannot fix the problem (a 500 response, a client-rendered architecture).
- [x] **Four proactive detectors**, each with a fixture that fires it and a near-miss that must stay
      silent: `R:faq_schema`, `R:facts_in_prose`, `R:undated_claims`, `R:image_facts_with_alt`.
- [x] Orchestrator wiring: the temp dir now spans analyze → score → advise, still removed in a
      `finally` because it is 0700 and holds page-derived HTML.
- [x] `sample-report.json` regenerated — score, coverage and findings **byte-identical**, now with
      snippets and validation attached.
- [x] `tests/test_remediation_advisor.py` (72). **Eight mutations applied, all caught.**

### Phase 7 — what the mutations found

Two of the eight initially SURVIVED, and both were tests passing for the wrong reason. This is now
the third time this project has hit that exact failure mode, after the chrome-cap test in the
render work and the share-widget test in the entity work.

| Test | Passed because | Fixed by |
|---|---|---|
| `..._ignores_a_question_with_no_answer` | Both headings were under the 8-character minimum-question filter, so the detector never got as far as looking for an answer | Longer questions, plus a separate test for the length floor |
| `..._is_silent_once_a_machine_readable_date_is_present` | Its sentence carried two claims against a threshold of three, so the detector could not have fired with or without the date | One shared 3-claim sentence across all four tests, with the claim count asserted explicitly |

The harness itself also lied once: a mutation reported as surviving had been served from a stale
`.pyc`, because a file written and reverted inside one filesystem timestamp tick can defeat
mtime-based invalidation. The mutation runs now set `PYTHONDONTWRITEBYTECODE`.

### Phase 8 delivered
- [x] `_narrative.py` — the non-expert output layer as pure functions over already-scored results.
      It reads scores and never writes them, which is asserted rather than intended.
- [x] `summary.verdict` + `summary.category_verdicts` — PLAN §9 layer 1, one plain sentence each,
      wording in `config/checks.json` under a new `categories` block so it still lives in one file.
- [x] `next_actions[]` — §9 layer 2: the same findings ordered by **points recoverable**, answering
      do / where / confirm / gain. A ranked VIEW, not a second copy; `findings[]` keeps its severity
      order so `F-001…` stay stable, which is exactly why it cannot also carry the ROI ordering.
- [x] **`summary.headline_reliable` + `headline_caveat`** — §9.1, in the DATA. Below 0.5 coverage the
      headline is withheld, so a CI job piping the JSON is protected the same way a reader is.
- [x] `not_checked[]` — which categories rest on a minority of the evidence and why, in plain words.
- [x] `tests/test_narrative.py` (37). **Six mutations applied, all caught.**

### Phase 8 — why there is no HTML report

The brief was read rather than assumed, and it settles this in four places:

| The brief says | What follows |
|---|---|
| "We evaluate the submitted marketplace itself … **not any single report it happens to produce**" | The graded artifact is the package and its schema, not a rendered file |
| Output-design row: the entrypoint "**is built to emit**" a report a non-expert could act on | The plain-language layer must live in what the entrypoint emits |
| "emit a **single** audit report (fixed schema)" | An HTML file would be a second artifact, not that one |
| the schema is "a **floor, not a ceiling** — you may add fields" | Adding plain-language fields to the JSON is explicitly sanctioned |

The brief never mentions HTML, a rendered view or a viewer, and the submission is a zip of the
marketplace plus a README. `render_html.py` therefore earns no rubric points and is **deferred, not
cut** — revisit only if Phases 9 and 10 finish early.

### Phase 9 delivered
- [x] `tests/labeled_corpus.json` — 10 fixtures labelled `must_find` / `must_not_find` /
      `score_range`, written from each fixture's CONSTRUCTION. A corpus recorded from the tool's own
      output asserts nothing.
- [x] `tests/run_precision_recall.py` — prints the table for a human; `tests/test_corpus.py` (18)
      asserts the same numbers as a CI gate. One implementation, two entry points.
- [x] Four new archetypes: **e-commerce PDP**, **image-heavy**, **div soup**, **adversarial**.
      The adversarial one is a structural CLONE of `healthy_page.html` plus injections in seven
      carriers, so any difference is attributable to the injection and nothing else.
- [x] **Five stale Phase-1 stub files deleted.** Their skip reason still read "checks not
      implemented yet" — untrue since Phase 4 — and a reviewer running `pytest` saw it. The suite
      now reports **0 skipped**. Their three unasserted properties were absorbed, not dropped:
      the sample report is checked against a fresh run, two audits of one input are asserted
      byte-identical, and the broken page must score materially below the healthy one.

| Corpus result | |
|---|---|
| recall | 100% (16 labelled defects, 0 missed) |
| precision | 100% (98 must-not-find labels, 0 violated) |
| scores in band | 10 of 10 |
| adversarial twin | identical score and findings to the healthy page |

### Phase 9 — the corpus found a real defect on its first use

`orientation.value_proposition` gated on a closed list of **18 offering nouns and 36 action verbs**.
Consequences, measured rather than argued:

| Page | Old verdict |
|---|---|
| "Emergency lock repair across Leeds" | **fail, high** — no list entry for locksmithing |
| "Rowan 3-Season Down Jacket" + weight, fill, use case | **fail, high** |
| "Archival book printing for short runs" | partial |
| `example.com`, which offers nothing at all | **partial — better than the locksmith** |

The ranking was inverted: a placeholder page outranked a real business's clear proposition. Same
defect family as the five-name entity type allowlist fixed on 2026-09-10, and PLAN §8 already
forbids it — word lists are "positive evidence, never a requirement".

**Rewritten around SPECIFICITY.** A concrete anchor is a figure with a unit or a proper noun used
inside a sentence; vague markers only ever subtract, so a phrase missing from that list can never
cause a failure. **Three defects in my own fix, each found by probing rather than reasoning:**
  1. Title Case headings read as proper nouns, so "Example Domain" looked specific. Fixed by
     ignoring a capitalised word that follows another capitalised word.
  2. A single brand name rescued a hero carrying seven filler phrases, because the penalty
     subtracted while anchor plus vocabulary outran it. Filler now CAPS the outcome instead.
  3. `example.com` still passed on the link label "Learn more" matching the verb "learn". A single
     incidental hit is not evidence, so the vocabulary path now needs a verb AND a noun.

Result: `ecommerce` 94 → 100, the locksmith 87 → 93, `example.com` correctly below both. Every
other fixture unchanged, and all 454 analyzer tests still pass including the Phase-5 anti-gaming
tests.

### Phase 10 delivered — every sign-off gate is now a test

A sign-off written into a document decays the moment the code moves, so each gate from PLAN.md's
Verification section is asserted mechanically in `tests/test_compliance.py` (43).

| Gate | How it is enforced |
|---|---|
| All six skills `agentskills.io`-compliant | The **reference validator** runs on each folder, AND the same rules are asserted natively — name present, valid charset, ≤64 chars, equals the folder name, body under 500 lines, description substantial enough to select on |
| Manifest well-formed, exactly one entrypoint | Declared ids match the folders on disk in BOTH directions; every path is relative, local and inside the marketplace |
| Report meets the brief's mandated floor | Required keys, the severity enum, and `total == critical + high + medium` |
| Recommend-only, read-only | Source scan: no state-changing HTTP verb in any non-orchestrator skill |
| Only the orchestrator touches the network | Source scan: no network import outside the entrypoint |
| Legacy project name gone from code | Whole-tree scan, prose documents excepted |
| README describes each skill and the composition | Every manifest id present, composition and entrypoint described, no shipped phase advertised as pending, and the documented validator command is the one that exists |
| Determinism, corpus, injection resistance | Already enforced in `test_corpus.py` from Phase 9 |
| Under the 5-minute budget | Measured live: `python.org` **41s**, `djangoproject.com` **31s**, both five-page audits |

**The validator is pinned** (`skills-ref==0.1.1`, exposing the `agentskills` command) in the dev
extra rather than left to chance. The brief calls it "a convenience, not required", but skill-format
hygiene is scored, so it runs in CI — and its rules are duplicated natively so the guarantee outlives
the tool.

Two things the phase found rather than assumed:
- **The README documented `skills-ref validate`, a command that does not exist.** The installed entry
  point is `agentskills`. A setup instruction that does not run is worse than none, because it is
  trusted. Now asserted.
- **The compliance scan reported itself.** The legacy-name guard matched its own source, because the
  test necessarily contains the string it searches for. The needle is now assembled at runtime; an
  exclusion list would have been worse, since it would hide a genuine hit in that file.

### Not yet done — implementation order (PLAN.md §14)
- [x] ~~**2.** `_safe_fetch.py` + security corpus.~~ **DONE**
- [ ] **3.** Artifact assembly, `_page_select.py` (sitemap → homepage fallback), `_render.py` (Tier A/B);
      fixture server routes (robots, sitemap, redirect, bomb).
- [x] ~~**4.** structural analyzers~~ **DONE**
- [x] ~~**5.** parity analyzers + i18n~~ **DONE**
- [x] ~~**6.** Orchestrator wiring → schema-valid JSON report.~~ **DONE**
- [x] ~~**7.** `remediation-advisor` skill (**new, 6th**) incl. non-obvious proactive suggestions.~~ **DONE**
- [x] ~~**8.** Non-expert output layer.~~ **DONE** — built into the REPORT, not a renderer, because the
      brief grades the emitted schema. `render_html.py` deferred: the brief never mentions HTML and no
      rubric row scores it. The SPA headline acceptance criterion is met and asserted.
- [x] ~~**9.** `labeled_corpus.json` + precision/recall harness; archetype fixtures.~~ **DONE**
- [x] ~~**10.** validate all 6 skills; README refresh; determinism + read-only sign-off.~~ **DONE**

### Structural deltas from the current scaffold (v3 requires)
- **Add** `config/checks.json` (check registry) — does not yet exist.
- ~~**Add** 6th skill `remediation-advisor/`~~ **DONE**; **do not** add `report-renderer` (cut as padding).
- **Split** `run_audit.py` into `_render.py`, `_page_select.py`, `_scoring.py`, `render_html.py`.
- **Add** fixture archetypes: static, spa, ecommerce, corporate, image-heavy, unstructured,
  **non-english**, **consent-wall**, **adversarial** (+ `labeled_corpus.json`).
- **Extend** schemas: `summary.category_scores`, `summary.score_confidence`, top-level `recommendations[]`,
  `findings[].{page_url,check_id,measurement,threshold,selector,impact,plain_summary}`,
  `suggested_action.validation`, `partial_reason` enum; artifact gains `pages[]` + `external_corroboration`.

## How to run / verify

Setup is in [Local environment](#local-environment-current-machine) and only needs doing once. Everything
below runs from the repo root with the virtual environment **activated**.

```powershell
.\.venv\Scripts\Activate.ps1                     # PowerShell — VS Code's default terminal
```
`.venv\Scripts\activate.bat` for cmd, `source .venv/Scripts/activate` for Git Bash. The prompt gains a
`(.venv)` prefix; `where python` should then point inside `.venv\Scripts`.

```bash
python tests/run_precision_recall.py                                                     # corpus table
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com          # live audit
python skills/audit-orchestrator/scripts/run_audit.py --html-file tests/fixtures/healthy_page.html
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com --ci      # exit 1 on any critical
pytest -q                                                                                  # 1142 passed, 0 skipped
for s in skills/*/; do agentskills validate "$s"; done                                     # all six skills, pinned in the dev extra
```

Each analyzer skill is also independently runnable, which is what makes the folders agentskills.io
skills rather than modules of one program:
```bash
python skills/crawl-render-extraction-audit/scripts/crawl_render_extract.py --html-file page.html
python skills/quotability-density-audit/scripts/quotability_density.py     --html-file page.html
python skills/entity-corroboration-audit/scripts/entity_corroboration.py   --html-file page.html
python skills/engagement-orientation-audit/scripts/engagement_orientation.py --html-file page.html
python skills/remediation-advisor/scripts/advise.py                        --html-file page.html
```
The advisor's standalone mode runs the proactive detectors only — with no findings to prescribe
against, there is nothing corrective to say. Its full input set is `--findings`, `--check-states`
and `--artifact`, which is what the orchestrator passes.

Report goes to **stdout only**; logs to **stderr**. (`--html-out` is deferred — see *why there is
no HTML report* above.) That separation is
enforced, not merely intended — see the stdout contract in Locked decisions — so redirecting stdout to a
file always yields valid JSON:
```bash
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com > report.json
```
In PowerShell the two streams render into one console, which **looks like** contamination and is not;
`>` still captures stdout alone.

Without activating the venv, prefix commands with `.\.venv\Scripts\python.exe` instead of `python`.

## Conventions & guardrails

- Read-only; respect robots.txt (+ `meta robots` / `X-Robots-Tag`). See **robots policy** in Locked
  decisions and PLAN.md §6.3.1.
- Never crash: every stage records failures into `diagnostics.errors[]` and still emits a schema-valid report.
- Global monotonic deadline (default 270s); on pressure set `partial: true` + `partial_reason`
  (`budget` | `blocked` | `render_failed` | `analyzer_failed`).
- Treat every fetched byte as hostile; page-derived text reaches the report only sanitized, truncated, and
  delimited as untrusted data — never as instructions.
- User-Agent: composed in ONE place, `_safe_fetch.user_agent()`. `fetch.contact_url` is empty by
  default and appended as ` (+URL)` only when set — see the locked decision above.
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
- 2026-09-06 — **Resilience hardening before Phase 6.** Probed Phases 2-5 with malformed artifacts,
  hostile HTML/JSON-LD, XML attacks, hostile URLs and corrupt geometry. Found 5 real defects — two
  of them orchestrator-crashing, two false-negative-producing (details in the table above).
  The most valuable fix is structural rather than a patch: analyzers now enforce an output
  CONTRACT via `finalize()`, so no internal failure can ever emit an invalid check id or drop a
  check. The blocked-homepage bug was the subtlest — a consent wall looked like a mid-scoring site
  because a healthy secondary page was graded in its place.
  Test count 349 -> 647.
- 2026-09-08 — **Phase 6 complete. Citely runs.** Wired the orchestrator end-to-end and confirmed it
  against a live site plus four fixtures.
  Three gaps had to be closed first, all found by running it rather than by tests:
    1. The mandated schema requires `suggested_action` on every finding, but **no remediation text
       existed anywhere** and the advisor is Phase 7 — added `remediation` to all 24 checks.
    2. **`points_recoverable` inverted priority**: a medium viewport finding scored +50.0 against a
       critical render finding at +20.0, because the denominator was only the currently-measurable
       weight. Since PLAN §7 ranks fixes by points, that would have told users to fix a meta tag
       before server-rendering. Now divided by full category weight — stable and severity-consistent.
    3. **Findings were titled as passes** — "Mobile viewport is declared" for a missing viewport.
       Added explicit failure phrasing.
  Also fixed `partial`: the catch-all marked a scan partial whenever ANY check was unknown, which
  includes normal dependency suppression — so a completely successful audit reported
  `partial: true, reason: render_failed`. `partial` now means the SCAN was incomplete, nothing else.
  Test count 655 -> 687.
- 2026-09-08 — **Entity Trust grading corrected** (found by a user audit of github.com scoring 37.5).
  Detection was right — GitHub genuinely publishes no JSON-LD or microdata in raw OR rendered DOM —
  but `check_organization_declared` consulted ONLY JSON-LD; its Open Graph argument was literally
  named `_og`, i.e. deliberately unused. So `og:site_name = GitHub` counted for nothing, the check
  hard-failed, and the `verified` failure suppressed 44% of the category as collateral. The check's
  own impact text ("without it the brand has no declared identity") was factually false for the page.
  Now three-state: typed entity -> pass, Open Graph identity -> partial, nothing -> fail.
  github.com: entity_trust 37.5 -> 58.9, overall 80 -> 86. Deliberately NOT "very high" — Entity
  Trust measures the identity markup ON THE PAGE, not how famous the brand is, and Open Graph gives
  no schema.org type and no sameAs anchor.
  That surfaced a mirrored wording bug: PARTIAL findings were using absolute failure titles, so
  F-001 read "No organization or person entity declared" directly above evidence saying identity WAS
  declared. Added `partial_title`/`partial_summary` for the 12 checks that can emit partial, plus a
  self-maintaining test that derives which checks emit `partial` from analyzer source and fails if
  any of them would fall back to an absolute "No X" title.
  Test count 687 -> 705.
- 2026-09-10 — **Acquisition & fallback resilience** (triggered by a user audit of python.org
  scoring 77 with Human Orientation at 41.7). Two defects, one per rendering tier, that compounded.
  **Tier A — the `networkidle` trap.** The renderer waited on a state requiring 500 ms with at most
  two connections in flight. Analytics beacons, chat widgets and long-polling keep a modern page
  permanently above that line, so it simply never fires. Timed it rather than assuming: on
  python.org `load` returned in 1.2 s with 63 KB, `domcontentloaded` in 3.9 s with 53 KB, and
  `networkidle` timed out at 20.0 s — **holding 68 KB of fully rendered DOM that the exception then
  threw away.** The default is now `load`; quiescence is pursued separately under
  `network_quiet_ms` and its expiry is *recorded*, not raised; and a navigation timeout **salvages**
  the DOM. The salvage is the part that generalizes: it protects against every cause of a navigation
  timeout, not only this one. `diagnostics.render_nav_state` discloses `ok` / `busy` / `salvaged`,
  because a salvaged Tier-A render is not the same thing as a clean one.
  **Tier B — the fold window measured markup instead of layout.** The DOM-order proxy treated the
  first 2,500 characters of `<body>` as the first viewport. Measured python.org rather than guessing:
  the first heading with text sits **16,151 characters** into `<body>`, and that region is 11,414
  characters of tags against 1,345 of text — the budget was 71% spent on angle brackets. Rewritten to
  parse first (a character slice also cuts through tags and hands the parser wreckage), prune
  non-rendering subtrees, and spend the budget in visible text, with chrome charged at a discount
  capped per chrome root.
  **Three design errors caught by measuring instead of reasoning:**
    1. A pure visible-text budget was *still* not enough — python.org's masthead alone holds 3,204
       characters of text. The chrome discount is load-bearing, not a refinement.
    2. Skipping chrome outright would have broken our own healthy fixture, whose `<h1>` is inside
       `<header>`. Heroes live in banners. Only `<nav>` and floating dialogs are barred from
       supplying the headline, which is also what stops the discount being farmed.
    3. My anti-gaming test **passed with the cap removed** — its `<nav>` was large enough that the
       discount alone exhausted the budget, so it was measuring the wrong thing. Same failure mode as
       the Phase-3 content-type fix that was inert against a green suite. Found by mutation, not by
       review; resized to the band where only the cap can decide.
  python.org: 77 → **90** overall, Human Orientation 41.7 → **91.0**, coverage 0.795 → 0.865,
  `partial` true → false, 58.6 s → 40.8 s. All five pre-existing fixtures unchanged.
  Test count 705 → 753; added `tests/fixtures/deep_chrome_page.html` (the python.org shape, `<h1>` at
  body offset 41,752) which also serves Phase 9's archetype corpus.
  **A fourth error, and the one a stub could never have found.** Forcing a 700 ms navigation timeout
  against the live site to prove the salvage path showed it degrading to Tier B anyway: a timeout can
  land *mid-navigation*, where `page.content()` itself raises "the page is navigating and changing the
  content". The salvage was reading a document that was not yet readable. Now it reaches
  `domcontentloaded` first and retries the read within the remaining budget. With that, a 700 ms
  navigation budget produces the **same report as a 15 s one** — 90 / 91.0 / 0.865, `nav_state:
  salvaged`, still Tier A. Unit tests alone would have reported this fix as working.
- 2026-09-10 — **stdout contract enforced** (reported as "INFO render: logs are leaking into stdout").
  **Checked the claim before acting on it, and it did not hold.** Every `basicConfig` in the project
  already named `stream=sys.stderr`, there is no `print()` to stdout anywhere in `skills/`, and
  separating the two streams on a live run showed stdout was byte-for-byte valid JSON with the
  `INFO render:` lines on stderr where they belong. What was observed is PowerShell rendering both
  streams into one console, which is not contamination.
  **The underlying concern was still well-founded, and probing it found a real latent defect.**
  `logging.basicConfig` is a **silent no-op when the root logger already has a handler**. Verified
  rather than reasoned about: a library calling `basicConfig(stream=sys.stdout)` at import time
  keeps its handler, our call changes nothing, the record lands on **stdout**, and even our format
  is ignored. That is one dependency away from exactly the CI breakage described. Fixed with
  `force=True` in all five skills.
  A second hole `force=True` cannot close: a bare `print()` in a dependency imported LATER in the
  run — Playwright is imported inside the render stage — is not logging and has no stream to
  correct. So the orchestrator now holds `sys.stdout` shut for the whole audit and writes the report
  to the real stream afterwards. Diverted bytes are counted and warned about rather than swallowed,
  since a silent diversion hides a bug as well as a leak causes one.
  **Third finding, unrelated to stdout:** analyzer subprocesses run under `capture_output=True`,
  so their stderr was piped and then **discarded entirely** — the only diagnostic a failing analyzer
  produces was going nowhere. Now forwarded, tagged `[skill]`, capped at 20,000 chars because the
  child is the component holding page-derived text.
  `tests/test_stdout_contract.py` (31) asserts the property through real subprocess pipes, pins the
  `basicConfig` premise so a future Python change would announce itself, and guards `print()` via an
  **AST walk** — the first text-scan version flagged this project's own docstrings, which is how a
  guard gets deleted for crying wolf. Four mutations confirm the tests fail without each fix.
  Test count 753 → 784.
- 2026-09-10 — **Entity Trust generalized to the open web** (triggered by eff.org returning 58.9 at
  0.56 coverage, the same two numbers github.com and a small dental practice returned).
  **The figure was a fixed point, not a measurement.** `(22*0.5 + 24*0.5 + 10*1.0) / 56 = 58.9`,
  `56/100 = 0.56`. Every Open-Graph-only site landed exactly there whatever it published, so the
  score said nothing about the site. A second fixed point, 78.6 / 0.56, was hiding behind it.
  **Built a ten-site corpus before touching anything** — three bakeries, four dental practices and
  the three large sites already tested — rather than fixing one site at a time. That immediately
  overturned the premise I had started from. Small businesses often have BETTER structured data
  than tech giants: github.com and eff.org publish no JSON-LD at all, while a Portland bakery and
  two UK dental practices publish `LocalBusiness`, `Organization` and `Dentist` graphs with sameAs.
  **Five defects, none of which the original three-item proposal would have caught:**
    1. **A five-name type allowlist.** schema.org has ~200 LocalBusiness subtypes. A dental
       practice with a textbook `Dentist` node — name, address, telephone, logo, sameAs — scored
       **39.3, the worst in the corpus**, below sites with no markup whatsoever. Replaced with a
       structural rule: a name plus two real-world facts is an identity claim whatever its type.
    2. **sameAs read only from accepted-type nodes**, so those same practices' declared links were
       invisible because of what the node called itself.
    3. **Names compared whole**, so a bakery's own "Bakeshop" and
       "Bakeshop | NE Portland Retail and Wholesale Bakery" were reported as conflicting brands.
    4. **Every entity was a naming candidate**, so the web agency that built a bakery's site was
       read as a rival brand. A high-severity check failing correct markup, twice over.
    5. **Open Graph paid out three times** across 46 points of critical weight, which is the
       arithmetic that produced the fixed point.
  **Three design errors of my own, each caught by measuring rather than reasoning:**
    a. Excluding `publisher` as a third-party role deleted the site's OWN Organization, because
       every major CMS points an Article's publisher at it. A bakery lost its identity node
       entirely and dropped to 50.
    b. Matching the brand token anywhere in the path claimed `wiki.qt.io/Qt_for_Python` and a
       dated blog post as python.org's own profiles. Fixed by requiring a short path with the
       handle at the end; chrome membership then recovers the profiles whose handle differs from
       the domain (`@ThePSF`, `/gcbakery`), which a token rule alone cannot see.
    c. **Two of my eight mutations initially survived** — the share-widget and third-party-author
       tests passed with those rules removed, because on their fixtures another rule happened to
       cover the case. Same failure mode as the chrome-cap test last time. Rewrote both against
       discriminating shapes: a share widget in the FOOTER, and a third party that is an
       Organization rather than a Person.
  Results: coverage 0.56 -> **1.0** on all ten real sites, entity scores spread **39 to 94** and
  ordered by markup quality, with two small trade businesses at the top of the table above GitHub.
  eff.org 58.9 -> 66.0 (overall 78 -> 80); github.com 58.9 -> 56.0; python.org 58.9 -> 88.0.
  Six archetype fixtures added (`tests/fixtures/entity_*.html`): a trade business on an unlisted
  subtype, an agency-built site, a social-only site with mismatched handles, a share-widget-only
  page, a directory-listed clinic, and a Spanish bakery. They encode SHAPES, so a change that
  helps one site cannot silently break another — which was the explicit requirement.
  Test count 784 -> 821; `tests/test_entity_generalization.py` (37), all eight mutations caught.
- 2026-09-10 — **Three measurement defects fixed; two reported root causes did not survive
  checking** (from a dev.to audit returning five findings, three of them wrong).
  **Verified each claim before acting on it, and two were mistaken.**
    * *"`wait_until: load` snapshotted the page before React hydrated."* Measured:
      `domcontentloaded`, `load` and `networkidle` return a **byte-identical 263,269-character
      DOM** on dev.to, which is server-rendered Rails, not a hydrated SPA. The wait strategy was
      not involved. The hydrating-SPA fixture confirms the settle window still catches real
      hydration, so nothing was changed there.
    * *"Implement dependency suppression so `organization_declared` is suppressed when
      `structured_data_present` fails."* Measured: that costs **24 points of coverage to gain 5 of
      score** (66.0 at 1.0 coverage becomes 71.1 at 0.76), and it reinstates exactly the collateral
      damage removed on 2026-09-08, where one verified failure wiped 44% of the category. Not done.
      The real defect was different and is fixed below.
  **Three real defects, each found by measuring the page rather than reasoning about it:**
    1. **Geometry counted `<script>` and `<style>` as page text**, because a `SHOW_TEXT` TreeWalker
       returns their contents and they report `top = 0`. 3,301 of 6,214 above-the-fold characters
       were JavaScript source, so `orientation.value_proposition` was handed inline script instead
       of the hero copy and failed at `high`. Nodes with no layout box were counted for the same
       reason. Now excluded; dev.to's above-fold text drops 6,214 -> 2,913, all of it real copy.
    2. **"A digit in the filename" meant "this image carries a fact."** Asset fingerprinting makes
       that match nearly every modern site: 79 of 108 images on dev.to, 59 of them 18x18 reaction
       icons. Note the reported cause — "counts every image without an alt tag" — was also not
       right; the digit rule was. Fixed by excluding content hashes, UUIDs, bare ids and
       URL-encoded proxy paths, requiring a short number beside real words, and skipping images the
       page declares smaller than 64px. dev.to: 79/108 flagged -> 0/49, ai_discoverability 90 -> 100.
    3. **Severity ignored state.** A `partial` was reported at the check's full declared severity,
       which is what produced two `critical` findings for one root cause and reported a title two
       characters under the floor at `high`. Partials are now demoted one tier.
  **On the title threshold:** measured 11 real sites before touching it. Median length 50, and only
  dev.to falls under the 15-character floor. Lowering the floor would be fitting the tool to one
  site, which is the opposite of the standing instruction. The `partial` STATE was right; only the
  `high` severity was wrong, and demotion fixes it to `medium`.
  dev.to: 82 -> **87**, findings 5 -> 4, severity 2C/3H/0M -> 1C/1H/2M, all three false positives
  gone. eff.org 80 -> 83, github.com 85 -> 88. Every fixture score unchanged.
  Test count 821 -> 848; `tests/test_measurement_precision.py` (27), four mutations all caught.
- 2026-09-10 — **Migrated to a second development machine; phases 1-6 re-verified from a clean
  environment.** Branch `feature/devansh-singh`, sitting at `origin/main` after the phase 1-6 work was
  merged. No source changes; documentation and environment only.
  **Python 3.14 could not build the project and the pins were not relaxed.** The new machine shipped
  3.14 alone, which has no cp314 wheel for `lxml==5.3.0` or for `greenlet==3.0.3` (a `playwright==1.47.0`
  dependency), so `pip install -e .` would have fallen back to source builds. Checked with
  `pip download --only-binary=:all:` rather than by attempting the install and reading the wreckage.
  Installed Python **3.12.10** alongside and built `.venv` from it, so every pin resolves byte-identical
  to the first machine — the precondition for the recorded scores meaning anything.
  **A Chromium failure worth recording, because the error names the wrong culprit.** `chrome.exe` died
  with *"the side-by-side configuration is incorrect"*, which reads like a missing Visual C++
  redistributable. It was not: the redistributable was present, and the 345 MB install was complete and
  uncorrupted. The Windows event log named the real dependent assembly — Chromium's own private
  `129.0.6668.29` manifest — and the cause was **path redirection**. A packaged/containerised shell has
  its `%LOCALAPPDATA%` writes redirected into the package's `LocalCache`, and the loader cannot resolve a
  private side-by-side assembly through that redirection. Provisioning the browsers outside
  `AppData\Local` and pointing `PLAYWRIGHT_BROWSERS_PATH` at them fixes it. An ordinary terminal is
  unaffected. Recorded because the symptom would otherwise send the next person to reinstall a runtime
  that was never missing, and because a silent degrade to Tier B is exactly the failure this project
  spent 2026-09-10 hunting.
  **Verification: 848 passed, 13 skipped — the recorded count exactly.** `--ci` returns 0 on the healthy
  fixture and 1 on the SPA shell. `python.org` reproduces at **90**.
  **The score table in Current status was stale and has been re-measured.** It predated the two work
  items logged immediately above it, so `example.com` (62 -> 66, coverage 0.805 -> 0.975, 8 -> 12
  findings) and the deep-chrome fixture (0.68 -> 0.85 coverage, 2 -> 6 findings) had drifted without
  anyone re-running them. The movement is the entity generalization making previously-`unknown` checks
  measurable, which is what it was built to do. `python.org`'s row was the one the log had updated, and
  it was already correct.
  **A high score on very thin coverage is now a tracked Phase 8 item.** `spa_hydrating.html` returns
  80 at 0.175 coverage, with Human Orientation at 100.0 off a single surviving check. Scoring is
  behaving as specified; the problem is that a page which builds itself in the browser presents an 80
  as its headline. Filed as the SPA headline problem under Current status, with an acceptance
  criterion, and assigned to the Phase 8 output layer rather than to `_scoring.py` — putting a
  presentation fix inside the scoring spine would cost the determinism guarantee the whole project
  rests on. Issue #10 is updated to point at it.
  **One correction to the entry above, found on 2026-09-11 while regenerating `sample-report.json`.**
  I first reported this as a *drift*, 30 -> 80. It was not: I had measured `spa_hydrating.html`
  against a table row that has always meant `broken_page.html`, which still returns 30 / 0.175 / 2
  exactly and is the sample report's own source. The concern is real and stands; the drift was mine.
  Both fixtures are now named by filename in the table, because "SPA shell" was ambiguous between two
  files that legitimately score very differently.
- 2026-09-11 — **Phase 7 complete. Every finding now carries a fix and a way to check it.**
  Built `remediation-advisor`, the sixth skill and the only one that prescribes rather than measures:
  corrective snippets bound to failing checks, a validation procedure on all 24, and four proactive
  detectors for improvements where no defect was found. Test count 848 → **922**.
  **Two contradictions in PLAN.md had to be settled before any code was written**, both found by
  reading the plan against the registry rather than by implementing and discovering later.
    1. §5.2 justified the skill as consuming "findings, not the artifact", while §7 requires snippets
       filled from values OBSERVED on the page and every proactive example inspects the page. The
       distinction that actually justifies the skill is CONCERN, not inputs. It now reads the artifact
       as read-only evidence, emits no check state, and cannot move the score — the last asserted by
       running the same audit with the advisor present and absent.
    2. §7's second proactive example — an `Organization` with `sameAs` but no authority anchor — had
       become the scored check `entity.sameas_authority` in the meantime. Building it would have
       reported one root cause twice, which is the double-jeopardy defect fixed on 2026-09-10. Not
       built, and pinned by a test so a future reader working from §7's list does not re-add it. The
       "aim higher" variant was rejected too: the project already locked the finding that a local
       business will never have a Wikidata entry.
  **Three real defects found by running the detectors rather than reasoning about them:**
    1. **A number is not a claim.** The first claim pattern matched any multi-digit run, so
       python.org-style outline numbering — 1.1, 1.2, 1.3 — read as **107 factual claims** on the
       deep-chrome fixture. Exactly the family of the image-fingerprint defect: a permissive numeric
       pattern matches almost the whole web. Split into a language-neutral quantity pattern (currency,
       percentage, thousands separator) and a language-gated one that also accepts a number bound to a
       unit noun. 107 → 3.
    2. **A snippet that did not parse.** `sameas_authority` joined observed profile links with no
       trailing comma and then appended another array element, so any site that already had one
       profile link received JSON-LD that LOOKED complete and was invalid — the worst possible failure
       for something whose whole purpose is being pasted in unmodified. Fixed with a second
       substitution form, and snippets now declare a `snippet_json` shape that a test parses.
    3. **Suppression that asserted a redundancy it had not established.** Checking `suppressed_by`
       before running the detector was cheaper, but recorded "already reported as a finding on X" for
       detectors that had nothing to say — on four of five real sites sampled. Suppression now runs
       after the detector, so the word means one thing only.
  **Two of eight mutations survived the first run, both tests passing for the wrong reason** — the
  third time this project has hit that failure mode. One was silenced by a minimum-question-length
  filter rather than by the missing answer it claimed to test; the other used a two-claim sentence
  against a three-claim threshold, so it could not have fired with or without the date it was
  supposedly testing. Both rewritten with the confounding variable asserted away; all eight now caught.
  The harness lied once too: a mutation reported as surviving had been served from a stale `.pyc`,
  since a file written and reverted inside one filesystem timestamp tick defeats mtime invalidation.
  **A correction to my own 2026-09-10 entry, found while regenerating `sample-report.json`.** I had
  reported the SPA shell as drifting 30 → 80. It had not: the table row has always meant
  `broken_page.html`, which still returns 30 / 0.175 / 2 exactly and is the sample report's own
  source. I measured `spa_hydrating.html`, a different fixture that was never in the table. The
  underlying concern — a high headline on very thin coverage — is real and remains a Phase 8 item;
  the drift was mine. Both fixtures are now named by filename.
  Live verification: `example.com` 66 and `python.org` 90, **both unchanged by the advisor**, with a
  snippet and a validation procedure on every finding. Across five further live sites the detectors
  behaved: wikipedia.org and djangoproject.com each emit one proactive item, developer.mozilla.org
  correctly emits none. `sample-report.json` regenerated with score, coverage and findings
  byte-identical.
- 2026-09-11 — **Removed the one flaky test in the suite** (`test_deadline_marks_later_pages_skipped`),
  ahead of relying on this suite in CI. It failed roughly one run in six.
  **The cause was not CPU load, which is what it looked like.** `time.monotonic()` on Windows is
  `GetTickCount64()` with a resolution of **15.625 ms**, and the test set its deadline 1 ms in the
  future — an interval the clock cannot represent at all. Whether the budget had "expired" by the
  time the page loop ran came down to whether a 15.6 ms tick boundary happened to fall during the
  crawl. Confirmed by reading `time.get_clock_info('monotonic')` rather than inferring it, and by
  looping the real call 60 times in-process: 26 runs produced `('ok','ok','ok')` with nothing
  skipped, against 13 `('ok','skipped','skipped')` and 14 where acquisition itself died on the
  deadline.
  **The assertion was also hiding half the problem.** `assert "skipped" in statuses or
  len(pages) == 1` meant a run where the homepage fetch itself failed on the deadline counted as a
  pass while proving nothing about skipping — so the test could not distinguish "budget skipping
  works" from "acquisition collapsed".
  **Fixed by driving the clock, not by widening the margin.** A `SteppedClock` is installed as
  `_artifact`'s `time` attribute only, so `_safe_fetch` keeps the real clock and the fixture-server
  fetches get a full 30 real seconds; the offset jumps to 31 s the moment `select_pages` returns,
  which is immediately before the page loop. The loop-top check is therefore expired on every
  iteration on every machine, independent of speed, load and clock resolution. The escape hatch is
  gone: the test now asserts more than one candidate page, a successful homepage, and at least one
  later page skipped with `skip_reason == "budget"`.
  Added `test_pages_are_not_skipped_while_the_budget_is_intact` as the control, so a skip caused by
  anything other than the deadline cannot satisfy the first test.
  Verified: 15/15 clean runs of the file, then 10/10 with ten CPU-saturating processes running
  alongside. Three mutations — skipping disabled, deadline never expired, everything skipped
  regardless of budget — all caught. Test count 922 → **923**.
- 2026-09-11 — **Phase 7 close-out before starting Phase 8.** Two real gaps found by auditing the
  delivered work against PLAN §7 rather than against my own notes. Test count 923 → **927**.
    1. **The README had gone stale the moment the sixth skill landed.** Its table listed five skills
       while `marketplace.json` declared six, and it still announced remediation snippets as "still
       to come (Phase 7)" after they had shipped. Marketplace composition is a scored criterion and
       the README is what a reviewer reads first, so an undercounted marketplace is not cosmetic.
       Fixed, and pinned by two drift tests — one asserting every manifest skill appears in the
       README, one asserting a shipped phase is not still advertised as pending.
    2. **Half of all corrective actions named no target.** PLAN §7 requires "the exact target
       (`selector` / file location hint)". Measured across five fixtures: only **12 of 23** findings
       carried a selector. That is not an analyzer bug — roughly half of all findings are an
       ABSENCE, and a missing meta tag has no element to point at. Added a `target` hint to all 24
       corrective templates, carried through `advise.py`, both schemas and the orchestrator's merge,
       so every finding now answers "where do I make this change" even when no selector exists. The
       analyzer's `selector` still names the exact element when there is one; the two are
       complementary rather than alternatives.
  `sample-report.json` regenerated with targets attached; score, coverage and findings unchanged.
  Checked and found NOT to be Phase 7 gaps: `checks-reference.md` (PLAN lists it under HIGH ROI for
  Phase 10), the User-Agent contact URL (Phase 10), and issue #7's SSRF kill-switch, which is
  already guarded by `_assert_no_ssrf_bypass_via_cli` in the entrypoint.
- 2026-09-11 — **Phase 8 complete: the non-expert output layer, built into the report rather than a
  renderer.** Test count 927 → **965**.
  **The scope was decided by reading the brief, not by following the plan's assumption.** PLAN §14
  paired the output layer with `render_html.py`. The brief says it evaluates "the marketplace itself
  … **not any single report it happens to produce**", its Output-design row asks whether the
  entrypoint "**is built to emit**" a report a non-expert could act on, §2 requires "a **single**
  audit report (fixed schema)", and the schema is "a **floor, not a ceiling** — you may add fields".
  It never mentions HTML anywhere, and the submission is a zip plus a README. So the plain-language
  layer belongs in the emitted JSON, and `render_html.py` is deferred as unscored rather than built.
  **Delivered:** `_narrative.py` (pure, no I/O); `summary.verdict` and `summary.category_verdicts`
  for §9 layer 1; `next_actions[]` for layer 2, ordered by points recoverable and answering do /
  where / confirm / gain; `not_checked[]` naming what rested on too little evidence and why; and
  `summary.headline_reliable` + `headline_caveat` for §9.1.
  **Open issue #10 is finally closed.** Phase 6 made a category scoring 100.0 off one check
  *visible* via `category_coverage`; Phase 8 makes it *un-misreadable*. Below 0.5 coverage the
  sentence a reader acts on says the category could not be assessed — while the number stays in
  `category_scores`, because declining to headline a measurement is not the same as hiding it.
  **Three details that needed measuring rather than reasoning:**
    1. The gap reasons first read `suppressed_by_failed_prerequisite`, which is true and useless.
       §9 bans undefined jargon in layers 1 and 2, so the report now names the check that actually
       blocked them in its own failure wording: *content only appears after javascript runs*.
    2. The jargon rule is asserted as a property over the text the report ACTUALLY emits, not by
       reading the wording once and trusting it.
    3. **A fourth false-pass, caught by mutation.** `test_next_actions_is_ordered_by_what_the_fix_is
       _worth` passed with the points-recoverable sort key deleted, because on that fixture severity
       order and gain order coincide — one high at 4.5 then three mediums descending. It was
       measuring the fixture, not the rule. Replaced with a case where the two orderings disagree: a
       medium worth 20 points must outrank a critical worth 5.
  Six mutations applied, all caught. `example.com` 66 and `python.org` 90 — **both unchanged**,
  confirming the layer is presentation only. `sample-report.json` regenerated; score and coverage
  identical.
- 2026-09-11 — **`marketplace.json` now keys skills by `id`, not `name`.** The brief states that the
  manifest is its own convention and not part of the agentskills.io spec, so its published example
  is the ONLY specification this file has — and that example uses `id`. Skill-format hygiene is a
  scored row and a grader matching the documented shape would not have found `name`. Pinned by
  `test_the_manifest_matches_the_shape_the_brief_documents`, which also asserts each `id` equals its
  folder name, since the spec already requires SKILL.md `name` to match the folder.
- 2026-09-11 — **Phase 9 complete: precision and recall are now measured rather than asserted.**
  Test count 965 → **983**, and **0 skipped** for the first time.
  Built `tests/labeled_corpus.json` (10 fixtures), `tests/run_precision_recall.py` and
  `tests/test_corpus.py`, plus four new archetypes: an e-commerce product page, an image-heavy
  page, a div-soup page and an adversarial one. Labels are written from each fixture's
  CONSTRUCTION; a corpus recorded from the tool's own output would pass by definition.
  **The corpus found a real, high-severity false positive on its first run**, which is the whole
  reason for building it. `orientation.value_proposition` gated on 18 offering nouns and 36 action
  verbs, so it failed "Emergency lock repair across Leeds" and a product page stating its weight,
  fill and use case — while `example.com`, which offers nothing whatsoever, scored BETTER than the
  locksmith. The ranking was inverted. Third instance of the closed-word-list defect, after the SPA
  markers and the entity type allowlist, and PLAN §8 already forbids it.
  Rewritten around specificity: a concrete anchor is a figure with a unit or a proper noun used
  inside a sentence, and vague markers only subtract, so a missing phrase can never cause a failure.
  **Three defects in my own fix, all found by probing the branches rather than reasoning about
  them:** Title Case headings read as proper nouns so "Example Domain" looked specific; one brand
  name rescued a hero carrying seven filler phrases because the penalty subtracted rather than
  capped; and `example.com` still passed on the link label "Learn more" matching the verb "learn",
  so the vocabulary path now requires a verb AND a noun. Ecommerce 94 → 100, locksmith 87 → 93,
  every other fixture unchanged, all 454 analyzer tests still green including the Phase-5
  anti-gaming ones.
  **The corpus was mutation-tested, because one that passes first time and has never been seen to
  fail is indistinguishable from one that asserts nothing.** Three mutations, each aimed at a
  different column: a detector that never fires took recall to 87.5%, a check that always fails
  took precision to 95.2%, and a broken render check collapsed both. All caught. The sample-report
  sync test was mutated five more ways — score changed, finding removed, verdict reworded, snippet
  altered, extra page audited — and caught all five, which matters because its path normaliser had
  just been made more permissive to survive relative-versus-absolute paths.
  **Five stale Phase-1 stub files deleted.** Their skip reason still read "checks not implemented
  yet", four phases after it stopped being true, and it was the first thing a reviewer running
  `pytest` would see. Their three genuinely unasserted properties were absorbed rather than lost:
  the committed sample report is now checked against a fresh run — it had been regenerated by hand
  every phase, which is exactly the step that goes stale — two audits of one input are asserted
  byte-identical, and the broken page must score materially below the healthy one.
- 2026-09-11 — **Phase 10 complete. All ten phases done; 983 → 1024 tests, 0 skipped.**
  Every gate in PLAN.md's Verification section is now a test rather than a paragraph. All six skills
  pass the **agentskills.io reference validator**, which is pinned as `skills-ref==0.1.1` in the dev
  extra so CI runs it; the same rules are also asserted natively, so the guarantee survives the tool
  changing or going away. Live budget measured rather than claimed: `python.org` 41s and
  `djangoproject.com` 31s, both five-page audits, against a 5-minute limit.
  **README rewritten reviewer-first.** The brief requires a root README "describing what each skill
  does and how the entrypoint composes them", and ours listed the skills but never explained the
  composition. It now opens with a marketplace-composition table and a seven-step flow from safe
  fetch through the artifact, the four network-free analyzers, scoring, and the score-neutral
  advisor, to one JSON report on stdout. The existing setup, security, rendering and testing material
  is kept below it.
  **Two defects found by doing the work rather than assuming it was fine:**
    1. The README documented `skills-ref validate`. The installed command is `agentskills`. It had
       been wrong since Phase 1 and nothing caught it, because no test had ever run the command the
       docs told a reviewer to run. Now asserted.
    2. The legacy-name compliance scan **reported itself** — a guard that searches for a string
       necessarily contains it. The needle is assembled at runtime rather than excluded by filename,
       because an exclusion would hide a real hit if code were later added to that file.
  **Consolidated two duplicated README drift tests** out of `test_remediation_advisor.py`. Phase 10's
  replacements are strictly stronger: they check every skill by the `id` the manifest actually uses,
  also require the composition and entrypoint to be described, and cover Phases 7, 8 and 9 rather
  than Phase 7 alone. Two copies would have meant two places to update and one going stale, which is
  the exact failure that pair existed to prevent.
  Remaining work is optional and unscored: `render_html.py` (the brief never mentions HTML),
  `checks-reference.md`, the User-Agent contact URL, and reusing one browser across pages.
- 2026-09-12 — **Post-Phase-10 scope decisions, and the User-Agent contact URL closed.**
  Four optional items were reviewed. Three were **declined on analysis**: `render_html.py` (the brief
  never mentions HTML and no rubric row scores it), `checks-reference.md` (high ROI but not required,
  and another document to keep from drifting), and browser reuse (a performance optimisation against
  a budget we already clear at 37s of 300s, so it would buy nothing and risk the render architecture).
  **The contact URL was the one that turned out to matter.** Not because the brief requires it — it
  does not, and no rubric row scores it — but because the config beside it had been instructing a
  replacement "before any run against a third-party site" since Phase 2, and we had ignored that on
  six real sites. The value pointed at `example.com`, which IANA reserves for documentation.
  No correct replacement existed for me to supply: the repository is private, so its URL would both
  404 for an operator and leak the repo path to every audited host, and inventing one would breach
  the project's own never-invent-a-fact rule. Resolved by removing the claim rather than replacing
  it: `fetch.contact_url` is a separate field, empty by default, appended as ` (+URL)` only when set.
  The agent is now composed in a single function, `_safe_fetch.user_agent()`, because four call sites
  read the config field directly and the robots gate matches on the product token — two of those
  drifting apart would mean evaluating permission for one agent and sending another.
  Five guard tests added, including one asserting no reserved documentation domain can reappear.
  **My own composer guard cried wolf on its first run**, flagging `artifact.get("user_agent")`, which
  reads a value the composer already produced. Tightened to config reads only, and given a test
  proving it can still see a real one.
  Verified after the change: **1030 tests, 0 skipped**; 49 compliance tests; all six skills pass the
  reference validator; corpus recall and precision both 100%; a live `python.org` audit scores 94,
  validates against the schema, holds the mandated floor invariant, and completes in 37s of the
  300s budget with `CitelyAuditBot/0.1` on the wire.
- 2026-09-12 — **External validation against avpws.com: one defect fixed, one deliberately not.**
  Test count 1030 → **1032**.
  **Fixed — a missing h1 was charged twice.** `extraction.semantic_html` graded h1 count alongside
  landmarks while `content.heading_hierarchy` already owned it, so the same absence cost the site in
  both AI discoverability and AI comprehension. Suppression could never have caught this: it follows
  a dependency chain, and these are unrelated checks in different categories reading one signal. The
  landmark check now grades landmarks only. On avpws.com its measurement went from
  `landmarks=nav; h1_count=0` to `landmarks=nav`, and only the heading check still cites h1.
  **Not changed — whether `nav` alone should earn partial credit.** Surveyed 32 real sites across
  two batches. Nav-only appears **once** (avpws itself), the hypothesised pathology of content
  living inside the nav appears **zero** times, and even avpws carries just 4.5% of its text in nav.
  The obvious implementation is disproved outright: the highest nav shares belong to hubspot 62.8%,
  MDN 51.9% and w3.org 32%, every one of which publishes a proper `main` or `article`, so a
  nav-share rule would fire hardest on the best-structured sites in the corpus. Changing grading on
  a sample of one is exactly the fit-to-fixtures failure mode this project has punished four times.
  **Two corrections to my own earlier claims.** I said `fail` was unreachable for any site built
  this century — wrong: Hacker News, example.com and canva.com all reach it, because they carry no
  landmarks at all. And my first survey read **gzip bytes as text**, since urllib does not
  decompress and some servers gzip regardless of Accept-Encoding; it therefore reported python.org
  and Hacker News as having no landmarks and no h1. Re-run with decompression, python.org has
  `header,nav,footer,section` and five h1s — matching what our own tool reported all along.
  Verified: 1032 tests, corpus recall and precision still 100%, avpws.com still scores 91.
- 2026-09-12 — **Encoding boundaries made UTF-8 end to end** (reported as "the audit crashes on a
  Hindi site"). Treating that as a language problem would have produced a patch for Devanagari and
  left the defect in place: the cause is `subprocess.run(text=True)` decoding with the platform
  locale while we tell the child to WRITE utf-8, so it bites on any character outside cp1252.
  Two failure modes, and the quiet one is worse — silent mojibake in every non-ASCII evidence
  string, and a crash when a byte lands on one of the five cp1252 cannot map, which arrives
  indirectly as `len(None)` after the audit has done all its work. All child reads now share
  `CHILD_TEXT`; the report write and the child-stderr forward are hardened the same way.
  `tests/test_encoding_boundary.py` drives real subprocesses and real streams, because a mock
  would have agreed with the broken code. Committed as `796e6e0`.
- 2026-09-12 — **The stderr hardening was written and then not wired in.** Found by auditing the
  commit above against its own claim rather than against its test run. `forward_child_stderr` and
  `_write_safely` existed, were unit-tested and passed — while `run_analyzer` and `run_advisor`
  each kept their own copy of the old loop, forwarding through a bare `sys.stderr.write`, which is
  the exact call that raises on a cp1252 console. So the locked decision above described a
  protection the audit did not have.
  **Confirmed by driving the real call sites before changing anything**, since the helper passing
  its own test proves only that the helper works: with a child emitting Devanagari on stderr and a
  strict cp1252 parent stream, both functions raised `UnicodeEncodeError`. Both now route through
  the one implementation, which also gives the advisor the truncation notice only the analyzer had.
  **The gap was invisible to a green suite**, which is the point worth recording: the existing
  source guard asserted `CHILD_TEXT` reached both subprocess calls and said nothing about what
  happened to the decoded text afterwards. Pinned two ways — a behavioural test per call site, and
  a source guard requiring both to forward through the helper with no caller keeping a private
  copy. Mutation-verified: reverting one call site fails both new tests, and the untouched call
  site's case still passes, so the test discriminates between them rather than firing on any edit.
  Verified: **1055 tests, 0 skipped** — 1052 plus exactly the three added, so nothing else moved.
  **Fixed two stale lines in this file's own run instructions while here**, both of which Phase 10
  had already corrected in the README and pinned with a test that only ever read the README: the
  validator was documented as `skills-ref validate`, a command that does not exist, and annotated
  "not installed yet" when it has been pinned in the dev extra since Phase 10. Re-run to confirm
  rather than assumed — all six skills report `Valid skill` under `agentskills validate`.
- 2026-09-12 — **Adversarial validation against `digital.go.jp` (Japanese): two confirmed general
  bugs fixed, two calibration defects proven and deliberately left open.**
  The report under test scored 82 with six findings. Three were investigated against fresh evidence
  fetched independently of the tool; two were false positives caused by defects that have nothing to
  do with this site, and one is real but unfixable without data we do not have.
  **Fixed 1 — a name could not match an identical copy of itself outside the Latin alphabet.**
  `normalize_name` stripped `[^a-z0-9]`, so `デジタル庁` normalized to `""`. The report claimed the
  name "matches neither the page title nor the domain" while quoting those same five characters as
  the page title two findings earlier — a self-contradiction on an observable fact. Verified in the
  raw HTML: `<title>` and `og:site_name` are byte-identical. Reproduced in Japanese, Chinese,
  Korean, Greek, Cyrillic, Arabic and Devanagari, so it is every non-Latin script rather than one
  page. `entity.name_consistency` partial → pass.
  **Fixed 2 — a base64 payload read as a chart filename.** `urlparse(src).path.rsplit("/")` on a
  `data:` URL returns a slice of its own payload, and base64's alphabet is exactly what the
  fact-filename rule wants: letters for words, digits for a figure. The evidence string printed in
  the report was literally `UEh4tn8UsNoAAAAASUVORK5CYII=`. Generalization measured rather than
  argued: **420 of 500 random inline PNGs** were flagged. `extraction.facts_not_image_only`
  partial → pass, and every designed detection still fires.
  **Not fixed — the character-count bands (open issue #11).** Proven miscalibrated for logographic
  scripts: 6 of 9 CJK homepages surveyed take a false title partial, and `jma.go.jp` passes with
  `気象庁 Japan Meteorological Agency` while its own Japanese name alone would not. No remedy was
  available that is not a constant fitted to a 9-site sample, so the threshold was left untouched
  and the evidence recorded instead. **Not fixed — `authority_domains` (open issue #12)**, which
  grades a self-managed LinkedIn page as a third-party record against its own check's definition.
  **Checked and found NOT to be bugs**, each verified rather than assumed: `access.ai_crawlers_allowed`
  passing is correct — the live `robots.txt` names no AI crawler and disallows only `/core/`,
  `/profiles/` and `/README.md`; `detect_faq_schema` is already punctuation-based and handles the
  full-width `？`, so it is not English-gated; and the markdown-looking `site` value is the report
  reader's rendering, not the emitted field, which is the raw URL.
  The three surviving entity findings are all TRUE positives: the page genuinely publishes no
  JSON-LD and no microdata, declares identity only through Open Graph, and declares no `sameAs`.
  Verified end to end: `digital.go.jp` **82 → 85**, findings **6 → 4**, ai_discoverability
  **95.0 → 100.0**, entity_trust **50.0 → 56.0**, and **coverage unchanged at 0.848** — the fixes
  removed two false findings without hiding a single measurement. The title finding remains, as
  intended, because its threshold was left alone. Cross-site regression: `python.org` reproduces
  **94 / 0.975 / 3** exactly, and every corpus fixture holds its recorded score with recall and
  precision still 100% — `healthy_page.html` and `archetype_adversarial.html` both still 99, which
  is what proves the Unicode change did not move Latin-script grading.
  Test count 1055 → **1107**, 0 skipped. Three mutations applied: reverting the normalizer fails 8
  tests, reverting the image call site fails 2, removing the helper guard fails 24.
  **One of my own tests failed first and the fix was right, not the test.** I asserted `fail` for 4
  charts among 10 images; that is ratio 0.40 against a 0.50 cap, so `partial` was correct. Replaced
  with a case above the cap, and a separate test now pins where an unjudgeable image lands in the
  ratio — denominator yes, numerator no — so the dilution is asserted rather than assumed.
- 2026-09-12 — **Pre-submission audit against the judging rubric: three blockers fixed.** All three
  were found by auditing the SUBMISSION rather than the code, and none would have been caught by a
  green test run, because none of them is a logic defect.
  **1. Windows cache files were committed and shipped in the archive.** Four `.db` files sat under a
  literal `%SystemDrive%` directory at the repo root, created once by a command run with an
  unexpanded variable and committed at `c4eecbe`. Being tracked, they shipped even from
  `git archive`. Not cosmetic: that path was the LONGEST entry in the archive at 116 characters, and
  extracting the zip into a deep-but-legal destination failed outright with `FileNotFoundError`.
  **Isolated before fixing rather than assumed** — extraction at a short root succeeded, so the
  cause is the Windows path limit, not the braces in the filenames. Untracked, deleted, and
  `.gitignore` now rejects that tree and three sibling unexpanded-variable names. Verified by
  recreating the directory and confirming git reports nothing. Archive: 102 entries → **93**, junk
  **9 → 0**, longest path **116 → 75** characters, and the extraction that failed now succeeds.
  **2. The declared Python range promised interpreters the pins cannot resolve.** `requires-python`
  was `>=3.11`, and all six SKILL.md files said "Requires Python 3.11+". Checked with
  `pip download --only-binary=:all:` rather than trusting either document: `greenlet==3.0.3`
  publishes no wheel for **3.13**, not 3.14 as the README claimed, and `lxml==5.3.0` none for 3.14.
  A reviewer on 3.13 — current stable — would silently fall back to a source build needing a C
  toolchain. Now `>=3.11,<3.13`, so pip refuses up front and says why; README and all six
  compatibility strings corrected. The pins were NOT relaxed: they are what make the recorded
  scores reproducible.
  **3. `--allow-external` was a documented capability that did nothing.** It reached
  `diagnostics.external_lookup` and stopped there, while `external_corroboration` was hardcoded
  `None` and no Wikidata request existed anywhere in the tree. Four shipped artifacts described it
  as working — the entity SKILL.md compatibility line and body, the orchestrator's help text, the
  marketplace manifest, and the crawl-artifact schema — while PLAN §14 lists it under NICE TO HAVE,
  i.e. never built. Same defect class as the `skills-ref` command that did not exist and the
  User-Agent contact URL, both fixed earlier for the same reason: a claim a reader will trust and
  the code cannot honour. The flag is removed and now rejected explicitly; every claim is corrected;
  the dead `external_lookup` block in `scoring-config.json`, read by nothing, is deleted.
  `diagnostics.external_lookup` stays as a constant `false` disclosing that all evidence is on-page.
  **The pinned validator earned its keep again.** My first rewrite of the entity compatibility line
  contained an unquoted `": "`, which YAML reads as a mapping. `agentskills validate` failed the
  skill immediately; a text-only review would have shipped a skill whose frontmatter does not parse.
  Reworded, and all six validate again.
- 2026-09-13 — **Brotli decoding added; malformed compression now reported as what it is.**
  A live CDN answered in `br` even when asked for `identity`, so berkshirehathaway.com could not be
  audited at all: 0 score, 0.0 coverage, one fetch error. Nothing in the analyzers or scoring moved.
  **Supported now:** gzip, x-gzip and deflate natively, plus `br` through `Brotli==1.2.0`, picked up
  by urllib3 automatically. The pin is exact because 1.2.0 is the first release whose decompressor
  takes an output limit; with anything older urllib3 decodes Brotli unbounded and only warns.
  Wheels verified for Python 3.11 and 3.12 on Windows, Linux x86_64 and macOS before pinning.
  **Not added: zstd.** It would need `backports.zstd` on our Python range, 0 of 18 sites probed
  sent it, and we never advertise it. It stays refused by the existing guard.
  **Kept unchanged:** `Accept-Encoding: gzip, deflate`. We decode `br` when a server sends it
  unasked but do not request it, so every compliant server keeps its current wire path.
  **A second defect fixed on the way.** requests files a decode failure under `RequestException`,
  so a corrupt gzip or br body was reported as `error_kind="network"`. Now a
  `MalformedContentEncodingError`, subclassing the undecodable-encoding error, so it maps to
  `content_encoding` with no change to the artifact or report schema.
  **My own wrong test.** I first asserted a Brotli call limited to 4096 bytes returns at most 4096.
  Measured: Brotli rounds the limit up to its own block and returned 32,752, while an unlimited call
  returned all 1,000,000. The bound is real; the exact size is not. The test now asserts the bound.
  **Mutation run nearly lied.** The first attempt applied only 1 of 4 mutations: `core.autocrlf` is
  true, so the working copy is CRLF and the newline anchors matched nothing. The script now adapts
  anchors to the file ending and exits non-zero on any mutation it cannot apply. All 4 caught.
  **Live:** berkshirehathaway.com 0 / 0.00 / 0 findings became 53 / 0.82 / 11, five pages read, and
  all eleven findings checked against the decoded page. A 60 MB Brotli bomb (11 KB on the wire) is
  stopped by the existing cap at 1.2 MB peak memory.
  **Open, deliberately not fixed:** a truncated compressed stream is accepted silently. A truncated
  gzip body returned 34 bytes of partial HTML, a truncated br body returned an empty body, both with
  no error. urllib3 exposes no public signal that a stream ended early, and detecting it would mean
  owning the decode loop, which is out of scope. Pre-existing for gzip.
  Verified: 1142 tests, 0 failed; corpus recall and precision still 100% with every score unchanged;
  all six skills pass the reference validator.
