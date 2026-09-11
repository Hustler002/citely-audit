#!/usr/bin/env python3
"""Precision and recall against tests/labeled_corpus.json (PLAN.md §6.3, §8).

    python tests/run_precision_recall.py            # the table, for a human
    python tests/run_precision_recall.py --json     # machine-readable
    pytest tests/test_corpus.py                     # the same numbers, as a CI gate

The rubric penalises misses and false positives equally, and they pull in opposite directions, so
measuring only one of them is worse than measuring neither — it makes a tool that never reports
anything look excellent. Both are counted here against labels written from each fixture's
construction rather than from the tool's own output.

  recall    = must_find labels actually reported        (a miss is a defect the audit failed to see)
  precision = reported findings that were not forbidden (a must_not_find hit is a false positive)

`must_not_find` is not the complement of `must_find`: it lists the checks a fixture is BUILT to
satisfy. A finding outside both lists is neither scored nor punished, because the corpus does not
claim to know every true answer for every page — claiming that is how a corpus starts lying.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
CORPUS_PATH = REPO_ROOT / "tests" / "labeled_corpus.json"

sys.path.insert(0, str(SCRIPTS))


def load_corpus(path: Path = CORPUS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def audit_fixture(name: str) -> dict:
    """One audit, in process. Analyzers still run as real subprocesses underneath."""
    import run_audit as RA
    config = json.loads((REPO_ROOT / "config" / "scoring-config.json").read_text(encoding="utf-8"))
    return RA.audit(None, str(FIXTURES / name), config)


def evaluate(name: str, labels: dict, report: dict) -> dict:
    """Score one fixture against its labels. Pure, so a test can drive it with a stub report."""
    reported = {f.get("check_id") for f in report.get("findings", []) if isinstance(f, dict)}
    must_find = list(labels.get("must_find") or [])
    must_not = list(labels.get("must_not_find") or [])

    hits = [c for c in must_find if c in reported]
    misses = [c for c in must_find if c not in reported]
    false_positives = [c for c in must_not if c in reported]

    summary = report.get("summary") or {}
    score = summary.get("discoverability_score")
    low, high = (labels.get("score_range") or [0, 100])[:2]
    in_range = score is not None and low <= score <= high

    return {
        "fixture": name,
        "archetype": labels.get("archetype", ""),
        "score": score,
        "score_range": [low, high],
        "score_in_range": in_range,
        "recall_hits": hits,
        "recall_misses": misses,
        "false_positives": false_positives,
        # Denominator is the labelled surface, not the whole registry: precision here means
        # "of the checks this page was built to satisfy, how many did we wrongly report".
        "recall": (len(hits) / len(must_find)) if must_find else None,
        "precision": ((len(must_not) - len(false_positives)) / len(must_not)) if must_not else None,
        "reported": sorted(reported),
    }


def run(corpus: dict | None = None) -> dict:
    corpus = corpus or load_corpus()
    results = []
    for name, labels in corpus["fixtures"].items():
        results.append(evaluate(name, labels, audit_fixture(name)))

    by_name = {r["fixture"]: r for r in results}
    # must_match is checked after every fixture has been audited, since it compares two of them.
    for name, labels in corpus["fixtures"].items():
        twin = labels.get("must_match")
        if not twin:
            continue
        a, b = by_name[name], by_name.get(twin)
        by_name[name]["must_match"] = twin
        by_name[name]["matches_twin"] = bool(
            b and a["score"] == b["score"] and a["reported"] == b["reported"])

    total_find = sum(len(r["recall_hits"]) + len(r["recall_misses"]) for r in results)
    total_hits = sum(len(r["recall_hits"]) for r in results)
    total_not = sum(len(r["false_positives"]) for r in results)
    total_not_labels = sum(
        len(corpus["fixtures"][r["fixture"]].get("must_not_find") or []) for r in results)

    return {
        "results": results,
        "totals": {
            "recall": (total_hits / total_find) if total_find else None,
            "precision": ((total_not_labels - total_not) / total_not_labels) if total_not_labels else None,
            "misses": total_find - total_hits,
            "false_positives": total_not,
            "out_of_range": [r["fixture"] for r in results if not r["score_in_range"]],
            "twin_mismatches": [r["fixture"] for r in results
                                if r.get("must_match") and not r.get("matches_twin")],
        },
    }


def render(outcome: dict) -> str:
    lines = [f"{'fixture':30} {'score':>6} {'range':>10} {'recall':>8} {'FP':>4}  notes", "-" * 86]
    for r in outcome["results"]:
        recall = "-" if r["recall"] is None else f"{r['recall']:.0%}"
        notes = []
        if not r["score_in_range"]:
            notes.append(f"SCORE OUT OF RANGE {r['score']}")
        if r["recall_misses"]:
            notes.append("MISSED " + ", ".join(r["recall_misses"]))
        if r["false_positives"]:
            notes.append("FALSE POSITIVE " + ", ".join(r["false_positives"]))
        if r.get("must_match") and not r.get("matches_twin"):
            notes.append(f"DIFFERS FROM {r['must_match']}")
        lines.append(f"{r['fixture'][:30]:30} {str(r['score']):>6} "
                     f"{str(r['score_range']):>10} {recall:>8} {len(r['false_positives']):>4}  "
                     + ("; ".join(notes) if notes else "ok"))

    totals = outcome["totals"]
    lines.append("-" * 86)
    recall = "-" if totals["recall"] is None else f"{totals['recall']:.1%}"
    precision = "-" if totals["precision"] is None else f"{totals['precision']:.1%}"
    lines.append(f"recall {recall}   precision {precision}   "
                 f"misses {totals['misses']}   false positives {totals['false_positives']}")
    if totals["out_of_range"]:
        lines.append("scores out of range: " + ", ".join(totals["out_of_range"]))
    if totals["twin_mismatches"]:
        lines.append("twin mismatches: " + ", ".join(totals["twin_mismatches"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the raw result object")
    args = parser.parse_args(argv)

    outcome = run()
    if args.json:
        json.dump(outcome, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render(outcome) + "\n")

    totals = outcome["totals"]
    clean = (not totals["misses"] and not totals["false_positives"]
             and not totals["out_of_range"] and not totals["twin_mismatches"])
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
