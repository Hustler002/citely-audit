"""stdout carries the report and nothing else — enforced, not merely intended.

Every skill in this project writes machine-readable JSON to stdout: the orchestrator writes the
report, each analyzer writes its check states. A single stray character in front of that JSON
breaks a CI job that pipes it.

The convention was already right — every `basicConfig` call named `stream=sys.stderr` — but it was
only a convention, and one with a specific hole underneath it:

    `logging.basicConfig` is a SILENT NO-OP when the root logger already has a handler.

So a dependency that configures logging at import time keeps its handler AND its stream, and our
call changes nothing. Probed rather than assumed: with `basicConfig(stream=sys.stdout)` called
first, our records land on STDOUT and our format is discarded. `force=True` closes that. A
`print()` in a dependency is not logging at all and carries no stream to correct, so the
orchestrator additionally holds stdout shut for the whole audit.

These tests assert the property end-to-end through a real subprocess, because that is the only
place the actual file descriptors are involved.
"""
from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_audit as RA  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
LOG_PREFIXES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

ANALYZERS = {
    "crawl-render-extraction-audit": "crawl_render_extract.py",
    "entity-corroboration-audit": "entity_corroboration.py",
    "quotability-density-audit": "quotability_density.py",
    "engagement-orientation-audit": "engagement_orientation.py",
}


def analyzer_path(skill):
    return REPO_ROOT / "skills" / skill / "scripts" / ANALYZERS[skill]


def run(argv, **kw):
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                          cwd=str(REPO_ROOT), timeout=180, **kw)


# =================================================================================================
# The property itself, through real pipes
# =================================================================================================
def test_orchestrator_stdout_is_nothing_but_the_report():
    proc = run([str(SCRIPTS / "run_audit.py"), "--html-file",
                str(FIXTURES / "deep_chrome_page.html")])
    report = json.loads(proc.stdout)          # raises if a single byte precedes the JSON
    assert proc.stdout.startswith("{")
    assert report["summary"]["discoverability_score"] >= 0


@pytest.mark.parametrize("skill", sorted(ANALYZERS))
def test_analyzer_stdout_is_nothing_but_check_states(skill):
    proc = run([str(analyzer_path(skill)), "--html-file", str(FIXTURES / "healthy_page.html")])
    rows = json.loads(proc.stdout)
    assert isinstance(rows, list) and rows


@pytest.mark.parametrize("skill", sorted(ANALYZERS))
def test_analyzer_logs_go_to_stderr(skill):
    """Not just "stdout is clean" — the logs must actually be somewhere, not discarded."""
    proc = run([str(analyzer_path(skill)), "--artifact", "does-not-exist.json"])
    assert not proc.stdout.startswith(LOG_PREFIXES)
    assert any(line.startswith(LOG_PREFIXES) for line in proc.stderr.splitlines())


def test_no_source_file_writes_to_stdout_directly():
    """`print()` defaults to stdout, so it has no place in a skill that emits JSON there.

    Parsed rather than grepped. A text scan flagged the word "print()" inside this project's own
    docstrings, which is the classic way a guard like this gets deleted for crying wolf. The AST
    sees calls, and a deliberate `print(..., file=sys.stderr)` is fine.
    """
    offenders = []
    for path in (REPO_ROOT / "skills").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
                continue
            if any(kw.arg == "file" for kw in node.keywords):
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, ("print() to stdout in a skill that emits JSON there:\n"
                           + "\n".join(offenders))


def test_the_print_guard_can_actually_see_a_print():
    """A guard that cannot fail is not a guard. Proves the AST walk detects what it claims to."""
    tree = ast.parse("import sys\nprint('bad')\nprint('ok', file=sys.stderr)\n")
    bare = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
            and not any(kw.arg == "file" for kw in n.keywords)]
    assert len(bare) == 1


# =================================================================================================
# The hole underneath the convention
# =================================================================================================
LIBRARY_HIJACK = textwrap.dedent("""
    import logging, sys
    # A dependency configuring logging at import time. Plenty do, and this one picks stdout.
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    sys.path.insert(0, {scripts!r})
    import {module} as M
    M.configure_logging()
    logging.getLogger("probe").warning("must-not-reach-stdout")
""")


def test_plain_basicconfig_really_is_a_no_op_when_pre_empted():
    """Pins the premise the fix rests on. If a future Python made `basicConfig` reconfigure by
    default, `force=True` would stop being load-bearing and this test would say so."""
    script = textwrap.dedent("""
        import logging, sys
        logging.basicConfig(stream=sys.stdout, level=logging.INFO)
        logging.basicConfig(stream=sys.stderr, level=logging.INFO)   # no force= — the old call
        logging.getLogger("probe").warning("where-am-i")
    """)
    proc = run(["-c", script])
    assert "where-am-i" in proc.stdout, "premise changed: basicConfig now reconfigures by default"


@pytest.mark.parametrize("module", ["run_audit", "crawl_render_extract", "entity_corroboration",
                                    "quotability_density", "engagement_orientation"])
def test_configure_logging_wins_against_a_library_that_grabbed_stdout_first(module):
    skill_dirs = [str(SCRIPTS)] + [str(REPO_ROOT / "skills" / s / "scripts") for s in ANALYZERS]
    script = LIBRARY_HIJACK.format(scripts=skill_dirs[0] if module == "run_audit" else
                                   str(analyzer_path(next(s for s in ANALYZERS
                                                          if ANALYZERS[s][:-3] == module)).parent),
                                   module=module)
    proc = run(["-c", script])
    assert "must-not-reach-stdout" not in proc.stdout
    assert "must-not-reach-stdout" in proc.stderr


def _scripts_defining_configure_logging():
    """Discovered from the tree, never listed by hand.

    This was a hardcoded list of five modules under a docstring promising it covered EVERY skill.
    Adding a sixth skill would have left the promise false and the new skill unguarded — the same
    way `test_embedded_thresholds_match_registry` silently missed both Phase-5 analyzers while this
    project's notes claimed drift was caught by tests. Globbing cannot go stale.
    """
    found = []
    for path in sorted((REPO_ROOT / "skills").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if "def configure_logging" in path.read_text(encoding="utf-8"):
            found.append(path)
    return found


def test_the_logging_discovery_actually_finds_every_skill():
    """A guard whose input set is empty, or short, silently guards nothing."""
    found = _scripts_defining_configure_logging()
    skills = {p.parent.parent.name for p in found}
    declared = {s["name"] for s in json.loads(
        (REPO_ROOT / "marketplace.json").read_text(encoding="utf-8"))["skills"]}
    assert skills == declared, f"skills without a configure_logging: {declared - skills}"


@pytest.mark.parametrize("path", _scripts_defining_configure_logging(),
                         ids=lambda p: p.parent.parent.name)
def test_every_skill_configures_logging_with_force(path):
    """The duplicated helper is deliberate — skills are self-contained and never import each other
    — so, like the embedded thresholds, it needs a drift guard rather than trust."""
    source = path.read_text(encoding="utf-8")
    assert "force=True" in source
    assert "logging.basicConfig(stream=sys.stderr, level=level, force=True" in source


# =================================================================================================
# What logging configuration cannot fix: raw writes
# =================================================================================================
def test_a_stray_print_during_the_audit_cannot_corrupt_the_report():
    """A `print()` in a lazily-imported dependency carries no stream configuration to correct.

    Simulated by monkeypatching a stage the audit definitely calls, then running the real CLI path.
    """
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(SCRIPTS)!r})
        import run_audit as RA, _artifact as A
        real = A.detect_language
        def noisy(html):
            print("NOISE FROM A DEPENDENCY")
            return real(html)
        A.detect_language = noisy
        RA.main(["--html-file", {str(FIXTURES / "healthy_page.html")!r}])
    """)
    proc = run(["-c", script])
    json.loads(proc.stdout)                       # still parses
    assert "NOISE" not in proc.stdout
    assert "NOISE" in proc.stderr


def test_the_diversion_is_reported_rather_than_hidden():
    """Silently swallowing a leak would hide a real bug as effectively as the leak would cause one."""
    with RA.stdout_reserved_for_report() as real_stdout:
        print("leaked")
        assert sys.stdout is not real_stdout
    assert sys.stdout is real_stdout


def test_stdout_is_restored_even_when_the_audit_raises():
    before = sys.stdout
    with pytest.raises(ValueError):
        with RA.stdout_reserved_for_report():
            raise ValueError("stage exploded")
    assert sys.stdout is before


def test_report_is_written_to_the_real_stdout_not_the_proxy():
    """The guard would be worse than useless if it also diverted the report."""
    proc = run([str(SCRIPTS / "run_audit.py"), "--html-file", str(FIXTURES / "healthy_page.html")])
    assert json.loads(proc.stdout)["summary"]["discoverability_score"] == 99


# =================================================================================================
# Analyzer logs must reach the operator, not vanish
# =================================================================================================
def _stub_analyzer(tmp_path, stderr_text, stdout_text="[]"):
    """A stand-in analyzer, so the forwarding is tested by behaviour rather than by grep."""
    skill_dir = tmp_path / "stub-skill" / "scripts"
    skill_dir.mkdir(parents=True)
    script = skill_dir / "stub.py"
    script.write_text(
        "import sys\n"
        f"sys.stderr.write({stderr_text!r})\n"
        f"sys.stdout.write({stdout_text!r})\n", encoding="utf-8")
    return script


def test_analyzer_stderr_is_forwarded_not_swallowed(tmp_path, monkeypatch, capsys):
    """`capture_output=True` pipes the child's stderr, so an analyzer's logs were being discarded
    outright. They are the only diagnostic a failing analyzer produces."""
    script = _stub_analyzer(tmp_path, "WARNING stub: something looked wrong\n")
    monkeypatch.setattr(RA, "SKILLS_DIR", tmp_path)
    errors = []
    rows = RA.run_analyzer("stub-skill", "stub.py", tmp_path / "artifact.json", errors, None)
    captured = capsys.readouterr()
    assert rows == []
    assert "[stub-skill] WARNING stub: something looked wrong" in captured.err
    assert "something looked wrong" not in captured.out


def test_forwarded_analyzer_stderr_cannot_flood_the_operator(tmp_path, monkeypatch, capsys):
    """The child is the component holding page-derived text, so it gets a capped channel."""
    script = _stub_analyzer(tmp_path, "A" * (RA.MAX_FORWARDED_STDERR * 3) + "\n")
    monkeypatch.setattr(RA, "SKILLS_DIR", tmp_path)
    RA.run_analyzer("stub-skill", "stub.py", tmp_path / "artifact.json", [], None)
    captured = capsys.readouterr()
    assert "truncated" in captured.err
    assert len(captured.err) < RA.MAX_FORWARDED_STDERR * 2
    assert script.exists()


def test_forwarded_analyzer_stderr_is_capped():
    assert 0 < RA.MAX_FORWARDED_STDERR <= 100000


def test_ci_exit_code_still_works_with_the_guard_in_place():
    proc = run([str(SCRIPTS / "run_audit.py"), "--html-file",
                str(FIXTURES / "broken_page.html"), "--ci"])
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["summary"]["critical"] > 0


def test_logging_is_configured_before_anything_can_log():
    """Config-read failures log an error and exit 2; that path must already be on stderr."""
    proc = run([str(SCRIPTS / "run_audit.py"), "--html-file",
                str(FIXTURES / "healthy_page.html"), "--config", "no-such-config.json"])
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "could not read config" in proc.stderr
