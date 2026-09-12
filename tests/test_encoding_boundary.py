"""Non-ASCII pages must survive every stream boundary in the pipeline.

Reported as "the audit crashes on a Hindi site". It is not a language problem, and treating it as
one would have produced a patch for Devanagari and left the defect in place. The cause is an
encoding mismatch at the subprocess boundary, and it bites on any page carrying a character outside
the parent's locale encoding — Hindi, Greek, Arabic, Japanese, or a French accent.

    the child WRITES utf-8          (we set PYTHONIOENCODING in its environment)
    the parent READ it as cp1252    (`text=True` uses locale.getpreferredencoding on Windows)

Two failure modes follow, and the quiet one is worse:

  * SILENT CORRUPTION on every non-ASCII byte sequence cp1252 happens to accept — "café" arrives as
    "cafÃ©". The audit completes and the evidence in the report is wrong.
  * A CRASH when a byte lands on one of the five cp1252 has no mapping for: 0x81, 0x8d, 0x8f, 0x90,
    0x9d. `subprocess`'s reader thread raises, the exception is swallowed, `stdout` stays None, and
    `len(None)` takes the whole audit down. U+090D — an ordinary Devanagari letter — encodes to
    e0 a4 8d, which is why a Hindi government site triggered it.

These tests drive real subprocesses and real streams, because that is the only place the actual
encodings are involved. A mock would have agreed with the broken code.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import run_audit as RA  # noqa: E402

# One character from each of several scripts, plus the specific one that crashed the audit.
SAMPLES = {
    "devanagari_candra_e": "ऍ",      # e0 a4 8d — contains a byte cp1252 cannot map
    "devanagari_word": "राजभाषा विभाग",
    "greek": "Ελληνικά",
    "arabic": "العربية",
    "japanese": "日本語のページ",
    "latin_accents": "café — naïve Ünter",
    "emoji": "📊 growth",
}


def child_printing(payload: str) -> str:
    return textwrap.dedent(f"""
        import json, sys
        json.dump({{"evidence": {payload!r}}}, sys.stdout, ensure_ascii=False)
    """)


# =================================================================================================
# The subprocess boundary
# =================================================================================================
@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_a_child_writing_any_script_is_read_back_intact(name):
    """Round-trip through a real pipe, using the orchestrator's own decoding settings."""
    payload = SAMPLES[name]
    proc = subprocess.run([sys.executable, "-c", child_printing(payload)],
                          capture_output=True, cwd=str(REPO_ROOT),
                          env=RA._analyzer_env(), **RA.CHILD_TEXT)
    assert proc.stdout is not None, "the reader thread died; stdout was never assigned"
    assert json.loads(proc.stdout)["evidence"] == payload


def test_the_orchestrator_decodes_child_output_as_utf8_not_the_platform_locale():
    """Pins the fix itself. `text=True` alone means locale.getpreferredencoding, which is cp1252 on
    a default Windows install while we tell the child to write utf-8."""
    assert RA.CHILD_TEXT.get("encoding") == "utf-8"
    assert RA.CHILD_TEXT.get("errors") == "replace"


def test_the_platform_default_really_would_have_broken_it():
    """The premise, asserted rather than assumed.

    If this ever stops holding — Python 3.15 makes utf-8 the default text encoding — the test says
    so instead of the fix quietly becoming decoration.
    """
    proc = subprocess.run([sys.executable, "-c", child_printing(SAMPLES["devanagari_candra_e"])],
                          capture_output=True, text=True, cwd=str(REPO_ROOT),
                          env=RA._analyzer_env())
    import locale
    if locale.getpreferredencoding(False).lower().replace("-", "") == "utf8":
        pytest.skip("platform default is already utf-8; the mismatch cannot arise here")
    assert proc.stdout is None or "ऍ" not in proc.stdout, (
        "the platform default no longer corrupts this; re-examine whether the fix is still needed")


def test_no_call_site_relies_on_the_platform_encoding():
    """Both subprocess calls must go through the shared settings, or one of them regresses alone."""
    source = (SCRIPTS / "run_audit.py").read_text(encoding="utf-8")
    assert source.count("**CHILD_TEXT") == 2, "a subprocess call is not using the shared decoding"
    assert "capture_output=True, text=True," not in source, "a bare text=True survived"


def test_malformed_bytes_from_a_child_degrade_rather_than_raise():
    """`errors="replace"` is load-bearing, not decoration.

    A child holding page-derived bytes can emit a broken UTF-8 sequence — a truncated multi-byte
    character at a size cap, say. Strict decoding turns that into the same swallowed reader-thread
    exception and the same None stdout that started all this. The run must lose one character, not
    the audit.
    """
    emit_broken = (
        "import sys; sys.stdout.buffer.write(b'{\"evidence\": \"' + bytes([0xff, 0xfe]) "
        "+ b'broken\"}')")
    proc = subprocess.run([sys.executable, "-c", emit_broken],
                          capture_output=True, cwd=str(REPO_ROOT),
                          env=RA._analyzer_env(), **RA.CHILD_TEXT)
    assert proc.stdout is not None, "strict decoding killed the reader thread"
    assert "broken" in proc.stdout


# =================================================================================================
# Nothing may assume the streams are strings
# =================================================================================================
def test_a_none_stdout_is_survived_rather_than_fatal(monkeypatch, tmp_path):
    """The exact shape of the reported crash: `TypeError: object of type 'NoneType' has no len()`.

    A decode failure inside `subprocess` is swallowed, so the attribute is simply never assigned.
    Any code that reaches for `len()` on it dies, and it dies AFTER the audit has done all its work.
    """
    class DeadStreams:
        returncode = 0
        stdout = None
        stderr = None

    monkeypatch.setattr(RA.subprocess, "run", lambda *a, **k: DeadStreams())
    errors = []
    artifact = tmp_path / "a.json"
    artifact.write_text("{}", encoding="utf-8")

    assert RA.run_analyzer(*RA.ANALYZERS[0], artifact, errors, None) == []
    advice = RA.run_advisor(tmp_path, artifact, [], {}, errors, None)
    assert advice["corrective"] == [] and advice["recommendations"] == []


def test_forwarding_a_childs_non_ascii_stderr_cannot_kill_the_audit(monkeypatch):
    """Forwarding was itself a hazard: writing Devanagari to a cp1252 stderr raises."""
    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(RA.sys, "stderr", narrow)
    RA.forward_child_stderr("some-skill", "राजभाषा विभाग failed\nsecond line\n")
    narrow.flush()


def test_forwarding_handles_a_missing_stream():
    RA.forward_child_stderr("some-skill", None)
    RA.forward_child_stderr("some-skill", "")


@pytest.mark.parametrize("stage", ["analyze", "advise"])
def test_the_real_call_sites_forward_through_the_hardened_path(monkeypatch, tmp_path, stage):
    """The helper above is worth nothing while the callers keep their own copy of the old loop.

    It was written, unit-tested and then not wired in: `run_analyzer` and `run_advisor` each still
    forwarded through a bare `sys.stderr.write`, which is the exact call that raises here. The
    helper passed its own test the whole time, so a green suite said nothing about the audit.

    This drives the CALLERS rather than the helper, because that is the gap. Testing a helper
    directly can only ever prove the helper works.
    """
    class Child:
        returncode = 0
        stdout = "[]" if stage == "analyze" else "{}"
        stderr = "राजभाषा विभाग: child warning\n"

    monkeypatch.setattr(RA.subprocess, "run", lambda *a, **k: Child())
    monkeypatch.setattr(RA.sys, "stderr",
                        io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict"))
    artifact = tmp_path / "a.json"
    artifact.write_text("{}", encoding="utf-8")

    if stage == "analyze":
        RA.run_analyzer(*RA.ANALYZERS[0], artifact, [], None)
    else:
        RA.run_advisor(tmp_path, artifact, [], {}, [], None)


def test_no_caller_keeps_its_own_copy_of_the_forwarding_loop():
    """Both callers must route through one implementation, or one of them regresses alone.

    Asserted on the source because the behavioural test above can only see the paths it drives,
    and a third subprocess call added later would reintroduce the defect unseen.
    """
    source = (SCRIPTS / "run_audit.py").read_text(encoding="utf-8")
    assert source.count("forward_child_stderr(skill, proc.stderr)") == 2, (
        "a call site is not forwarding through the hardened path")
    assert "sys.stderr.write(f\"[{skill}]" not in source, (
        "a caller still writes a child's text straight to a stream that may not encode it")


# =================================================================================================
# Writing the report
# =================================================================================================
def test_a_non_ascii_report_can_be_written_to_a_narrow_console():
    """An audit that ran perfectly must not die while printing its own result."""
    narrow = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    RA.write_report({"site": "राजभाषा.भारत", "summary": {"verdict": "日本語"}}, narrow)
    narrow.flush()


def test_the_written_report_is_always_valid_json_and_lossless():
    """Whichever path it takes — reconfigured to utf-8, or escaped — the value must round-trip."""
    payload = {"site": "राजभाषा", "note": "café 日本語 📊"}
    for encoding in ("cp1252", "ascii", "utf-8"):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding=encoding, errors="strict")
        RA.write_report(payload, stream)
        stream.flush()
        raw = buffer.getvalue().decode(stream.encoding, errors="strict")
        assert json.loads(raw) == payload, f"lost data through a {encoding} stream"


def test_a_stream_that_cannot_be_reconfigured_still_works():
    class Stubborn(io.StringIO):
        encoding = "cp1252"

        def reconfigure(self, **kwargs):
            raise ValueError("cannot reconfigure")

    stream = Stubborn()
    RA.write_report({"site": "राजभाषा"}, stream)
    assert json.loads(stream.getvalue()) == {"site": "राजभाषा"}


# =================================================================================================
# End to end, through the real entrypoint
# =================================================================================================
def test_a_page_in_a_non_latin_script_audits_without_crashing(tmp_path):
    """The whole point. A real subprocess fan-out over a page whose evidence is Devanagari —
    including U+090D, the character that produced the original TypeError."""
    page = tmp_path / "hindi.html"
    page.write_text(
        "<!DOCTYPE html><html lang='hi'><head><meta charset='utf-8'>"
        "<title>राजभाषा विभाग — गृह मंत्रालय ऍ</title>"
        "<meta name='description' content='राजभाषा विभाग की आधिकारिक वेबसाइट ऍ'></head>"
        "<body><header><h1>राजभाषा विभाग ऍ</h1><p>हिन्दी में जानकारी।</p></header>"
        "<main><p>यह एक परीक्षण पृष्ठ है।</p></main></body></html>",
        encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_audit.py"), "--html-file", str(page)],
        capture_output=True, cwd=str(REPO_ROOT), timeout=300, encoding="utf-8", errors="replace")

    assert proc.returncode == 0, proc.stderr[-3000:]
    report = json.loads(proc.stdout)
    assert "NoneType" not in proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr[-2000:]
    assert report["diagnostics"]["errors"] == [], report["diagnostics"]["errors"]
    assert report["partial"] is False


def test_the_entrypoint_writes_a_non_ascii_report_to_a_narrow_console(tmp_path):
    """Through `main()`, not by calling the writer directly.

    The first version of this suite only exercised `write_report` in isolation, so a mutation that
    bypassed it entirely in `main()` passed every test. `PYTHONIOENCODING` makes the child's own
    stdout cp1252, which is what a default Windows console gives you.
    """
    page = tmp_path / "greek.html"
    page.write_text(
        "<!DOCTYPE html><html lang='el'><head><meta charset='utf-8'>"
        "<title>Ελληνική σελίδα δοκιμής</title></head>"
        "<body><header><h1>Ελληνικά</h1><p>Κείμενο δοκιμής για τη σελίδα.</p></header>"
        "<main><p>Περισσότερο κείμενο ώστε η σελίδα να μην είναι κενή.</p></main></body></html>",
        encoding="utf-8")

    import os
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_audit.py"), "--html-file", str(page)],
        capture_output=True, cwd=str(REPO_ROOT), timeout=300, env=env)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    assert b"UnicodeEncodeError" not in proc.stderr, "the audit died while printing its own result"
    report = json.loads(proc.stdout.decode("utf-8", "replace"))
    assert report["site"] == "greek.html"


def test_non_latin_evidence_survives_into_the_report_uncorrupted(tmp_path):
    """Not merely "it did not crash". The corruption mode is silent, so the TEXT has to be checked:
    a mojibake report is a wrong report that looks like a working one."""
    title = "राजभाषा विभाग"
    page = tmp_path / "hindi2.html"
    # Deliberately a SUBSTANTIAL page. The first version of this test used a two-word body, which
    # failed the render check and suppressed every downstream check, so the title was never
    # measured and the assertion failed for a reason that had nothing to do with encoding.
    page.write_text(
        f"<!DOCTYPE html><html lang='hi'><head><meta charset='utf-8'><title>{title}</title>"
        "<meta name='description' content='राजभाषा विभाग की आधिकारिक वेबसाइट'></head>"
        f"<body><header><h1>{title}</h1><p>हिन्दी में जानकारी।</p></header>"
        "<main><p>यह एक परीक्षण पृष्ठ है। इसमें पर्याप्त सामग्री है ताकि पृष्ठ खाली न लगे।</p>"
        "<ul><li>पहला बिंदु</li><li>दूसरा बिंदु</li></ul></main></body></html>",
        encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_audit.py"), "--html-file", str(page)],
        capture_output=True, cwd=str(REPO_ROOT), timeout=300, encoding="utf-8", errors="replace")
    report = json.loads(proc.stdout)

    blob = json.dumps(report, ensure_ascii=False)
    assert title in blob, "the page's own title never survived into the report"
    assert "à¤" not in blob, "evidence arrived mojibake — decoded as cp1252 somewhere"
