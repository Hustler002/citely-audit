# Citely — Brand AI-Readiness Audit

An Agent Skill Marketplace (`agentskills.io`-compliant) that audits an arbitrary, unseen website and
diagnoses two things: **off-site AI discoverability** — why AI assistants fail to reach, read,
corroborate or cite the brand's facts — and **on-site engagement** — why visitors who arrive from an
AI answer leave again. It runs entirely read-only and emits a single evidence-backed JSON report of
findings plus prioritized, copy-pasteable fixes.

**No language model is involved anywhere in the fetch, analysis or scoring path.** That is what makes
the same page produce the same report every time, makes every number traceable to a measurement, and
makes the audit immune to instructions hidden in the page it is reading.

---

## Marketplace composition

Six skills, one entrypoint. The decomposition follows the **five failure mechanics** the audit is
built on, not a wish to have more folders: each analyzer owns one mechanic and one method, so none of
them can be folded into another without mixing concerns.

| Skill | Concern | Method | Network |
|---|---|---|---|
| **`audit-orchestrator`** — *entrypoint* | Acquisition, composition, scoring, report emission | Safe fetch + render, subprocess fan-out, pure scoring | **All of it** |
| `crawl-render-extraction-audit` | Mechanic 1 — can a machine reach and parse the page? | Raw-vs-rendered DOM diff | None |
| `quotability-density-audit` | Mechanics 2 + 3 — is the content citable, and does it survive summarizing? | Text and sentence analysis | None |
| `entity-corroboration-audit` | Mechanic 4 — is the brand's identity unambiguous? | Structured-data graph analysis | None |
| `engagement-orientation-audit` | Mechanic 5 — can a human orient in the first viewport? | Viewport geometry and DOM position | None |
| `remediation-advisor` | **Prescription, not detection** — what to change and how to confirm it | Template synthesis from observed values | None |

Mechanics 2 and 3 share one skill because they share a method: both are pure text analysis over
content already harvested, so splitting them would mean two skills doing the same thing to the same
bytes.

### How the entrypoint composes them

```
        ┌─ audit-orchestrator (the ONLY skill that touches the network) ──────────────┐
        │                                                                             │
  URL ──┼─▶ 1. safe fetch ──▶ 2. render ──▶ 3. crawl artifact                         │
        │      robots gate       Tier A/B      one normalized JSON snapshot            │
        │      SSRF guard                      of raw HTML, rendered DOM, geometry     │
        │                                              │                              │
        │      ┌───────────────────────────────────────┴───────────────────┐          │
        │      ▼               ▼                  ▼                    ▼   │ 4. run as│
        │  crawl-render-  quotability-      entity-           engagement-  │ separate │
        │  extraction      density          corroboration     orientation  │ processes│
        │      │               │                  │                    │   │          │
        │      └───────────────┴─── check states ─┴────────────────────┘   │          │
        │                              │                                              │
        │                              ▼                                              │
        │                        5. scoring ──▶ findings, scores, coverage            │
        │                              │                                              │
        │                              ▼                                              │
        │                    6. remediation-advisor ──▶ snippets + proactive advice    │
        │                              │                                              │
        │                              ▼                                              │
        └──────────────────── 7. single JSON report on stdout ────────────────────────┘
```

1. **Safe fetch.** The orchestrator checks `robots.txt` and fetches the homepage behind an SSRF
   guard. If the audit's own user agent is disallowed, it stops and reports that as the finding
   rather than proceeding quietly.
2. **Render once.** Tier A is a real Chromium render when one is available; Tier B is a browserless
   heuristic when it is not. Which tier ran is disclosed in `diagnostics.render_mode`.
3. **One crawl artifact.** Raw HTML, rendered DOM, geometry and page metadata are written to a single
   normalized snapshot. This is the only thing the analyzers ever see.
4. **Four analysis skills, as separate processes.** Each reads the artifact and prints **check
   states** — not findings — so report wording lives in one place and cannot drift between skills.
   They perform **no network I/O whatsoever**, which is what makes them deterministic and replayable.
   A crashed or slow analyzer degrades its own checks to `unknown` and is recorded; it never takes
   the audit down.
5. **Scoring.** Check states become category scores and one overall score, with dependency
   suppression so a single root cause does not produce a dozen downstream false positives.
6. **Remediation advice.** `remediation-advisor` runs **after** scoring and only ever *adds* to
   findings that already exist. It cannot change a score, a count or a severity — a property asserted
   end to end by running the same audit with the advisor present and absent.
7. **One report.** The orchestrator validates the assembled report against its schema and prints it
   to stdout. Nothing else is ever written there.

---

## Run

```bash
# Online: audit a live site
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com

# Offline: audit a local HTML file with zero network access
python skills/audit-orchestrator/scripts/run_audit.py --html-file tests/fixtures/healthy_page.html

# CI: exit non-zero when any critical finding is present
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com --ci
```

The report goes to **stdout only**; all logs go to stderr, so `> report.json` always yields valid
JSON. Every analysis skill is independently runnable too, which is what makes them skills rather than
modules of one program:

```bash
python skills/entity-corroboration-audit/scripts/entity_corroboration.py --html-file page.html
python skills/remediation-advisor/scripts/advise.py --html-file page.html
```

## Setup

```bash
pip install -e ".[dev]"
# Provision Chromium ONCE, never inside a timed run. Optional — the audit degrades to Tier B without it.
python -m playwright install chromium
```

Python 3.12 is recommended: the pinned `lxml` and `greenlet` publish no wheels for 3.14.

---

## Reading the report

Written to be acted on by someone who is not an SEO engineer, at three depths:

1. `summary.verdict` and `summary.category_verdicts` — one plain sentence each, no jargon.
2. `next_actions[]` — the same findings ordered by the score each fix recovers, each saying what to
   do, where to do it, and how to confirm it worked.
3. `findings[]` — the measurement, threshold, selector and confidence behind every verdict. Present,
   but never the headline.

**A score built on too little evidence is not presented as a verdict.** When under half the check
weight could be measured — a page that assembles itself in the browser, say — `summary.headline_reliable`
is `false`, the verdict says the site could not be measured, and `not_checked[]` names each gap and
its cause in plain words. The numbers stay in `summary`, because declining to headline a measurement
is not the same as hiding it.

### Suggested actions

Every finding carries a `suggested_action` with a plain-language summary, the **target** (where to
make the change), a **validation procedure** (how to confirm it worked), and — where pasting markup
can actually fix the problem — a **copy-paste snippet**. Snippets are filled only from values
**observed on the page**; anything the page never stated stays a literal `{{PLACEHOLDER}}` and is
listed in `placeholders_remaining`, so the report never invents a business fact.

Beyond-problem suggestions live in a separate top-level `recommendations[]`. They are **not**
findings: they are excluded from the summary counts and from scoring, so they can neither inflate nor
deflate the score, and one is never emitted when the check it relates to already produced a finding.

---

## Security posture

- Read-only; no auth, no writes, no site-altering actions; respects `robots.txt` (stops if the audit
  user agent is disallowed on root).
- SSRF guard: `http`/`https` only; rejects private, loopback, link-local, reserved and metadata IPs;
  pins the resolved IP; re-validates on every redirect; caps redirect hops; per-request timeouts and
  maximum body size with a decompression-bomb cap.
- Analyzer subprocesses run with an explicit environment allowlist, so none of the parent's tokens
  are inherited.
- Every fetched byte is treated as hostile. Page-derived text reaches the report only sanitized,
  truncated and labelled as evidence, never as instructions.

## Performance budget

Under **5 minutes** for a typical site. Measured: `python.org` 41s and `djangoproject.com` 31s, both
five-page audits. A global monotonic deadline (default 270s) skips lower-priority work under pressure
and marks the report `partial: true` with a reason rather than silently truncating.

## Rendering strategy (tiered)

- **Tier A** — Playwright render diff when Chromium is available. Navigation waits for `load`, never
  for `networkidle`: that state needs 500ms with almost nothing in flight, which analytics beacons,
  chat widgets and long-polling never allow, so it does not fire on most real sites. Quiescence is
  pursued afterwards under its own small budget, and a navigation timeout **salvages** the rendered
  DOM rather than discarding it. `diagnostics.render_nav_state` reports `ok`, `busy` or `salvaged`.
- **Tier B** — browserless SPA heuristic (framework markers, empty-shell mount node, script-to-text
  ratio) when it is not; findings labelled `heuristic` and `diagnostics.render_mode = "heuristic"`.
  Above-the-fold checks fall back to a DOM-order proxy budgeted in **visible text**, with
  non-rendering subtrees pruned and navigation chrome charged at a capped discount, so a mega-menu or
  an inline SVG sprite cannot push the real content out of the measured window.

---

## Testing and validation

```bash
pytest                                                  # full suite
python tests/run_precision_recall.py                    # precision/recall against the labelled corpus
for s in skills/*/; do agentskills validate "$s"; done  # agentskills.io compliance, all six skills
```

`tests/labeled_corpus.json` labels ten fixtures — static, hydrating and dead JavaScript shells,
e-commerce, image-heavy, div soup, non-English, chrome-heavy corporate, a small trade business, and
an adversarial page — with the checks each one MUST and must NOT be reported for, plus an expected
score band. Labels come from how each fixture is built, never from what the tool outputs, and the
adversarial fixture is a structural clone of the healthy one, so any difference in its report is
attributable to its injected instructions and nothing else.

Generalization is built in rather than tested afterwards: language-dependent checks resolve to
`unknown` on a page whose language cannot be confirmed, never to `fail`, so a non-English site shows
its gap as reduced coverage instead of a wall of false positives.

## Layout

| Path | Purpose |
|------|---------|
| `marketplace.json` | Manifest declaring all six skills; exactly one `entrypoint: true`. |
| `config/checks.json` | The check registry — 24 checks, six per category, with weights, severities, report wording and category verdicts. |
| `config/scoring-config.json` | Severity thresholds, score weights, budgets and fetch limits. |
| `skills/` | The six skill folders, each independently `agentskills.io`-valid. |
| `tests/` | Fixture archetypes, a localhost fixture server, the labelled corpus, and the suite. |

## Bespoke convention (not base spec)

`marketplace.json` and the `entrypoint` flag are **this brief's own lightweight convention**. The base
`agentskills.io` spec defines only the per-skill folder plus `SKILL.md` (requiring `name` and
`description`); it has no marketplace or entrypoint concept. Skills are keyed by `id`, matching the
example the brief publishes, and each `id` equals its folder name and its `SKILL.md` `name`.
