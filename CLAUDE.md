# CLAUDE.md — Project State & Handoff

> Purpose: single source of truth for a **new chat session** to resume this project without re-deriving
> context. Update the "Progress log" and "Current status" sections as work advances.

## What this project is

**Brand AI-Readiness Audit** — an Agent Skill Marketplace (`agentskills.io`-compliant) that audits an
arbitrary website and diagnoses (1) **off-site AI discoverability** (why AI assistants fail to crawl /
extract / corroborate / cite the brand's facts) and (2) **on-site engagement** (why AI-referred visitors
bounce). Runs read-only, non-destructive, targets a **< 5-minute** budget, and emits **one JSON report**.

Built around the 5 failure mechanics: (1) crawl/render/extraction funnel, (2) RAG quotability,
(3) information density / summarizer survival, (4) entity disambiguation & cross-web corroboration,
(5) first-viewport orientation & retention.

## Key documents (read these first)
- `../CONTEXT.md` (user's Downloads, i.e. `C:\Users\PRIYANSHU PAL\Downloads\CONTEXT.md`) — original brief + handoff.
- Approved plan: `C:\Users\PRIYANSHU PAL\.claude\plans\c-users-priyanshu-pal-downloads-context-whimsical-pike.md`
  — the production-ready plan with the full list of mistakes found in the original and the corrected design.
- `README.md` — usage, security posture, setup.

## Locked decisions (do not re-litigate)
- **Orchestration** = subprocess + JSON file contract. Orchestrator owns ALL network I/O; the 3 analysis
  sub-skills are pure, network-free consumers of a shared crawl artifact.
- **Rendering** = tiered. Tier A = Playwright render diff; Tier B = browserless SPA heuristic when no
  browser; hybrid warning finding + `diagnostics.render_mode`. Chromium is provisioned as a SETUP step,
  never inside the timed run.
- **Page scope** = homepage-only for v1 (clean extension point left for multi-page).
- **Report additions adopted**: `discoverability_score` (0–100), `diagnostics` block, `partial`+reason,
  `sample-report.json`, `suggested_action.snippet`.
- **Severity** = strict 3-tier (`critical`/`high`/`medium`), no `low`. Invariant enforced in code:
  `total_findings == critical + high + medium`.
- **Confidence** = `verified` (directly observed) vs `heuristic` (inference, the default).
- **Wikidata lookup** = the ONE sanctioned network exception; off by default, `--allow-external` only,
  soft-fail, findings labeled `heuristic`.
- Each skill is a self-contained folder (agentskills.io rule) → scripts embed their own config defaults
  rather than importing a shared module; drift is caught by tests, not prevented by imports.

## Verified spec facts (agentskills.io)
- Skill = folder + `SKILL.md`; required frontmatter is only `name` + `description`. Optional: `license`,
  `compatibility` (≤500 chars, for env needs), `metadata` (string→string), `allowed-tools` (experimental).
- `name`: ≤64 chars, lowercase alnum + single hyphens, no leading/trailing/consecutive hyphens, MUST match
  folder name.
- Body recommended < 5000 tokens / < 500 lines; push detail to `references/`.
- Base spec has **no** `marketplace.json` / `entrypoint` concept — those are this project's bespoke convention.
- Official validator: `skills-ref validate ./<skill>` (from github.com/agentskills/agentskills).

## Current status: ARCHITECTURE SCAFFOLD COMPLETE — logic NOT implemented

Directory structure, manifest, both JSON Schemas, config, all 5 SKILL.md files, fixtures, fixture server,
README, and CLAUDE.md are in place. All Python check/fetch/orchestration bodies are `TODO` /
`NotImplementedError` skeletons with the CLI + I/O contract wired. Test files are `pytest.mark.skip` stubs.

### Done
- [x] Full tree per plan §2 (27 files).
- [x] `marketplace.json` (one `entrypoint: true`), `pyproject.toml` (pinned deps, Py 3.11+).
- [x] Contracts: `report-schema.json` (superset floor), `crawl-artifact-schema.json`.
- [x] `config/scoring-config.json` (placeholder thresholds/weights), `severity-rubric.md`.
- [x] 5 SKILL.md (valid frontmatter, `compatibility` where relevant).
- [x] Script skeletons: `run_audit.py`, `_safe_fetch.py`, + 4 sub-skill scripts (dual-mode CLI, defaults, stubs).
- [x] Fixtures (healthy SSR / broken CSR), `fixture_server.py` skeleton, placeholder `sample-report.json`.

### Not yet done (next implementation steps, in order)
- [ ] `_safe_fetch.py`: SSRF guard (`validate_url`, `is_safe_ip`), `safe_get` redirect loop, `check_robots`.
- [ ] `run_audit.py`: `build_crawl_artifact` (fetch+render, Playwright probe + Tier-B fallback),
      `run_consumer` (subprocess), `assign_ids_and_summarize` (deterministic sort + score), `validate_report`.
- [ ] 4 sub-skill check bodies (crawl/render/extraction, quotability/density, entity, engagement).
- [ ] `fixture_server.py` routes; un-skip + implement all tests; regenerate `sample-report.json` from E2E.
- [ ] Tune thresholds/weights in `scoring-config.json` against fixtures.
- [ ] Run `skills-ref validate` on every skill folder.

## How to run / verify (once implemented)
```bash
pip install -e .
python -m playwright install chromium            # setup step, optional (Tier A)
python skills/audit-orchestrator/scripts/run_audit.py --url https://example.com     # online
python skills/audit-orchestrator/scripts/run_audit.py --html-file tests/fixtures/healthy_page.html  # offline
pytest
skills-ref validate ./skills/audit-orchestrator  # repeat per skill
```
Report goes to **stdout only**; logs to **stderr**.

## Conventions & guardrails
- Read-only; respect robots.txt (stop if audit UA disallowed on root → top finding).
- Never crash: every stage records failures into `diagnostics.errors[]` and still emits a schema-valid report.
- Global monotonic deadline (default 270s); per-stage soft timeouts; on pressure set `partial: true`.
- User-Agent: `BrandAIReadinessAuditBot/0.1 (+https://example.com/audit-bot)` (contact URL TODO).
- Partial finding shape (sub-skill output, no id): `{title, severity, category, confidence, evidence,
  suggested_action:{summary, priority, snippet?}}`. Orchestrator assigns `F-001…`.

## Progress log
- 2026-09-01 — Reviewed original plan, found 30 mistakes/gaps, wrote corrected production plan (approved).
- 2026-09-01 — Scaffolded full directory structure + architecture (contracts, config, SKILL.md, skeletons, tests).
