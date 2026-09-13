# Sample reports

Five audits of real, public websites, chosen to span very different shapes rather than to produce
particular scores. Each `<site>.json` is the unedited report the entrypoint wrote to stdout; each
`<site>.stderr.txt` is the log it wrote to stderr during the same run.

Reproduce any of them with:

```bash
python skills/audit-orchestrator/scripts/run_audit.py --url <url>
```

| Report | Site | Why it is here | Score | Coverage | Findings |
|---|---|---|---|---|---|
| `stripe.com.json` | stripe.com | Highly structured: a full schema.org `Organization` with address, contact point and logo | 97 | 1.00 | 2 |
| `bundesregierung.de.json` | bundesregierung.de | A large **German** site, so the language-dependent checks must withhold judgement instead of guessing | 90 | 0.85 | 4 |
| `craigslist.org.json` | craigslist.org | Average and mixed: enormously useful, minimal markup, no declared language | 69 | 0.82 | 8 |
| `spacejam.com-1996.json` | spacejam.com/1996 | The 1996 original, preserved: table layout, no headings, no metadata | 51 | 0.85 | 12 |
| `berkshirehathaway.com.json` | berkshirehathaway.com | A server that answers in **Brotli it was never asked for**, and a famously bare page | 53 | 0.82 | 11 |

Every run finished far inside the five-minute budget; the slowest was 82 seconds.

## What each one demonstrates

**stripe.com** — what a good result looks like. Entity Trust is 100 because the identity is declared
in a form a machine can corroborate. The two findings are real and small: the page renders two
identical `<h1>` elements (a responsive desktop/mobile pair), and three large images carry no text
equivalent. Both proactive recommendations fire, including one that notices ten specification-shaped
facts sitting in prose rather than in a table.

**bundesregierung.de** — generalization. The page declares `lang="de"`, so the three checks that use
English vocabulary resolve to `unknown` rather than `fail`, and two of the four proactive detectors
stay silent. The gap shows up as reduced coverage (0.85), never as a wall of false positives. The
structural checks still work: the only JSON-LD on the page is a `BreadcrumbList`, so the report
correctly says identity is declared through Open Graph alone.

**craigslist.org** — an average page, and an honest one. No `lang` attribute at all, so the language
gate closes for a second, different reason. Findings are ordinary and fixable: a title nine
characters over the limit, no `main` landmark, no `og:type`, and no identity links anywhere.

**spacejam.com/1996** — the low end, measured rather than mocked. Twelve findings across every
category: no structured data, no headings, no meta description, no viewport, a nine-character title.
Note that `access` still scores well; the page is perfectly reachable, it simply carries almost
nothing a machine can use.

**berkshirehathaway.com** — an unrequested encoding, handled. The request advertises only
`gzip, deflate`, yet this server answers in Brotli, even when asked for no compression at all.
Brotli is a pinned dependency, so the response is decoded before any analysis and all five pages
are read. Every one of the eleven findings was checked against the decoded page: no headings, no
meta description, no viewport, no Open Graph tags, no structured data, no landmarks and no identity
links. Entity Trust scores 6.0 because nothing on the page says who the company is in a form a
machine can read.

An encoding the HTTP stack still cannot undo, or a body that claims an encoding it does not
contain, is refused rather than read. The audit then records `content_encoding` as a fetch error
and resolves the affected checks to `unknown`, so undecodable bytes never reach an analyzer.

## Known limitations these runs illustrate

- **A live site is not a fixture.** Repeated runs of craigslist scored 69 three times out of five,
  and 74 and 79 on the runs where the render timed out or fell back. The determinism guarantee is
  that one crawl artifact always produces one report, and that still holds; a page that rewrites
  itself while being read is a different matter, and `render_nav_state` discloses it.
- **The fact-image check is a statistical signal.** On stripe it named
  `platform-graphic-background_2x.png`, a decorative asset whose retina suffix supplies the digit the
  heuristic looks for. The finding stays `partial`, `heuristic`, and names the file, so a reader can
  dismiss it in seconds. Measured across these sites, ten of thirteen images on bundesregierung.de
  matched the same filename rule and produced no finding at all, because they carry real alt text.
