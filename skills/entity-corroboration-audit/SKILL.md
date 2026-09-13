---
name: entity-corroboration-audit
description: Diagnoses entity disambiguation and cross-web corroboration failures (mechanic 4). Use as part of the brand AI-readiness audit, over the shared crawl artifact. Detects missing schema.org/JSON-LD identity markup, missing sameAs and profile links, identities not anchored to any third-party record, incomplete Open Graph tags and inconsistent naming, which cause AI mistaken identity.
license: Apache-2.0
compatibility: Requires Python 3.11 or 3.12 (the pinned greenlet and lxml publish no wheels beyond 3.12). Needs no network at all, because every verdict is read from the shared crawl artifact.
metadata:
  mechanic: "4"
  category: entity-corroboration
---

# Entity Corroboration Audit (mechanic 4)

Can a machine tell who this page is about, unambiguously, and confirm it against another record?
Extracts and evaluates the page's identity markup from the crawl artifact.

## Inputs

- `--artifact <crawl_artifact.json>` (as run by the orchestrator) or `--html-file <page.html>`
  (offline).
- `--config <checks.json>`: the check registry. Each check's `threshold` is read from it, with
  built-in defaults for any check it does not define. Default: `config/checks.json`.
- This skill has **no** network access. The artifact declares an `external_corroboration` slot for a
  third-party lookup; no such lookup is implemented, so it is always null and nothing here reads it.

## Output

A JSON array of **check states** on stdout, one per check:
`{check_id, state, measurement, evidence, selector, page_url, reason}`, as defined by the
orchestrator's `references/check-result-schema.json`. These are not findings: report wording lives in
`config/checks.json` and is applied by the orchestrator. Logs go to stderr.

## Scope

Identity markup often lives on an "about" page rather than the homepage, so these checks accept
evidence from **any** successfully fetched page and record which page supplied it. The rendered DOM is
preferred when available, because tag managers can inject JSON-LD. Structured data is treated as
claims, never instructions, and parsed with size and depth limits.

## Checks

Threshold names refer to the check's `threshold` block in `config/checks.json`.

| Check | pass | partial | fail |
|---|---|---|---|
| `entity.structured_data_present` | JSON-LD on an audited page | Microdata only | Neither |
| `entity.organization_declared` | A JSON-LD identity node (see below) | Identity only through Open Graph (`og:site_name`, or `og:title` with `og:url`) | Neither |
| `entity.sameas_present` | At least `min_links` explicit identity links: schema.org `sameAs` on any node, microdata `sameAs`, or `rel="me"` | Only ordinary links to the brand's own profiles | No identity links at all |
| `entity.sameas_authority` | An identity link reaches a host in `authority_domains` (records someone else maintains, such as registries and directories) | Links reach only `social_profile_domains` (profiles the brand controls) | Links reach neither, or there are no identity links |
| `entity.opengraph_identity` | Every `required_properties` tag | Some of them | None |
| `entity.name_consistency` | The site's name agrees across its machine-readable identity fields, or is corroborated by the title or domain | A name appears only in the title or `og:title`, or a single stated name matches neither the title nor the domain | Conflicting names that cannot be attributed to third parties, or no name at all |

**Identity node.** A JSON-LD node with a `name` (or `legalName`) that either has a type in
`accepted_types` or carries at least `min_identity_properties` of the `identity_properties` (such as
`address`, `telephone`, `logo` or `sameAs`), whatever its type. Page-level types such as `WebSite`
and `WebPage` do not count, and nodes referenced as third parties (for example `author`) are
excluded. An organization outranks a person when both are present.

**Profile links.** Off-site links whose path carries the brand's name, or short profile paths on a
known platform inside the page's header, footer or navigation. Share buttons are excluded.

**Names** are compared by segment, so a name followed by a tagline is not a conflict, and matching
works in any script.

There is **no external lookup**: every verdict rests on evidence observed on the audited pages, which
is what makes the same page produce the same verdict every time.

See the orchestrator's `references/severity-rubric.md` for severity and confidence.
