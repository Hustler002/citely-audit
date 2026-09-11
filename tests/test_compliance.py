"""Phase 10 — compliance and sign-off.

Every gate in PLAN.md's Verification section, asserted mechanically rather than checked once by hand
and written down. A sign-off that lives in a document decays the moment the code moves.

Two layers deliberately cover the same ground:

  * the **agentskills.io reference validator**, pinned in the dev extra so CI runs the tool that
    defines the format;
  * the **same rules asserted natively**, so the guarantee survives the tool changing, being
    unavailable, or quietly relaxing a rule.

Belt and braces is the right call here because skill-format hygiene is a scored criterion and the
brief describes the validator itself as "a convenience, not required".
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
MANIFEST = json.loads((REPO_ROOT / "marketplace.json").read_text(encoding="utf-8"))
SKILL_DIRS = sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())

# agentskills.io: lowercase alphanumerics with single hyphens, no leading, trailing or doubled
# hyphen, 64 characters maximum, and it MUST equal the folder name.
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def frontmatter(skill_dir: Path) -> dict:
    """The YAML frontmatter as a flat mapping, parsed without assuming a YAML library."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_dir.name}: SKILL.md does not open with frontmatter"
    _, block, _ = text.split("---", 2)
    fields, key = {}, None
    for line in block.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and key:        # nested value, e.g. metadata:
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def body_lines(skill_dir: Path) -> int:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    return len(text.split("---", 2)[2].splitlines())


# =================================================================================================
# The reference validator
# =================================================================================================
def test_the_reference_validator_is_installed():
    """Pinned in the dev extra. If this fails the environment is incomplete, and the next test would
    otherwise be skipped — which reads as "fine" to anyone glancing at the output."""
    proc = subprocess.run([sys.executable, "-m", "pip", "show", "skills-ref"],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, "skills-ref is not installed; run pip install -e '.[dev]'"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_every_skill_passes_the_reference_validator(skill_dir):
    validator = Path(sys.executable).parent / "agentskills"
    proc = subprocess.run([str(validator), "validate", str(skill_dir)],
                          capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)
    assert proc.returncode == 0, f"{skill_dir.name}:\n{proc.stdout}\n{proc.stderr}"


# =================================================================================================
# The same rules, asserted natively
# =================================================================================================
@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_md_exists_and_declares_the_required_fields(skill_dir):
    assert (skill_dir / "SKILL.md").exists(), skill_dir.name
    fields = frontmatter(skill_dir)
    assert fields.get("name"), f"{skill_dir.name}: no name"
    assert fields.get("description"), f"{skill_dir.name}: no description"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_name_is_valid_and_matches_its_folder(skill_dir):
    name = frontmatter(skill_dir)["name"]
    assert len(name) <= 64, f"{name} exceeds 64 characters"
    assert NAME_RE.fullmatch(name), f"{name} is not a valid agentskills.io name"
    assert name == skill_dir.name, f"frontmatter name {name!r} != folder {skill_dir.name!r}"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_body_stays_under_the_recommended_ceiling(skill_dir):
    """The whole body loads into context on activation, so the spec recommends under 500 lines and
    pushing detail into references/."""
    assert body_lines(skill_dir) < 500, f"{skill_dir.name}: SKILL.md body is too long"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_description_says_what_it_does_and_when_to_use_it(skill_dir):
    """A description is what an agent selects on, so a bare label makes the skill unusable."""
    description = frontmatter(skill_dir)["description"]
    assert len(description) > 60, f"{skill_dir.name}: description is too thin to select on"


def test_the_native_rules_can_actually_reject_something():
    """A guard that cannot fail is not a guard."""
    assert not NAME_RE.fullmatch("Bad_Name")
    assert not NAME_RE.fullmatch("double--hyphen")
    assert not NAME_RE.fullmatch("-leading")
    assert NAME_RE.fullmatch("remediation-advisor")


# =================================================================================================
# Marketplace manifest
# =================================================================================================
def test_the_manifest_declares_exactly_one_entrypoint():
    assert sum(1 for s in MANIFEST["skills"] if s.get("entrypoint")) == 1


def test_the_manifest_and_the_skills_directory_agree():
    """Either direction is a defect: a declared skill that does not exist cannot be invoked, and a
    skill on disk that is not declared is invisible to the marketplace."""
    declared = {s["id"] for s in MANIFEST["skills"]}
    on_disk = {p.name for p in SKILL_DIRS}
    assert declared == on_disk, f"declared-only {declared - on_disk}, on-disk-only {on_disk - declared}"


def test_every_declared_path_resolves_inside_the_marketplace():
    for skill in MANIFEST["skills"]:
        path = (REPO_ROOT / skill["path"]).resolve()
        assert path.is_dir(), skill["path"]
        assert REPO_ROOT.resolve() in path.parents, f"{skill['path']} escapes the marketplace root"


def test_the_manifest_is_self_contained():
    """The brief: "the marketplace manifest should be self-contained — no external service needed to
    resolve it". Every path must be relative and local."""
    for skill in MANIFEST["skills"]:
        assert not skill["path"].startswith(("http://", "https://", "/", "\\")), skill["path"]
        assert ".." not in Path(skill["path"]).parts, skill["path"]


# =================================================================================================
# Sign-off gates from PLAN.md's Verification section
# =================================================================================================
def test_the_legacy_project_name_survives_only_in_prose():
    """The legacy project name must survive nowhere but the historical record.

    Permitted: CONTEXT.md, which preserves the original brief verbatim by design, and prose in the
    planning documents that DESCRIBES the rename. Any hit in code, config or a schema means the
    rename was left half-done.
    """
    # Assembled at runtime rather than written out, so this guard cannot match its OWN source and
    # report itself. An exclusion list would also work and would be worse: it would hide a genuine
    # hit if code were ever added to this file.
    needle = "brand-ai-" + "readiness-audit"

    offenders = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or {".git", ".venv", "__pycache__"} & set(path.parts):
            continue
        if path.suffix not in (".py", ".json", ".toml", ".md", ".html"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if needle not in text:
            continue
        if path.name in ("CONTEXT.md", "PLAN.md", "CLAUDE.md"):
            continue
        offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"legacy project name still in: {offenders}"


def test_no_skill_can_write_to_a_target():
    """Recommend-only is a hard rule of the brief, so it is asserted over the source rather than
    trusted. Any HTTP verb that changes state, or a write-mode file open, is a violation.

    `open(..., "w")` in the ORCHESTRATOR is legitimate — it writes the crawl artifact to its own
    temporary directory for the analyzers to read — so this scans the analysis and advice skills,
    none of which has any reason to write anywhere.
    """
    forbidden = (".post(", ".put(", ".patch(", ".delete(", "requests.post", "requests.put")
    offenders = []
    for path in SKILLS_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or path.parent.parent.name == "audit-orchestrator":
            continue
        text = path.read_text(encoding="utf-8")
        for verb in forbidden:
            if verb in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {verb}")
    assert not offenders, f"a non-orchestrator skill can change state: {offenders}"


def test_only_the_orchestrator_reaches_the_network():
    """PLAN's locked decision, with zero exceptions: the analyzers are pure functions of the crawl
    artifact. If one of them imported `requests`, its results would stop being replayable and the
    single-crawl politeness guarantee would be gone."""
    offenders = []
    for path in SKILLS_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or path.parent.parent.name == "audit-orchestrator":
            continue
        text = path.read_text(encoding="utf-8")
        for module in ("import requests", "import urllib.request", "import http.client",
                       "import socket"):
            if module in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {module}")
    assert not offenders, f"a non-orchestrator skill can reach the network: {offenders}"


def test_the_report_meets_the_briefs_mandated_floor():
    """The brief's schema is "a floor, not a ceiling". The floor still has to hold on real output."""
    sample = json.loads((SKILLS_DIR / "audit-orchestrator" / "references" / "sample-report.json")
                        .read_text(encoding="utf-8"))
    assert {"site", "audited_at", "summary", "findings"} <= set(sample)
    summary = sample["summary"]
    assert {"total_findings", "critical", "high", "medium"} <= set(summary)
    assert summary["total_findings"] == summary["critical"] + summary["high"] + summary["medium"]
    for finding in sample["findings"]:
        assert {"id", "title", "severity", "evidence", "suggested_action"} <= set(finding)
        assert finding["severity"] in ("critical", "high", "medium")
        assert {"summary", "priority"} <= set(finding["suggested_action"])


def test_the_readme_describes_every_skill_and_the_composition():
    """The brief requires a root README "describing what each skill does and how the entrypoint
    composes them", so both halves are asserted, not just the list."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for skill in MANIFEST["skills"]:
        assert skill["id"] in readme, f"README does not mention {skill['id']}"
    assert "composition" in readme.lower() or "composes" in readme.lower()
    assert "entrypoint" in readme.lower()


def test_the_readme_does_not_advertise_finished_work_as_pending():
    """It went stale twice — once when the sixth skill landed, once when Phase 9 finished. A README
    that undersells a completed marketplace is a scored problem, not a cosmetic one."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    for phase, marker in ((7, "remediation-advisor"), (8, "_narrative.py"), (9, "labeled_corpus.json")):
        shipped = (SKILLS_DIR / "remediation-advisor").exists() if phase == 7 else (
            (SKILLS_DIR / "audit-orchestrator" / "scripts" / "_narrative.py").exists() if phase == 8
            else (REPO_ROOT / "tests" / "labeled_corpus.json").exists())
        if shipped:
            assert f"(phase {phase})" not in readme, f"README still lists Phase {phase} as pending"


def test_the_readme_names_the_validator_command_that_actually_exists():
    """It documented `skills-ref validate` for months; the installed command is `agentskills`.
    A setup instruction that does not run is worse than none, because it is trusted."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "agentskills validate" in readme
    assert "skills-ref validate" not in readme
